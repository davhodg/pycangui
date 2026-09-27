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
    CLEARING,
    DM13_HOLD,
    DM13_HOLD_S,
    DM13_START,
    DM13_STOP,
    GLOBAL,
    PGN_ACKNOWLEDGEMENT,
    PGN_COMPONENT_ID,
    PGN_DM1,
    PGN_DM2,
    PGN_DM4,
    PGN_DM5,
    PGN_DM13,
    PGN_ECU_ID,
    PGN_SOFTWARE_ID,
    REQUESTABLE,
    Name,
    build_id,
    identification,
    parse_acknowledgement,
    parse_dm1,
    parse_dm4,
    parse_dm5,
    parse_id,
    pgn_label,
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
    dm1 = Signal(int, object)  # sa, Dm1: active faults
    #: sa, Dm1: previously active faults. The same layout as DM1 and a
    #: different meaning, so a signal of its own: they used to arrive on dm1
    #: and were shown as active.
    dm2 = Signal(int, object)
    #: Whether pycangui is holding the network's broadcasts stopped (DM13).
    broadcasts_stopped = Signal(bool)
    claimed = Signal(int)  # our address after a successful claim (0xFE = lost)
    log = Signal(str)

    #: How long a node has to answer a request, from J1939-21. After it, a
    #: request nothing answered is reported as unanswered.
    RESPONSE_S = 1.25

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
        #: Repeats the DM13 hold while broadcasts are stopped: nodes start
        #: again by themselves 6 s after the last one.
        self._hold_timer = QTimer(self, interval=int(DM13_HOLD_S * 1000), timeout=self._hold)
        self._hold_to = GLOBAL
        #: What has been asked for and not yet timed out: PGN -> (who was
        #: asked, which nodes have answered, which request this is). One per
        #: PGN, the latest: asking again starts it afresh.
        self._asked: dict[int, tuple[int, set[int], int]] = {}
        self._requests_made = 0
        self.nodes: dict[int, Name | None] = {}
        self.last_seen: dict[int, float] = {}
        bus.connected.connect(self._on_bus_connected)
        bus.disconnected.connect(self._on_bus_disconnected)
        bus.frames.connect(self._on_frames)
        # Emitted on the library's thread and handled on this one, where the
        # record of what was asked lives.
        self.message.connect(self._on_answer)

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
        if self._hold_timer.isActive():
            self._hold_timer.stop()
            self.broadcasts_stopped.emit(False)
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
            if name is not None:
                self._answered(PGN_ADDRESS_CLAIM, mid.source, bytes(f.data))
            self.last_seen[mid.source] = time.monotonic()

    # --- callbacks on the ECU job thread: emit only ----------------------------
    def _on_message(self, priority: int, pgn: int, sa: int, timestamp: float, data) -> None:
        payload = bytes(data)
        self.message.emit(priority, pgn, sa, timestamp, payload)
        if pgn == PGN_DM1:
            self.dm1.emit(sa, parse_dm1(payload))
        elif pgn == PGN_DM2:
            self.dm2.emit(sa, parse_dm1(payload))

    # --- the answers to requests ----------------------------------------------------
    def request_name(self, pgn: int) -> str:
        """What a request is called: its entry in the list, else the PGN's name."""
        listed = dict(REQUESTABLE).get(pgn)
        if listed:
            return listed
        if pgn == PGN_ADDRESS_CLAIM:
            return "Address claim"
        return self.pgn_name(pgn) or pgn_label(pgn)

    @Slot(int, int, int, float, bytes)
    def _on_answer(self, _priority: int, pgn: int, sa: int, _ts: float, data: bytes) -> None:
        if pgn == PGN_ACKNOWLEDGEMENT:
            # An acknowledgement answers the request for the PGN it names:
            # the clear it carried out, or a request the node will not answer.
            said, about = parse_acknowledgement(data)
            if self._expected(about, sa):
                self._asked[about][1].add(sa)
                what = CLEARING.get(about)
                subject = f"clearing {what} faults" if what else f"{self.request_name(about)}"
                self.log.emit(f"J1939 {sa:02X}: {subject} {said}")
            return
        self._answered(pgn, sa, data)

    def _expected(self, pgn: int, sa: int) -> bool:
        """Whether this is an answer to a request still waiting, not yet had from sa."""
        asked = self._asked.get(pgn)
        if asked is None:
            return False
        destination, answered, _serial = asked
        return (destination == GLOBAL or destination == sa) and sa not in answered

    def _answered(self, pgn: int, sa: int, data: bytes) -> None:
        """Say what a node answered, once per request: a node that broadcasts
        the same PGN every second is reported for the answer, not the rest."""
        if not self._expected(pgn, sa):
            return
        self._asked[pgn][1].add(sa)
        self.log.emit(self.describe_answer(pgn, sa, data))

    def _no_answer(self, pgn: int, serial: int) -> None:
        asked = self._asked.get(pgn)
        if asked is None or asked[2] != serial:
            return  # asked again since, and that request has its own timer
        destination, answered, _serial = self._asked.pop(pgn)
        if not answered:
            who = "any node" if destination == GLOBAL else f"{destination:02X}"
            self.log.emit(f"J1939: no answer to {self.request_name(pgn)} from {who}")

    def describe_answer(self, pgn: int, sa: int, data: bytes) -> str:
        """A node's answer to a request, in words."""
        who = f"J1939 {sa:02X}"
        if pgn in (PGN_DM1, PGN_DM2):
            kind = "active" if pgn == PGN_DM1 else "previously active"
            dm = parse_dm1(data)
            title = f"{who} {self.request_name(pgn)}"
            if not dm.dtcs:
                return f"{title}: no {kind} faults"
            lamps = f", lamps {dm.lamps()}" if dm.lamps() else ""
            lines = [f"{title}: {len(dm.dtcs)} {kind} fault(s){lamps}"]
            for d in dm.dtcs:
                spn = f"SPN {d.spn} {self.spn_description(d.spn)}".rstrip()
                fmi = f"FMI {d.fmi} {self.fmi_description(d.fmi)}".rstrip()
                lines.append(f"  {spn}, {fmi}, occurred {d.occurrence}")
            return "\n".join(lines)
        if pgn == PGN_ADDRESS_CLAIM:
            return f"{who} claims its address: NAME {Name.from_bytes(data).summary()}"
        if pgn in (PGN_ECU_ID, PGN_SOFTWARE_ID, PGN_COMPONENT_ID):
            title = dict(REQUESTABLE)[pgn]
            fields = "\n".join(f"  {name}: {text}" for name, text in identification(pgn, data))
            return f"{who} {title}:\n{fields}" if fields else f"{who} {title}: empty"
        if pgn == PGN_DM5:
            r = parse_dm5(data)
            return (
                f"{who} DM5 readiness: {r.active} active, {r.previously_active} previously "
                f"active, OBD compliance {r.obd_compliance}, monitors {r.monitors.hex(' ').upper()}"
            )
        if pgn == PGN_DM4:
            frames = parse_dm4(data)
            if not frames:
                return f"{who} DM4 freeze frames: none"
            lines = [f"{who} DM4 freeze frames:"]
            for dtc, values in frames:
                name = self.spn_description(dtc.spn)
                spn = f"SPN {dtc.spn} {name}".rstrip()
                recorded = values.hex(" ").upper() or "(nothing recorded)"
                lines.append(f"  {spn} FMI {dtc.fmi} OC {dtc.occurrence}: {recorded}")
            return "\n".join(lines)
        shown = data.hex(" ").upper() or "(empty)"
        return f"{who} {self.request_name(pgn)}: {shown}"

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
        """Send a Request (PGN 59904) for *pgn*; from our claimed address or 0xFE.

        What comes back is said in the Event Log -- each node's answer,
        decoded, or that it refused -- and so is silence, once the time a node
        has to answer is up.
        """
        self._requests_made += 1
        serial = self._requests_made
        self._asked[pgn] = (destination, set(), serial)
        QTimer.singleShot(int(self.RESPONSE_S * 1000), lambda: self._no_answer(pgn, serial))
        if self.ca is not None and self.own_address is not None:
            self.ca.send_request(0, pgn, destination)
        else:
            self._bus.send(
                build_id(PGN_REQUEST, 0xFE, destination), pgn.to_bytes(3, "little"), extended=True
            )

    def request_address_claims(self) -> None:
        """Ask every node to say who it is (a request for Address Claimed)."""
        self.request_pgn(PGN_ADDRESS_CLAIM, GLOBAL)

    # --- DM13 -----------------------------------------------------------------------
    def stop_broadcasts(self, destination: int = GLOBAL) -> None:
        """Stop the broadcasts on this network, and keep them stopped until start."""
        self._hold_to = destination
        self.send_pgn(PGN_DM13, DM13_STOP, destination)
        self._hold_timer.start()
        self.log.emit(f"J1939: broadcasts stopped (DM13 to {destination:02X}), held until started")
        self.broadcasts_stopped.emit(True)

    def start_broadcasts(self) -> None:
        self._hold_timer.stop()
        self.send_pgn(PGN_DM13, DM13_START, self._hold_to)
        self.log.emit("J1939: broadcasts started again (DM13)")
        self.broadcasts_stopped.emit(False)

    @property
    def holding_broadcasts(self) -> bool:
        return self._hold_timer.isActive()

    def _hold(self) -> None:
        self.send_pgn(PGN_DM13, DM13_HOLD, self._hold_to)

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
