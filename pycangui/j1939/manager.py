# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""J1939 network side, built on the `python-can-j1939` package for transport
protocol reassembly (TP.BAM / TP.CM) and address claiming.

The ECU object is fed from the shared bus Notifier and sends through our bus.
Its callbacks run on its own job thread, so they only emit Qt signals.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import can
from PySide6.QtCore import QObject, QTimer, Signal, Slot

if TYPE_CHECKING:  # annotations only, which are strings at run time
    import j1939 as j1939lib

from pycangui.core.bus import BusManager, Frame
from pycangui.core.hooks import Hooks
from pycangui.j1939 import (
    GLOBAL,
    Name,
    build_id,
    parse_dm1,
    parse_id,
)


class _RxOnlyListener(can.Listener):
    """Passes only genuinely received frames through, dropping our own echoes."""

    def __init__(self, inner: can.Listener) -> None:
        self._inner = inner

    def on_message_received(self, msg: can.Message) -> None:
        if msg.is_rx:
            self._inner.on_message_received(msg)

    def on_error(self, exc: Exception) -> None:
        pass


PGN_ADDRESS_CLAIM = 60928
PGN_REQUEST = 59904
PGN_DM1 = 65226
PGN_DM2 = 65227


def tester_name():
    """The NAME this tool claims an address under.

    Built when it is asked for rather than at import, and every use of the
    library below is inside a method for the same reason: a session that never
    opens the J1939 pane -- which is most of them -- loads none of it.
    """
    import j1939 as j1939lib

    return j1939lib.Name(
        arbitrary_address_capable=True,
        industry_group=j1939lib.Name.IndustryGroup.Global,
        vehicle_system_instance=0,
        vehicle_system=0,
        function=129,  # off-board diagnostic service tool
        function_instance=0,
        ecu_instance=0,
        manufacturer_code=0,
        identity_number=0x1CAFE,
    )


