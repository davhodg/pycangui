# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""SAE J1939 helpers that need no library: 29-bit id layout, PGN names,
DM1 parsing, the other diagnostic messages and identification, NAME decoding.
The transport (TP.BAM / TP.CM) and address claiming come from the
`python-can-j1939` package in ``manager.py``."""

from __future__ import annotations

from dataclasses import dataclass

GLOBAL = 0xFF
NULL_ADDRESS = 0xFE

PGN_ACKNOWLEDGEMENT = 59392
PGN_DM1 = 65226  # active DTCs
PGN_DM2 = 65227  # previously active DTCs
PGN_DM3 = 65228  # clear previously active DTCs
PGN_DM4 = 65229  # freeze frames
PGN_DM5 = 65230  # diagnostic readiness
PGN_DM11 = 65235  # clear active DTCs
PGN_DM13 = 57088  # stop and start broadcasts
PGN_ECU_ID = 64965
PGN_SOFTWARE_ID = 65242
PGN_COMPONENT_ID = 65259

#: What the Request PGN box offers, most asked for first. Anything else can
#: still be typed: this is a shortlist, not a limit.
REQUESTABLE = (
    (PGN_DM1, "DM1 active faults"),
    (PGN_DM2, "DM2 previously active faults"),
    (PGN_DM3, "DM3 clear previously active faults"),
    (PGN_DM4, "DM4 freeze frames"),
    (PGN_DM5, "DM5 diagnostic readiness"),
    (PGN_DM11, "DM11 clear active faults"),
    (PGN_ECU_ID, "ECU identification"),
    (PGN_SOFTWARE_ID, "Software identification"),
    (PGN_COMPONENT_ID, "Component identification"),
)
#: Requests that erase what a node holds, and so ask first.
CLEARING = {PGN_DM3: "previously active", PGN_DM11: "active"}

#: DM13 (J1939-73). The first byte gives each of four networks two bits --
#: the current data link in bits 8-7, J1939 network 1 in bits 2-1 -- where 00
#: is stop, 01 start and 11 take no action. The hold, sent at least every
#: 5 s, keeps a stop in force: nodes start again by themselves 6 s after the
#: last one.
DM13_STOP = bytes([0x3F]) + b"\xff" * 7
DM13_START = bytes([0x7F]) + b"\xff" * 7
DM13_HOLD = b"\xff\xff\xff\x0f\xff\xff\xff\xff"  # byte 4, bits 8-5: all devices
DM13_HOLD_S = 4.0


@dataclass(frozen=True, slots=True)
class MessageId:
    priority: int
    pgn: int
    source: int
    destination: int  # GLOBAL for PDU2 / broadcast

    @property
    def pdu1(self) -> bool:
        return ((self.pgn >> 8) & 0xFF) < 240


def parse_id(can_id: int) -> MessageId:
    """Split a 29-bit id into priority, PGN, SA and DA (PDU1 PS byte)."""
    priority = (can_id >> 26) & 0x7
    pf = (can_id >> 16) & 0xFF
    ps = (can_id >> 8) & 0xFF
    dp = (can_id >> 24) & 0x3  # extended data page + data page
    sa = can_id & 0xFF
    if pf < 240:
        return MessageId(priority, (dp << 16) | (pf << 8), sa, ps)
    return MessageId(priority, (dp << 16) | (pf << 8) | ps, sa, GLOBAL)


def build_id(pgn: int, source: int, destination: int = GLOBAL, priority: int = 6) -> int:
    pf = (pgn >> 8) & 0xFF
    ps = destination if pf < 240 else pgn & 0xFF
    return (priority << 26) | ((pgn >> 16 & 0x3) << 24) | (pf << 16) | (ps << 8) | source


def pgn_mask(pgn: int) -> int:
    """Bits of a 29-bit id that identify this PGN (for DBC matching)."""
    return 0x03FF0000 if ((pgn >> 8) & 0xFF) < 240 else 0x03FFFF00


def pgn_label(pgn: int) -> str:
    """A PGN with no name to go by. The names live in hooks/j1939.py."""
    return f"PGN {pgn} (0x{pgn:05X})"


# --- DM1 / DM2 -------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Dtc:
    spn: int
    fmi: int
    occurrence: int
    conversion_method: int


@dataclass(frozen=True, slots=True)
class Dm1:
    protect_lamp: int
    amber_warning_lamp: int
    red_stop_lamp: int
    malfunction_lamp: int
    dtcs: tuple[Dtc, ...]

    def lamps(self) -> str:
        names = []
        for value, short in (
            (self.malfunction_lamp, "MIL"),
            (self.red_stop_lamp, "RSL"),
            (self.amber_warning_lamp, "AWL"),
            (self.protect_lamp, "PL"),
        ):
            if value == 1:
                names.append(short)
        return " ".join(names)


def parse_dm1(data: bytes) -> Dm1:
    """DM1/DM2 payload: 2 lamp bytes then 4-byte DTCs (SPN conversion method 4)."""
    lamps = data[0] if data else 0xFF
    dtcs = []
    for i in range(2, len(data) - 3, 4):
        b0, b1, b2, b3 = data[i : i + 4]
        spn = b0 | (b1 << 8) | ((b2 >> 5) << 16)
        if spn == 0 and b2 == 0 and b3 == 0:
            continue  # padding / "no DTC" placeholder
        dtcs.append(Dtc(spn, b2 & 0x1F, b3 & 0x7F, b3 >> 7))
    return Dm1(
        protect_lamp=lamps & 0x3,
        amber_warning_lamp=(lamps >> 2) & 0x3,
        red_stop_lamp=(lamps >> 4) & 0x3,
        malfunction_lamp=(lamps >> 6) & 0x3,
        dtcs=tuple(dtcs),
    )


def encode_dm1(dtcs: list[Dtc], mil: int = 0, rsl: int = 0, awl: int = 0, pl: int = 0) -> bytes:
    out = bytearray([(mil << 6) | (rsl << 4) | (awl << 2) | pl, 0xFF])
    for d in dtcs:
        out += bytes(
            [
                d.spn & 0xFF,
                (d.spn >> 8) & 0xFF,
                ((d.spn >> 16) & 0x7) << 5 | (d.fmi & 0x1F),
                (d.conversion_method << 7) | (d.occurrence & 0x7F),
            ]
        )
    if not dtcs:
        out += bytes(4)
    return bytes(out)


# --- the answers to the other requests --------------------------------------
ACK_CONTROL = {0: "accepted", 1: "refused", 2: "access denied", 3: "cannot respond"}


def parse_acknowledgement(data: bytes) -> tuple[str, int]:
    """(what the node said, which PGN it said it about) from an Acknowledgement."""
    control = data[0] if data else 0xFF
    pgn = int.from_bytes(data[5:8], "little") if len(data) >= 8 else 0
    return ACK_CONTROL.get(control, f"control {control}"), pgn


#: The fields of each identification message, in the order they come,
#: separated by '*'. Software identification starts with a count instead.
IDENTIFICATION_FIELDS = {
    PGN_ECU_ID: ("Part number", "Serial number", "Location", "Type", "Manufacturer", "Hardware"),
    PGN_COMPONENT_ID: ("Make", "Model", "Serial number", "Unit number"),
}


def identification(pgn: int, data: bytes) -> list[tuple[str, str]]:
    """(field, text) pairs from an identification message.

    Each field is text ended by '*'; one left empty is kept, so the ones
    after it keep their names.
    """
    body = data
    if pgn == PGN_SOFTWARE_ID and data:
        body = data[1:]  # the number of fields, which the separators also give
    fields = body.decode("latin-1").split("*")
    if fields and not fields[-1].strip("\x00\xff "):
        fields.pop()  # after the last separator
    names = IDENTIFICATION_FIELDS.get(pgn, ())
    spare = "Software" if pgn == PGN_SOFTWARE_ID else "Field"

    def name(i: int) -> str:
        return names[i] if i < len(names) else f"{spare} {i + 1}"

    return [(name(i), text.strip()) for i, text in enumerate(fields)]


@dataclass(frozen=True, slots=True)
class Readiness:
    """DM5: how many faults, and which OBD rules the node is built to."""

    active: int
    previously_active: int
    obd_compliance: int
    monitors: bytes  # which monitors are supported, and done: left as they come


def parse_dm5(data: bytes) -> Readiness:
    padded = bytes(data) + b"\xff" * (8 - len(data))
    return Readiness(padded[0], padded[1], padded[2], padded[3:8])


def parse_dm4(data: bytes) -> list[tuple[Dtc, bytes]]:
    """Each freeze frame: the fault it was taken for, and what was recorded.

    A frame is its length, then the fault as DM1 gives one, then the values
    recorded when it was set -- left as bytes, since which parameters follow
    the few J1939-73 fixes is up to the maker.
    """
    frames = []
    at = 0
    while at < len(data):
        length = data[at]
        frame = data[at + 1 : at + 1 + length]
        if length < 4 or len(frame) < 4:
            break
        b0, b1, b2, b3 = frame[:4]
        spn = b0 | (b1 << 8) | ((b2 >> 5) << 16)
        frames.append((Dtc(spn, b2 & 0x1F, b3 & 0x7F, b3 >> 7), bytes(frame[4:])))
        at += 1 + length
    return frames


# --- NAME ------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Name:
    value: int

    @property
    def identity_number(self) -> int:
        return self.value & 0x1FFFFF

    @property
    def manufacturer_code(self) -> int:
        return (self.value >> 21) & 0x7FF

    @property
    def ecu_instance(self) -> int:
        return (self.value >> 32) & 0x7

    @property
    def function_instance(self) -> int:
        return (self.value >> 35) & 0x1F

    @property
    def function(self) -> int:
        return (self.value >> 40) & 0xFF

    @property
    def vehicle_system(self) -> int:
        return (self.value >> 49) & 0x7F

    @property
    def industry_group(self) -> int:
        return (self.value >> 60) & 0x7

    @property
    def arbitrary_address_capable(self) -> bool:
        return bool(self.value >> 63)

    @classmethod
    def from_bytes(cls, data: bytes) -> Name:
        return cls(int.from_bytes(data[:8], "little"))

    def summary(self) -> str:
        return (
            f"mfr {self.manufacturer_code} fn {self.function} inst {self.function_instance}"
            f" ecu {self.ecu_instance} ig {self.industry_group} id {self.identity_number}"
        )
