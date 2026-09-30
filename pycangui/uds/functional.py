# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Functional requests: one request, to every ECU on the bus at once.

Some services are about the whole bus rather than one ECU. A baud rate change
that half the bus makes is a broken bus; quietening one ECU before a flash
leaves the rest talking; a session kept alive in one ECU lets the others fall
back to their defaults. ISO 14229 sends those to the functional address, and
every ECU that serves it answers -- or, with the suppress-positive-response
bit set, only the ones with something to object to.

**Sent raw, as one frame.** ISO 15765-2 allows a functional request only as a
single frame, since nobody could send flow control for a message addressed
to everybody, and every service sent this way fits in one. So there is no
ISO-TP conversation to hold: the frame is built here and put on the bus, and
udsoncan -- whose client is bound to the one ECU the pane is open to -- is
not involved.

**Answers come from several ECUs**, each on its own identifier, and are
collected for as long as a physical request would wait: P2, stretched to P2*
for as long as any of them says 0x78, response pending. Silence is not a
failure: with the suppress bit set a positive answer is not sent at all, and
an ECU that does not serve a functional request says nothing rather than
refusing it.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import can

from pycangui.uds import CAN_DL, MAX_11_BIT, PHYSICAL_PF, UdsConfig

#: The suppress-positive-response bit, in a service's sub-function byte.
SUPPRESS = 0x80
#: NRC 0x78: the ECU has the request and will answer, later.
PENDING = 0x78
#: The services that can go to every ECU, whose second byte is a sub-function
#: and so can carry the bit. (Which of them the pane sends that way is
#: pycangui.uds.FUNCTIONAL_SERVICES; security access, DIDs, DTC reports,
#: routines and transfers stay physical -- one ECU's business, or answered
#: in more frames than a functional request can take.)
WITH_SUBFUNCTION = {0x10, 0x11, 0x28, 0x3E, 0x85, 0x87}
#: The OBD convention on 11-bit identifiers: requests to 0x7DF, answers on
#: 0x7E8 to 0x7EF, one id per ECU.
OBD_FUNCTIONAL_ID = 0x7DF
OBD_ANSWERS = range(0x7E8, 0x7F0)


def suppressed(payload: bytes) -> bytes:
    """The request with its positive answer suppressed, where it has a sub-function."""
    if len(payload) > 1 and payload[0] in WITH_SUBFUNCTION:
        return bytes([payload[0], payload[1] | SUPPRESS]) + payload[2:]
    return payload


def single_frame(payload: bytes, padding: int | None, fd: bool = False) -> bytes:
    """An ISO 15765-2 single frame carrying the whole request.

    Seven bytes on classic CAN; on FD, up to 62 with the length in a byte of
    its own. Padded out to the frame length when the pane pads.
    """
    if len(payload) <= 7:
        frame = bytes([len(payload)]) + payload
        size = 8
    elif fd and len(payload) <= 62:
        frame = bytes([0x00, len(payload)]) + payload
        size = next(n for n in CAN_DL if n >= len(frame))
    else:
        raise ValueError(
            f"{len(payload)} bytes is too long to send functionally: a functional "
            "request is one frame"
        )
    if padding is not None:
        frame += bytes([padding]) * (size - len(frame))
    return frame


def payload_of(data: bytes) -> bytes | None:
    """What a single frame carries, or None for any other kind of frame."""
    if not data or data[0] >> 4 != 0:
        return None
    length = data[0] & 0x0F
    if length:
        return bytes(data[1 : 1 + length])
    if len(data) > 1:  # FD: the length in the next byte
        return bytes(data[2 : 2 + data[1]])
    return None


def request_id(config: UdsConfig) -> tuple[int, bool]:
    """The functional identifier, and whether it is 29-bit."""
    can_id = config.functional_id
    return can_id, config.fixed or can_id > MAX_11_BIT


def answers_to(config: UdsConfig):
    """Which frames are an ECU answering, as a test on (id, 29-bit).

    On a J1939 bus every ECU answers ``18DA<tester><ecu>``, so all of them
    can be recognised. With typed identifiers only the pane's own response
    id is known -- and, where the functional id is OBD's 0x7DF, the OBD
    answers 0x7E8 to 0x7EF. An ECU answering elsewhere is not heard.
    """
    if config.fixed:
        tester = config.tester_address & 0xFF

        def fixed(can_id: int, extended: bool) -> bool:
            return (
                extended and (can_id >> 16) & 0xFF == PHYSICAL_PF and (can_id >> 8) & 0xFF == tester
            )

        return fixed
    obd = config.functional_id == OBD_FUNCTIONAL_ID

    def typed(can_id: int, extended: bool) -> bool:
        if can_id == config.rx_id and extended == config.extended_id:
            return True
        return obd and not extended and can_id in OBD_ANSWERS

    return typed