class J1939Manager(QObject):
    message = Signal(
        int, int, int, float, bytes
    )  # priority, pgn, sa, timestamp, data (reassembled)
    node_seen = Signal(int, object)  # sa, Name | None (None = seen without a claim yet)
    dm1 = Signal(int, object)  # sa, Dm1
    claimed = Signal(int)  # our address after a successful claim (0xFE = lost)
    log = Signal(str)

    #: How long a claim may stay undecided before it is reported as failed.
    #: The library sends the claim half a second after it starts and then waits a
    #: quarter of a second for anybody to contest it, so no answer exists before
    #: about 0.75 s -- and on a busy machine its thread gets there later still.
    CLAIM_DEADLINE_S = 3.0
    #: How often to look while it is undecided.
    CLAIM_POLL_MS = 100

    def __init__(self, bus: BusManager, hooks: Hooks) -> None:
        super().__init__()
        self._bus = bus
        self._hooks = hooks
        self.ecu: j1939lib.ElectronicControlUnit | None = None
        self._rx_only: list[_RxOnlyListener] = []
        self.ca: j1939lib.ControllerApplication | None = None
        #: One timer for the claim in progress, so a claim released and made
        #: again cannot leave two of them reporting. A child of the manager,
        #: so it goes when the manager does.
        self._claim_timer = QTimer(self, interval=self.CLAIM_POLL_MS, timeout=self._check_claim)
        self._claim_deadline = 0.0
        self.nodes: dict[int, Name | None] = {}
        self.last_seen: dict[int, float] = {}
        bus.connected.connect(self._on_bus_connected)
        bus.disconnected.connect(self._on_bus_disconnected)
        bus.frames.connect(self._on_frames)

    # --- bus lifecycle ---------------------------------------------------------
    @Slot(str)
    def _on_bus_connected(self, _desc: str) -> None:
        import j1939 as j1939lib

        self.ecu = j1939lib.ElectronicControlUnit(send_message=self._send_message)
        # The bus echoes our own tx (receive_own_messages, for the trace). The
        # ECU must not see those: its own address claim echoed back looks like a
        # contender with an identical NAME and triggers an infinite re-claim storm.
        self._rx_only = [_RxOnlyListener(inner) for inner in self.ecu._listeners]
        for listener in self._rx_only:
            self._bus.add_listener(listener)
        self.ecu.subscribe(self._on_message)

    @Slot()
    def _on_bus_disconnected(self) -> None:
        self.release_address()
        if self.ecu is not None:
            for listener in self._rx_only:
                self._bus.remove_listener(listener)
            self._rx_only = []
            self.ecu.stop()
            self.ecu = None
        self.nodes.clear()
        self.last_seen.clear()

    def shutdown(self) -> None:
        self._on_bus_disconnected()

    def _send_message(self, can_id: int, extended_id: bool, data, fd_format: bool = False) -> None:
        self._bus.send(can_id, bytes(data), extended=extended_id, fd=fd_format)

    # --- raw frames on the GUI thread: node discovery -----------------------------
    # (the library consumes Address Claim internally and never forwards it, and a
    # node is worth listing from its first single frame, before any reassembly)
    @Slot(list)
    def _on_frames(self, frames: list[Frame]) -> None:
        for f in frames:
            if not f.extended or not f.rx:
                continue
            mid = parse_id(f.can_id)
            if mid.source in (0xFE, GLOBAL):
                continue
            name = None
            if mid.pgn == PGN_ADDRESS_CLAIM and len(f.data) >= 8:
                name = Name.from_bytes(f.data)
            if name is not None or mid.source not in self.nodes:
                self.nodes[mid.source] = name
                self.node_seen.emit(mid.source, name)
            self.last_seen[mid.source] = time.monotonic()

    # --- callbacks on the ECU job thread: emit only ----------------------------
    def _on_message(self, priority: int, pgn: int, sa: int, timestamp: float, data) -> None:
        payload = bytes(data)
        self.message.emit(priority, pgn, sa, timestamp, payload)
        if pgn in (PGN_DM1, PGN_DM2):
            self.dm1.emit(sa, parse_dm1(payload))

    # --- labelling ------------------------------------------------------------------
    def pgn_name(self, pgn: int) -> str:
        """The PGN's short name, from hooks/j1939.py. "" if it has none."""
        return self._hooks.call("j1939", "pgn_name", pgn) or ""

    def classify(self, frame: Frame) -> str | None:
        if not frame.extended:
            return None
        mid = parse_id(frame.can_id)
        name = self.pgn_name(mid.pgn) or f"PGN {mid.pgn}"
        dest = "" if mid.destination == GLOBAL else f"->{mid.destination:02X}"
        return f"{name} SA {mid.source:02X}{dest}"

    def spn_description(self, spn: int) -> str:
        return self._hooks.call("j1939", "spn_description", spn) or ""

    def fmi_description(self, fmi: int) -> str:
        """What the failure mode means. Standard, so this is nearly always set."""
        return self._hooks.call("j1939", "fmi_description", fmi) or ""

    # --- address claim / sending --------------------------------------------------------
    def claim_address(self, address: int) -> None:
        """Become a node on the bus (needed to send multi-packet messages and
        to receive messages addressed to us)."""
        if self.ecu is None:
            self.log.emit("J1939: not connected")
            return
        self.release_address()
        self.ca = self.ecu.add_ca(name=tester_name(), device_address=address)
        self.ca.start()
        self.log.emit(f"J1939: claiming address {address:02X}")
        # The claim resolves on the ECU thread; look until it has.
        self._claim_deadline = time.monotonic() + self.CLAIM_DEADLINE_S
        self._claim_timer.start()

    def _check_claim(self) -> None:
        """Report the claim once it has resolved, however long its thread takes.

        This used to be one look after 600 ms, which is sooner than the
        library can ever answer. It usually got away with it; a loaded machine did
        not, and reported "address in use" -- releasing the address -- for a
        claim that would have succeeded a moment later.
        """
        if self.ca is None:
            self._claim_timer.stop()
            return
        import j1939 as j1939lib

        state = self.ca.state
        if state == j1939lib.ControllerApplication.State.NORMAL:
            self._claim_timer.stop()
            self.log.emit(f"J1939: address {self.ca.device_address:02X} claimed")
            self.claimed.emit(self.ca.device_address)
        elif (
            state == j1939lib.ControllerApplication.State.CANNOT_CLAIM
            or time.monotonic() >= self._claim_deadline
        ):
            self._claim_timer.stop()
            self.log.emit("J1939: address claim failed (address in use?)")
            self.release_address()

    def release_address(self) -> None:
        self._claim_timer.stop()
        if self.ca is not None and self.ecu is not None:
            try:
                self.ca.stop()
                self.ecu.remove_ca(self.ca.device_address)
            except Exception:  # library raises if the claim never completed
                pass
            self.ca = None
            self.claimed.emit(0xFE)

    @property
    def own_address(self) -> int | None:
        import j1939 as j1939lib

        if self.ca is not None and self.ca.state == j1939lib.ControllerApplication.State.NORMAL:
            return self.ca.device_address
        return None

    def request_pgn(self, pgn: int, destination: int = GLOBAL) -> None:
        """Send a Request (PGN 59904) for *pgn*; from our claimed address or 0xFE."""
        if self.ca is not None and self.own_address is not None:
            self.ca.send_request(0, pgn, destination)
        else:
            self._bus.send(
                build_id(PGN_REQUEST, 0xFE, destination), pgn.to_bytes(3, "little"), extended=True
            )

    def send_pgn(self, pgn: int, data: bytes, destination: int = GLOBAL, priority: int = 6) -> None:
        """Single frame from 0xFE, or any length via TP when we hold an address."""
        if self.ca is not None and self.own_address is not None:
            pf = (pgn >> 8) & 0xFF
            ps = destination if pf < 240 else pgn & 0xFF
            self.ca.send_pgn((pgn >> 16) & 1, pf, ps, priority, list(data))
        elif len(data) <= 8:
            self._bus.send(build_id(pgn, 0xFE, destination, priority), data, extended=True)
        else:
            self.log.emit("J1939: claim an address first to send more than 8 bytes")