@dataclass(frozen=True)
class Answer:
    """What one ECU said to a functional request."""

    sender: int  # the identifier it answered on
    payload: bytes
    #: It began a multi-frame answer, which a functional request cannot take
    #: part in -- nobody sent it flow control.
    too_long: bool = False

    @property
    def positive(self) -> bool:
        return bool(self.payload) and self.payload[0] != 0x7F

    @property
    def nrc(self) -> int | None:
        """The negative response code, for a refusal."""
        if len(self.payload) >= 3 and self.payload[0] == 0x7F:
            return self.payload[2]
        return None

    @property
    def objected(self) -> bool:
        """It refused, or answered in a way that cannot be taken: not a yes."""
        return self.too_long or (self.nrc is not None and self.nrc != PENDING)


class _Catch(can.Listener):
    """Keeps the answers that arrive while a functional request is waiting."""

    def __init__(self, wanted) -> None:
        self._wanted = wanted
        self.lock = threading.Lock()
        self.frames: list[tuple[int, bytes]] = []

    def on_message_received(self, msg: can.Message) -> None:
        if msg.is_rx and not msg.is_error_frame:
            if self._wanted(msg.arbitration_id, msg.is_extended_id):
                with self.lock:
                    self.frames.append((msg.arbitration_id, bytes(msg.data)))


def request(
    bus,
    config: UdsConfig,
    payload: bytes,
    *,
    p2_s: float,
    p2_star_s: float,
    fd: bool = False,
    clock=time.monotonic,
    sleep=time.sleep,
) -> list[Answer]:
    """Send ``payload`` to every ECU and return what they said, each once.

    ``bus`` is what the pane is open on: something with ``bus`` (python-can's)
    and ``add_listener``/``remove_listener``. Waits P2 for answers, and P2*
    from each 0x78 for the answer it promises. An ECU's last word is the one
    kept: a pending then a positive is a positive.
    """
    can_id, extended = request_id(config)
    frame = single_frame(payload, config.padding, fd)
    catch = _Catch(answers_to(config))
    bus.add_listener(catch)
    try:
        bus.bus.send(
            can.Message(arbitration_id=can_id, data=frame, is_extended_id=extended, is_fd=fd)
        )
        deadline = clock() + p2_s
        said: dict[int, Answer] = {}
        seen = 0
        while clock() < deadline:
            sleep(0.005)
            with catch.lock:
                new, seen = catch.frames[seen:], len(catch.frames)
            for sender, data in new:
                if data and data[0] >> 4 == 1:  # a first frame: more than one frame
                    said[sender] = Answer(sender, b"", too_long=True)
                    continue
                answer = Answer(sender, payload_of(data) or b"")
                said[sender] = answer
                if answer.nrc == PENDING:
                    deadline = max(deadline, clock() + p2_star_s)
        return list(said.values())
    finally:
        bus.remove_listener(catch)


def send_only(bus, config: UdsConfig, payload: bytes, *, fd: bool = False) -> None:
    """Send a functional request and wait for nothing: tester present, a transition."""
    can_id, extended = request_id(config)
    bus.bus.send(
        can.Message(
            arbitration_id=can_id,
            data=single_frame(payload, config.padding, fd),
            is_extended_id=extended,
            is_fd=fd,
        )
    )


def describe(service: str, answers: list[Answer], quiet_positive: bool) -> str:
    """One line for the pane's log: who agreed, who refused, who said nothing."""
    agreed = [a for a in answers if a.positive]
    refused = [a for a in answers if a.nrc is not None and a.nrc != PENDING]
    long = [a for a in answers if a.too_long]
    parts = []
    if agreed:
        parts.append(f"{len(agreed)} agreed ({', '.join(f'{a.sender:X}' for a in agreed)})")
    for a in refused:
        parts.append(f"{a.sender:X} refused, NRC 0x{a.nrc:02X}")
    for a in long:
        parts.append(f"{a.sender:X} began an answer too long to take functionally")
    for a in answers:
        if a.nrc == PENDING:
            parts.append(f"{a.sender:X} said it would answer, and did not within P2*")
    if not answers:
        parts.append(
            "no ECU objected (positive answers are suppressed)"
            if quiet_positive
            else "no ECU answered"
        )
    elif quiet_positive and not refused and not long:
        parts.append("no ECU objected")
    return f"{service} (all ECUs): " + "; ".join(parts)
