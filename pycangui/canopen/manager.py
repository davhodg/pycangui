# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""CANopen master side, built on the `canopen` package.

Threads, and why:

* The bus Notifier thread delivers frames to ``canopen.Network`` (heartbeats,
  PDOs, SDO responses). Our callbacks there only *emit signals*; Qt queues
  them to the GUI thread.
* SDO transfers block until the node answers (or time out), so they run on a
  worker thread fed by a queue. Each job is a plain function; its result or
  exception comes back through the ``_Worker.done`` signal.
* The GUI thread only ever touches ``self.network`` to add/replace nodes and
  send NMT commands, both of which are quick.
"""

from __future__ import annotations

import time
from collections import deque
from pathlib import Path
from statistics import median
from typing import Any

import canopen
from canopen import lss as lss_module
from canopen.nmt import NMT_COMMANDS, NMT_STATES
from canopen.objectdictionary import ODArray, ODRecord, ODVariable, datatypes, eds
from PySide6.QtCore import QObject, QTimer, Signal, Slot

from pycangui.canopen import NodeIdentity, PdoConfig, PdoEntry, eds_extras, faults
from pycangui.canopen.dcf import values_from, write_dcf
from pycangui.canopen.display import Display, from_variable, with_overrides
from pycangui.canopen.emcy import Emcy
from pycangui.core.bus import BusManager
from pycangui.core.classify import predefined_labels
from pycangui.core.events import GOOD, INFORMATION, WARNING
from pycangui.core.worker import Worker

DATATYPE_NAMES: dict[int, str] = {
    v: k for k, v in vars(datatypes).items() if isinstance(v, int) and k.isupper()
}
INTEGER_TYPES = {*datatypes.SIGNED_TYPES, *datatypes.UNSIGNED_TYPES, datatypes.BOOLEAN}

#: A node is reported lost after this many heartbeats fail to arrive. CiA 301
#: leaves the consumer window to configuration; the usual choice is a small
#: multiple of the producer time, which is what we learn from the bus.
MISSED_HEARTBEATS = 3
MIN_HEARTBEAT_GAP_S = 0.01  # the shortest gap that can be a real heartbeat period
MIN_HEARTBEAT_TIMEOUT_S = 1.0

#: The state shown for a node put in the list by hand, until it is heard from.
ADDED_BY_HAND = "added by hand"

#: SDO timing as the ``canopen`` package ships it: 300 ms for each answer and
#: no second try. Settable in the CANopen pane, because a slow node or a busy
#: bus is found in the field and not at a desk.
DEFAULT_SDO_TIMEOUT_S = 0.3
DEFAULT_SDO_RETRIES = 0
#: The SDO channel CiA 301 predefines: requests to 0x600 + node, answers on
#: 0x580 + node. A node configured otherwise is given its own pair.
#: The largest 11-bit identifier. A PDO configured for 29-bit frames has
#: bit 29 set in its stored word, which the library leaves in the id: such
#: an id cannot name an 11-bit frame, so it is left out of this map.
MAX_11_BIT = 0x7FF

SDO_REQUEST_BASE = 0x600
SDO_RESPONSE_BASE = 0x580

#: How many gaps to judge the period from, and how few is too few to judge at
#: all. The median of several is used rather than the latest one: a producer
#: that stalls and then sends twice in quick succession puts one short gap
#: among the good ones, and taking the latest would read that burst as the
#: period and call a healthy node lost a moment later. A median ignores it;
#: a mean would be dragged by it.
HEARTBEAT_SAMPLES = 5
HEARTBEAT_MIN_SAMPLES = 3

#: Said after every DCF that wrote anything. A node that answers an SDO
#: write without an abort has accepted the value -- that is what the positive
#: response means, and checking it by reading it straight back only proves
#: the node can remember it until the next question. What nobody can see
#: from here is whether it *keeps* it: a value that was never stored, or
#: silently clamped on the way into the saved image, reads back perfectly
#: until the power goes off. So the honest advice is a power cycle and a
#: comparison, and pycangui says so rather than implying its own check was
#: the last word.
VERIFY_NOTE = (
    "  The node accepted those writes. To be sure they survive, store them, "
    "power-cycle node {node_id} and compare it against the DCF in the "
    "CANopen DCF compare pane."
)

#: Bit timing table 1 of CiA 305, as (index, bit rate).
LSS_BIT_TIMINGS: tuple[tuple[int, int], ...] = (
    (0, 1_000_000),
    (1, 800_000),
    (2, 500_000),
    (3, 250_000),
    (4, 125_000),
    (6, 50_000),
    (7, 20_000),
    (8, 10_000),
)


class CanopenManager(QObject):
    node_seen = Signal(int, str)  # node_id, NMT state from heartbeat
    identified = Signal(object)  # NodeIdentity
    eds_loaded = Signal(int, str, str)  # node_id, path, product name
    sdo_result = Signal(int, int, int, object, object)  # node_id, index, sub, value, error|None
    pdo_update = Signal(int, str, dict)  # node_id, pdo name, {variable name: value}
    emcy = Signal(object)  # Emcy
    node_lost = Signal(int)  # node_id: its heartbeat stopped arriving
    node_back = Signal(int)  # node_id: heartbeats resumed
    rpdos_read = Signal(int)  # node_id: its RPDO configuration is now known
    lss_result = Signal(str)  # readable outcome of an LSS operation
    lss_found = Signal(object)  # NodeIdentity discovered by LSS
    pdo_config = Signal(int)  # node_id: its PDO configuration changed
    dcf_progress = Signal(int, int)  # done, total (while reading or writing a DCF)
    access_level = Signal(int, object)  # node_id, the access level held (None: none known)
    fault_state = Signal(object)  # faults.FaultState: what a node says about itself
    #: For the Event Log pane, with how much the line matters. The level
    #: belongs at the call site: only the code that knows a read failed
    #: knows that the line is a failure rather than a note.
    message = Signal(str, str)  # text, level

    def __init__(self, bus: BusManager, hooks=None) -> None:
        super().__init__()
        self._bus = bus
        self._hooks = hooks
        #: node -> (index, sub) -> the EDS keys and comments the parser dropped.
        self._extras: dict[int, dict[tuple[int, int], dict[str, str]]] = {}
        #: node -> the EDS it was loaded from, so a DCF can be written
        #: through it rather than rebuilt from the parsed dictionary.
        self._eds_path: dict[int, str] = {}
        self.emcy_history: list[Emcy] = []
        #: node_id -> what it last said about its own faults, from read_faults.
        #: Kept because it is state rather than an event: the answer stands
        #: until somebody asks again or the node says otherwise.
        self.fault_states: dict[int, faults.FaultState] = {}
        #: node_id -> when its last heartbeat arrived, and the interval between
        #: the last two. A node is called lost after MISSED_HEARTBEATS of them.
        self.last_heartbeat: dict[int, float] = {}
        #: The same arrivals on the *bus* clock, for measuring the period.
        #: Separate because the two questions want different clocks -- see
        #: _on_heartbeat.
        self._heartbeat_stamp: dict[int, float] = {}
        #: The last few gaps per node, which the period is the median of.
        self._heartbeat_gaps: dict[int, deque[float]] = {}
        self.heartbeat_interval: dict[int, float] = {}
        #: node -> seconds, for the nodes whose timeout is set rather than worked
        #: out. Kept across a disconnect: it is configuration, not observation.
        self.heartbeat_overrides: dict[int, float] = {}
        self.lost_nodes: set[int] = set()
        #: node -> the access level it said is held, or the one a login was
        #: granted. Forgotten on disconnect: a level lasts a connection at most.
        self.access_levels: dict[int, int] = {}
        self._liveness = QTimer(self, interval=250, timeout=self._check_liveness)
        self._liveness.start()
        self.network: canopen.Network | None = None
        #: Applied to every node's SDO client as it is made; see set_sdo_timing.
        self.sdo_timeout_s = DEFAULT_SDO_TIMEOUT_S
        self.sdo_retries = DEFAULT_SDO_RETRIES
        #: node -> (request, response) COB-IDs, for the nodes whose SDO server
        #: is not on the predefined channel. See set_sdo_channels.
        self.sdo_channels: dict[int, tuple[int, int]] = {}
        #: Which id means what, worked out from the nodes that are known and
        #: thrown away whenever that changes. Built on demand rather than
        #: kept up to date, because it is read once per frame and written
        #: only when somebody finds a node or loads an EDS.
        self._labels: dict[int, str] | None = None
        self._sync_on = False
        self._worker = Worker()  # starts itself the first time it is used
        bus.connected.connect(self._on_bus_connected)
        bus.disconnected.connect(self._on_bus_disconnected)

    # --- bus lifecycle -------------------------------------------------------
    @Slot(str)
    def _on_bus_connected(self, _desc: str) -> None:
        self.network = canopen.Network(bus=self._bus.bus)
        for listener in self.network.listeners:
            self._bus.add_listener(listener)
        for node_id in range(1, 128):
            self.network.subscribe(0x700 + node_id, self._on_heartbeat)

    @Slot()
    def _on_bus_disconnected(self) -> None:
        self.forget_nodes()
        if self.network is None:
            return
        for listener in self.network.listeners:
            self._bus.remove_listener(listener)
        self.network = None

    def shutdown(self) -> None:
        self._liveness.stop()
        self.stop_sync()
        self._on_bus_disconnected()
        self._worker.stop()
        for signal, slot in (
            (self._bus.connected, self._on_bus_connected),
            (self._bus.disconnected, self._on_bus_disconnected),
        ):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass

    # --- callbacks on the Notifier thread: emit only -------------------------
    def _on_heartbeat(self, can_id: int, data: bytearray, timestamp: float) -> None:
        if not data:
            return
        node_id = can_id - 0x700
        now = time.monotonic()
        # Two clocks, deliberately. The *period* is measured from when the
        # frames arrived -- what the driver stamped them with -- and the
        # *liveness* from now, because "nothing for nine seconds" is a
        # question about the present and a bus clock may be an epoch.
        #
        # Measuring the period from this callback instead read 11 ms for a
        # 500 ms heartbeat whenever the machine was busy. The notifier hands
        # over whatever queued while the GUI was elsewhere, several at once,
        # so the gap between callbacks is the gap between two deliveries
        # rather than between two heartbeats -- which shortened the liveness
        # timeout by a factor of forty and called a healthy node lost.
        arrived = timestamp or now
        if (previous := self._heartbeat_stamp.get(node_id)) is not None:
            gap = arrived - previous
            # A producer time of 0x1017 is in milliseconds and nobody sets
            # one below about ten. A shorter gap than that is two frames
            # arriving together -- a periodic task catching up after the
            # machine stalled -- and taking it for the period would make
            # the liveness timeout far shorter than the node deserves.
            if MIN_HEARTBEAT_GAP_S < gap < 60:
                gaps = self._heartbeat_gaps.setdefault(node_id, deque(maxlen=HEARTBEAT_SAMPLES))
                gaps.append(gap)
                # Not published until there is enough to judge from. One gap
                # is an anecdote, and a wrong period is worse than none: it
                # produces a timeout, and the timeout decides whether a node
                # gets reported lost.
                if len(gaps) >= HEARTBEAT_MIN_SAMPLES:
                    self.heartbeat_interval[node_id] = median(gaps)
        self._heartbeat_stamp[node_id] = arrived
        self.last_heartbeat[node_id] = now
        state = NMT_STATES.get(data[0] & 0x7F, f"0x{data[0]:02X}")
        self.forget_labels()  # a node nobody knew of accounts for ids now
        self.node_seen.emit(node_id, state)

    def _on_pdo(self, node_id: int, pdo_map: canopen.pdo.base.PdoMap) -> None:
        values = {var.name: var.raw for var in pdo_map}
        self.pdo_update.emit(node_id, pdo_map.name, values)

    # --- liveness: the heartbeat consumer side ---------------------------------
    def set_heartbeat_timeouts(self, timeouts: dict[int, float]) -> None:
        """Nodes whose heartbeat timeout is set, in seconds, rather than worked out.

        A node taken off the list goes back to the worked-out timeout.
        """
        self.heartbeat_overrides = {
            int(node_id): float(seconds) for node_id, seconds in timeouts.items() if seconds > 0
        }

    def heartbeat_timeout(self, node_id: int) -> float:
        """How long to wait before calling a node lost.

        A timeout set for this node wins, as set: whoever set it knows
        something about the node that its heartbeats do not say. Otherwise
        the producer time from object 0x1017 when the EDS or the node has given
        us one, or the interval observed on the bus. Returns 0 until a few
        heartbeats have been seen: there is nothing to judge from yet, and a
        timeout guessed from one gap would be a node reported lost on the
        strength of an anecdote.
        """
        if (override := self.heartbeat_overrides.get(node_id)) is not None:
            return override
        interval = None
        node = self.node(node_id)
        if node is not None and 0x1017 in node.object_dictionary:
            configured = node.object_dictionary[0x1017].value
            if configured:
                interval = configured / 1000
        if interval is None:
            interval = self.heartbeat_interval.get(node_id)
        if not interval:
            return 0.0
        return max(interval * MISSED_HEARTBEATS, MIN_HEARTBEAT_TIMEOUT_S)

    def _check_liveness(self) -> None:
        now = time.monotonic()
        for node_id, last in list(self.last_heartbeat.items()):
            timeout = self.heartbeat_timeout(node_id)
            if not timeout:
                continue
            overdue = now - last > timeout
            if overdue and node_id not in self.lost_nodes:
                self.lost_nodes.add(node_id)
                self.message.emit(
                    f"Node {node_id}: heartbeat lost (nothing for {now - last:.1f} s)", WARNING
                )
                self.node_lost.emit(node_id)
            elif not overdue and node_id in self.lost_nodes:
                self.lost_nodes.discard(node_id)
                self.message.emit(f"Node {node_id}: heartbeat back", GOOD)
                self.node_back.emit(node_id)

    def forget_nodes(self) -> None:
        self.last_heartbeat.clear()
        self._heartbeat_stamp.clear()
        self._heartbeat_gaps.clear()
        self.heartbeat_interval.clear()
        self.lost_nodes.clear()
        self.access_levels.clear()
        self.fault_states.clear()

    # --- a node added by hand ----------------------------------------------------
    def add_node(self, node_id: int) -> bool:
        """Put a node in the list that nobody has heard from.

        A node appears by itself when its heartbeat arrives, which leaves out
        exactly the ones somebody most needs to reach: heartbeat switched off,
        held in pre-operational, or sitting in a bootloader. Returns whether
        it was added.
        """
        if self.network is None:
            self.message.emit(
                f"Node {node_id}: not connected, so there is nothing to add it to", WARNING
            )
            return False
        if not 1 <= node_id <= 127:
            self.message.emit(f"{node_id} is not a node id (1 to 127)", WARNING)
            return False
        self._ensure_node(node_id)
        self.forget_labels()
        self.node_seen.emit(node_id, ADDED_BY_HAND)
        return True

    # --- access levels: the maker's login, through the hooks -------------------------
    def login(self, node_id: int, level: int, password: str = "") -> None:
        """Ask a node for an access level, the way hooks/canopen.py::login says.

        CANopen has no login of its own, so the whole of it is the hook. The
        password goes to the hook and nowhere else: not into a message, not
        into the settings.
        """
        node = self._node_for_hooks(node_id)
        if node is None:
            return
        hooks = self._hooks

        def job() -> tuple[object, object]:
            granted = hooks.call("canopen", "login", node, level, password)
            held = hooks.call("canopen", "current_level", node) if granted else None
            return granted, held

        def done(result, error: str | None) -> None:
            if error:
                self.message.emit(f"Node {node_id}: login failed ({error})", WARNING)
                return
            granted, held = result
            if granted is None:
                self.message.emit(
                    f"Node {node_id}: no login for this device -- hooks/canopen.py::login "
                    "is where one is written",
                    WARNING,
                )
                return
            if not granted:
                self.access_levels.pop(node_id, None)
                self.message.emit(f"Node {node_id}: level {level} refused", WARNING)
                self.access_level.emit(node_id, None)
                return
            now = held if isinstance(held, int) and not isinstance(held, bool) else level
            self.access_levels[node_id] = now
            asked = f" (asked for {level})" if now != level else ""
            self.message.emit(f"Node {node_id}: logged in at level {now}{asked}", INFORMATION)
            self.access_level.emit(node_id, now)

        self._worker.submit(job, done)

    def read_level(self, node_id: int) -> None:
        """Ask a node which access level is held, through hooks/canopen.py::current_level."""
        node = self._node_for_hooks(node_id)
        if node is None:
            return
        hooks = self._hooks

        def done(held, error: str | None) -> None:
            if error:
                self.message.emit(
                    f"Node {node_id}: reading the access level failed ({error})", WARNING
                )
                return
            if not isinstance(held, int) or isinstance(held, bool):
                self.message.emit(
                    f"Node {node_id}: no way to read its access level -- "
                    "hooks/canopen.py::current_level is where one is written",
                    WARNING,
                )
                return
            self.access_levels[node_id] = held
            self.message.emit(f"Node {node_id}: access level {held}", INFORMATION)
            self.access_level.emit(node_id, held)

        self._worker.submit(lambda: hooks.call("canopen", "current_level", node), done)

    # --- what a node says about its own faults -------------------------------
    def read_faults(self, node_id: int, stored: bool = True) -> None:
        """Read the error register, and the two optional objects beside it.

        One trip rather than three calls, because they are read together and
        answer one question. ``stored`` is false for a refresh that only
        wants to know whether the node is faulted now: 0x1003 costs an SDO
        per entry, and a list of history does not change while nothing is
        happening.

        Nothing here assumes an object exists. 0x1001 is mandatory and the
        other two are not, so an abort against those is an answer -- "this
        node has nowhere to keep that" -- and is recorded as such rather
        than reported as a failure.
        """
        node = self.node(node_id)
        if node is None:
            self.message.emit(f"Node {node_id}: not known", WARNING)
            return

        def job() -> faults.FaultState:
            state = faults.FaultState(node_id)
            state.register, ok = self._optional(node, faults.ERROR_REGISTER)
            if not ok:
                # Mandatory in CiA 301, so a node refusing it is worth the
                # line: it is either not a CANopen node or it is in a state
                # where it will not answer anything else either.
                state.missing.add(faults.ERROR_REGISTER)
            state.manufacturer_status, ok = self._optional(node, faults.MANUFACTURER_STATUS)
            if not ok:
                state.missing.add(faults.MANUFACTURER_STATUS)
            if stored:
                self._read_stored(node, state)
            return state

        self._worker.submit(job, lambda s, e: self._faults_read(node_id, s, e))

    def _read_stored(self, node, state: faults.FaultState) -> None:
        """The node's fault list: its own way if it has one, else 0x1003.

        The hook first, because a device that keeps its faults somewhere of
        its own usually has a 0x1003 that is absent, empty or stale, and
        reading that instead would answer the question wrongly rather than
        not at all. An empty list back from the hook means "none stored",
        which is why it is checked for None rather than for emptiness.
        """
        if self._hooks is not None:
            own = self._hooks.call("canopen", "stored_errors", node)
            if own is not None:
                state.stored.extend(faults.as_errors(own))
                return
        count, ok = self._optional(node, faults.PREDEFINED_ERROR_FIELD, faults.COUNT_SUB)
        if not ok or count is None:
            state.missing.add(faults.PREDEFINED_ERROR_FIELD)
            return
        for sub in range(1, min(int(count), faults.MOST_ENTRIES) + 1):
            value, ok = self._optional(node, faults.PREDEFINED_ERROR_FIELD, sub)
            if not ok or value is None:
                break  # a node that says five and holds three is describing itself
            state.stored.append(faults.unpack(int(value)))

    @staticmethod
    def _optional(node, index: int, sub: int = 0) -> tuple[int | None, bool]:
        """Read one object, with "the node has not got it" as an answer.

        Returns the value and whether the node answered at all. Every abort
        is taken as absence: 0x06020000 is the one that means it, and a
        device that answers 0x06090011 or times out is equally a device with
        nothing to show, which is what the caller has to act on.
        """
        try:
            return int.from_bytes(node.sdo.upload(index, sub), "little"), True
        except Exception:
            return None, False

    def _faults_read(self, node_id: int, state, error: str | None) -> None:
        if error:
            self.message.emit(f"Node {node_id}: reading its faults failed ({error})", WARNING)
            return
        self.fault_states[node_id] = state
        for index in sorted(state.missing):
            self.message.emit(f"Node {node_id}: {faults.missing_text(index)}", INFORMATION)
        self.fault_state.emit(state)

    def clear_stored_errors(self, node_id: int) -> None:
        """Write 0 to 0x1003 sub 0, which is how CiA 301 says to empty it.

        The node's own history, not this tool's copy of it: the Emergencies
        list is untouched, because what pycangui saw and what the node kept
        are two different records and clearing one is not clearing the other.
        """
        node = self.node(node_id)
        if node is None:
            self.message.emit(f"Node {node_id}: not known", WARNING)
            return

        def job() -> None:
            # The hook first, for the same reason as reading: a device with
            # its own list is cleared its own way, and writing to a 0x1003
            # it may not have would be a write to the wrong place.
            if self._hooks is not None and self._hooks.call("canopen", "clear_stored_errors", node):
                return
            node.sdo.download(faults.PREDEFINED_ERROR_FIELD, faults.COUNT_SUB, faults.ZERO)

        def done(_result, error: str | None) -> None:
            if error:
                self.message.emit(
                    f"Node {node_id}: clearing its stored errors failed ({error})", WARNING
                )
                return
            self.message.emit(f"Node {node_id}: stored errors cleared", GOOD)
            self.read_faults(node_id)

        self._worker.submit(job, done)

    def _node_for_hooks(self, node_id: int):
        if self.network is None:
            self.message.emit(f"Node {node_id}: not connected", WARNING)
            return None
        if self._hooks is None:
            self.message.emit(f"Node {node_id}: no hooks are loaded", WARNING)
            return None
        return self._ensure_node(node_id)

    def _on_emcy(self, node_id: int, err: canopen.emcy.EmcyError) -> None:
        """Runs on the Notifier thread: decode and emit, nothing else."""
        data = bytes(err.data or b"")
        text = ""
        if self._hooks is not None:
            text = (
                self._hooks.call("canopen", "emcy_manufacturer", err.code, err.register, data) or ""
            )
        self.emcy.emit(
            Emcy(
                node_id=node_id,
                code=err.code,
                register=err.register,
                data=data,
                timestamp=err.timestamp,
                manufacturer_text=text,
            )
        )

    @Slot(object)
    def remember_emcy(self, emergency: Emcy) -> None:
        """Keep a history (GUI thread); the view connects this to ``emcy``."""
        self.emcy_history.append(emergency)
        del self.emcy_history[:-500]

    def clear_emcy_history(self) -> None:
        self.emcy_history.clear()

    # --- naming frames in the trace --------------------------------------------
    def classify(self, frame) -> str | None:
        """Name a frame, but only where there is a reason to think it CANopen.

        The predefined connection set claims most of the 11-bit range --
        0x180 to 0x57F is PDO, 0x580 to 0x67F is SDO -- so reading every id
        that way labels an ordinary CAN bus as a CANopen one it is not. The
        way out is to name only what belongs to a node that is actually
        known: heard from, or put in the list by hand. Until then the ids
        are just ids, and a database's own names have the field to
        themselves.

        Where an EDS says a node's PDOs are somewhere other than the
        predefined places, that is what is used: the file is a better
        authority about that node than CiA 301's defaults are.
        """
        if frame.extended or not self.nodes():
            return None
        if self._labels is None:
            self._labels = self._build_labels()
        return self._labels.get(frame.can_id)

    def known_ids(self) -> dict[int, str]:
        """Every id the known nodes account for. Empty when none are known."""
        if not self.nodes():
            return {}
        if self._labels is None:
            self._labels = self._build_labels()
        return dict(self._labels)

    def forget_labels(self) -> None:
        """The names are out of date: a node, its SDO channel or its PDOs changed."""
        self._labels = None

    def _build_labels(self) -> dict[int, str]:
        """Every id this bus's known nodes account for, and what to call it."""
        labels: dict[int, str] = {}
        for node_id in self.nodes():
            labels.update(predefined_labels(node_id))
            request, response = self.sdo_channel(node_id)
            labels[request] = f"SDO-R n{node_id}"
            labels[response] = f"SDO-T n{node_id}"
            for pdo in self.pdo_configs(node_id):
                # Where this node's PDOs actually are, which is worth having
                # over the predefined places -- but only when it is a real
                # answer. A DCF carries the configured identifier; an EDS may
                # declare the communication object and leave the value out,
                # or give it as $NODEID plus an offset, and an object with no
                # value reads as zero. Zero is the NMT id, and a PDO at
                # 0x000 is a missing value rather than a fact about the node.
                # Bit 31 of the stored word says the PDO is not in use, which
                # the library has already taken off the id; a disabled one is
                # not on the wire to be named.
                if pdo.enabled and 0 < pdo.cob_id <= MAX_11_BIT:
                    labels[pdo.cob_id] = f"{pdo.direction}{pdo.number} n{node_id}"
        # Last, because these are fixed by the standard: a node claiming one
        # of them is misconfigured, and calling the frame SYNC says more
        # about what is on the wire than calling it that node's PDO.
        labels.update({0x000: "NMT", 0x080: "SYNC", 0x100: "TIME", 0x7E4: "LSS", 0x7E5: "LSS"})
        return labels

    # --- nodes ---------------------------------------------------------------
    def node(self, node_id: int) -> canopen.RemoteNode | None:
        if self.network is None:
            return None
        return self.network.nodes.get(node_id)

    def nodes(self) -> list[int]:
        """Every node this manager knows of: heard from, or asked about."""
        seen = set(self.last_heartbeat)
        if self.network is not None:
            seen |= set(self.network.nodes)
        return sorted(seen)

    def _ensure_node(self, node_id: int) -> canopen.RemoteNode:
        node = self.node(node_id)
        if node is None:
            self._adopt(node_id, canopen.RemoteNode(node_id, None))  # empty object dictionary
            node = self.node(node_id)
        return node

    def _adopt(self, node_id: int, node: canopen.RemoteNode) -> None:
        """Put a node object on the network, the one way every one of them goes.

        Two places make node objects -- one heard from, one given an EDS, which
        replaces the first -- and a setting applied in only one of them is a
        setting that quietly stops applying when an EDS is loaded.
        """
        self.network.add_node(node)  # replaces any existing node object
        node.emcy.add_callback(lambda err, n=node_id: self._on_emcy(n, err))
        self._apply_sdo_timing(node)
        self._apply_sdo_channel(node_id, node)

    # --- SDO channel --------------------------------------------------------------
    def set_sdo_channels(self, channels: dict[int, tuple[int, int]]) -> None:
        """The nodes whose SDO server is not on the predefined channel, and where it is.

        Every node not listed goes back to 0x600 + id and 0x580 + id, so taking a
        node off the list is how its override is undone.
        """
        self.forget_labels()
        self.sdo_channels = {
            int(node_id): (int(request), int(response))
            for node_id, (request, response) in channels.items()
        }
        if self.network is not None:
            for node_id, node in list(self.network.nodes.items()):
                self._apply_sdo_channel(node_id, node)

    def sdo_channel(self, node_id: int) -> tuple[int, int]:
        """The COB-IDs this node's SDO requests go out on and its answers come back on."""
        return self.sdo_channels.get(
            node_id, (SDO_REQUEST_BASE + node_id, SDO_RESPONSE_BASE + node_id)
        )

    def _apply_sdo_channel(self, node_id: int, node) -> None:
        sdo = getattr(node, "sdo", None)
        if sdo is None:
            return
        request, response = self.sdo_channel(node_id)
        if (sdo.rx_cobid, sdo.tx_cobid) == (request, response):
            return
        # The client stays the same object, so everything holding node.sdo --
        # panes, plugins, a download in progress -- goes on working; only what
        # it sends on and listens for moves.
        if self.network is not None:
            try:
                self.network.unsubscribe(sdo.tx_cobid, sdo.on_response)
            except (KeyError, ValueError):
                pass  # it was not listening there
        sdo.rx_cobid, sdo.tx_cobid = request, response
        if self.network is not None:
            self.network.subscribe(response, sdo.on_response)

    # --- SDO timing -------------------------------------------------------------
    def set_sdo_timing(self, timeout_s: float, retries: int) -> None:
        """How long to wait for each SDO answer, and how many times to ask again.

        Applied to the nodes already known as well as to the ones still to come.
        Set on each node's client rather than on the library's class, so that
        nothing else in the process that uses ``canopen`` is changed with it.
        """
        self.sdo_timeout_s = max(0.01, float(timeout_s))
        self.sdo_retries = max(0, int(retries))
        if self.network is not None:
            for node in self.network.nodes.values():
                self._apply_sdo_timing(node)

    def _apply_sdo_timing(self, node) -> None:
        sdo = getattr(node, "sdo", None)
        if sdo is None:
            return
        sdo.RESPONSE_TIMEOUT = self.sdo_timeout_s
        sdo.MAX_RETRIES = self.sdo_retries + 1  # the library counts tries, not retries

    def identify(self, node_id: int) -> None:
        """Read 0x1000 and 0x1018 with raw SDO uploads (no EDS needed)."""
        if self.network is None:
            return
        node = self._ensure_node(node_id)

        def job() -> NodeIdentity:
            def read_u32(index: int, sub: int) -> int | None:
                try:
                    return int.from_bytes(node.sdo.upload(index, sub)[:4], "little")
                except canopen.SdoAbortedError:
                    return None  # object not implemented: allowed

            return NodeIdentity(
                node_id=node_id,
                vendor_id=read_u32(0x1018, 1),
                product_code=read_u32(0x1018, 2),
                revision=read_u32(0x1018, 3),
                serial=read_u32(0x1018, 4),
                device_type=read_u32(0x1000, 0),
            )

        def done(identity: NodeIdentity | None, error: str | None) -> None:
            if error:
                self.message.emit(f"Node {node_id}: identify failed ({error})", WARNING)
            else:
                self.identified.emit(identity)

        self._worker.submit(job, done)

    def load_eds(self, node_id: int, path: str) -> None:
        if self.network is None:
            return

        def job() -> tuple[canopen.RemoteNode, dict]:
            # The extras are read from the same file in the same breath: what
            # the parser kept and what it threw away describe one object each,
            # and letting them arrive separately is how they get out of step.
            return canopen.RemoteNode(node_id, path), eds_extras(path)

        def done(loaded: tuple | None, error: str | None) -> None:
            if error or self.network is None:
                self.message.emit(f"Node {node_id}: EDS load failed ({error})", WARNING)
                return
            node, extras = loaded
            self._extras[node_id] = extras
            self._eds_path[node_id] = path
            self._adopt(node_id, node)
            self.eds_loaded.emit(
                node_id, path, node.object_dictionary.device_information.product_name or ""
            )
            self.load_pdos_from_eds(node_id)
            self.subscribe_pdos(node_id)

        self._worker.submit(job, done)

    # --- how an object is shown ------------------------------------------------
    def extras(self, node_id: int, index: int, sub: int) -> dict[str, str]:
        """What this node's EDS said about the object that the parser dropped."""
        return self._extras.get(node_id, {}).get((index, sub), {})

    def display(self, node_id: int, index: int, sub: int) -> Display:
        """Name, unit, scaling, choices and limits for one object.

        The EDS first, through whatever the ``canopen`` package managed to
        parse, then the hook over the top of it -- because the file is very
        often silent about everything except the limits, and the hook is
        written by somebody holding the documentation.
        """
        node = self.node(node_id)
        var = None
        if node is not None:
            try:
                var = node.object_dictionary.get_variable(index, sub)
            except Exception:  # not in this EDS, or no EDS at all
                var = None
        base = from_variable(var)
        overrides = None
        if self._hooks is not None:
            overrides = self._hooks.call(
                "canopen", "object_display", index, sub, self.extras(node_id, index, sub), None
            )
        return with_overrides(base, overrides if isinstance(overrides, dict) else None)

    # --- SDO -----------------------------------------------------------------
    @staticmethod
    def _variable(node: canopen.RemoteNode, index: int, sub: int) -> canopen.sdo.SdoVariable:
        obj = node.sdo[index]
        return obj[sub] if isinstance(obj, canopen.sdo.SdoRecord | canopen.sdo.SdoArray) else obj

    def sdo_read(self, node_id: int, index: int, sub: int) -> None:
        node = self.node(node_id)
        if node is None:
            return

        def job() -> Any:
            try:
                return self._variable(node, index, sub).raw
            except KeyError:
                return node.sdo.upload(index, sub)  # not in the EDS: give bytes

        self._worker.submit(job, lambda v, e: self.sdo_result.emit(node_id, index, sub, v, e))

    def sdo_write(self, node_id: int, index: int, sub: int, text: str) -> None:
        node = self.node(node_id)
        if node is None:
            return

        def job() -> Any:
            var = self._variable(node, index, sub)
            var.raw = parse_value(text, var.od)
            return var.raw

        self._worker.submit(job, lambda v, e: self.sdo_result.emit(node_id, index, sub, v, e))

    # --- NMT -----------------------------------------------------------------
    def nmt(self, node_id: int, command: str) -> None:
        """command is a key of canopen.nmt.NMT_COMMANDS; node_id 0 = all nodes."""
        if self.network is None:
            return
        code = NMT_COMMANDS[command]
        if node_id == 0:
            self.network.nmt.send_command(code)
        else:
            self._ensure_node(node_id).nmt.send_command(code)

    # --- PDO -----------------------------------------------------------------
    def subscribe_pdos(self, node_id: int) -> None:
        """Read the node's TPDO configuration over SDO, then decode them live."""
        node = self.node(node_id)
        if node is None or not len(node.object_dictionary):
            return

        def job() -> list[str]:
            node.tpdo.read()
            names = []
            for pdo_map in node.tpdo.values():
                if pdo_map.cob_id is not None and pdo_map.enabled:
                    pdo_map.add_callback(lambda m, n=node_id: self._on_pdo(n, m))
                    names.append(pdo_map.name)
            return names

        def done(names: list[str] | None, error: str | None) -> None:
            if error:
                self.message.emit(
                    f"Node {node_id}: PDO configuration read failed ({error})", WARNING
                )
            elif names:
                self.message.emit(f"Node {node_id}: decoding {', '.join(names)}", INFORMATION)

        self._worker.submit(job, done)

    # --- PDO configuration (both directions) -----------------------------------
    def pdo_configs(self, node_id: int) -> list[PdoConfig]:
        """Every configured PDO of a node, transmit and receive."""
        node = self.node(node_id)
        if node is None:
            return []
        out: list[PdoConfig] = []
        for direction, maps in (("TPDO", node.tpdo.map), ("RPDO", node.rpdo.map)):
            for number, pdo_map in maps.items():
                if pdo_map.cob_id is None:
                    continue
                out.append(
                    PdoConfig(
                        node_id=node_id,
                        direction=direction,
                        number=number,
                        name=pdo_map.name,
                        cob_id=pdo_map.cob_id,
                        enabled=bool(pdo_map.enabled),
                        transmission_type=pdo_map.trans_type,
                        inhibit_time_us=(pdo_map.inhibit_time or 0) * 100,
                        event_timer_ms=pdo_map.event_timer,
                        entries=[PdoEntry(v.index, v.subindex, v.length, v.name) for v in pdo_map],
                    )
                )
        return out

    def read_pdo_config(self, node_id: int) -> None:
        """Read the live PDO configuration of a node from the node itself."""
        node = self.node(node_id)
        if node is None or not len(node.object_dictionary):
            self.message.emit(f"Node {node_id}: load an EDS first", WARNING)
            return

        def job() -> int:
            node.tpdo.read()
            node.rpdo.read()
            return len(self.pdo_configs(node_id))

        def done(count: int | None, error: str | None) -> None:
            if error:
                self.message.emit(
                    f"Node {node_id}: PDO configuration read failed ({error})", WARNING
                )
                return
            self.message.emit(f"Node {node_id}: {count} PDO(s) configured", INFORMATION)
            self.forget_labels()
            self.pdo_config.emit(node_id)
            self.rpdos_read.emit(node_id)

        self._worker.submit(job, done)

    def write_pdo_config(self, config: PdoConfig) -> None:
        """Write one PDO's communication and mapping parameters back to the node."""
        node = self.node(config.node_id)
        if node is None:
            return
        maps = node.tpdo.map if config.direction == "TPDO" else node.rpdo.map
        pdo_map = maps.get(config.number)
        if pdo_map is None:
            self.message.emit(
                f"Node {config.node_id}: {config.direction}{config.number} unknown", WARNING
            )
            return

        def job() -> str:
            pdo_map.cob_id = config.cob_id
            pdo_map.enabled = config.enabled
            pdo_map.trans_type = config.transmission_type
            # Many devices implement only sub-indices 1 and 2 of the communication
            # record; setting the others would make canopen write a missing entry.
            has = {sub for sub in pdo_map.com_record}
            pdo_map.inhibit_time = (
                int(config.inhibit_time_us // 100)
                if config.inhibit_time_us is not None and 3 in has
                else None
            )
            pdo_map.event_timer = config.event_timer_ms if 5 in has else None
            if 6 not in has:
                pdo_map.sync_start_value = None
            pdo_map.clear()
            for entry in config.entries:
                pdo_map.add_variable(entry.index, entry.subindex, entry.bits)
            pdo_map.save()  # writes the communication and mapping records over SDO
            return f"{config.direction}{config.number} written to node {config.node_id}"

        def done(text: str | None, error: str | None) -> None:
            if error:
                self.message.emit(f"Node {config.node_id}: PDO write failed ({error})", WARNING)
            else:
                self.message.emit(f"Node {config.node_id}: {text}", INFORMATION)
                self.forget_labels()
                self.pdo_config.emit(config.node_id)

        self._worker.submit(job, done)

    # --- saving and restoring parameters (0x1010 / 0x1011) ---------------------
    def store_parameters(self, node_id: int, subindex: int = 1) -> None:
        node = self.node(node_id)
        if node is None:
            return

        def job() -> str:
            node.store(subindex)
            return "parameters stored to non-volatile memory"

        self._worker.submit(job, lambda t, e: self._report(node_id, t, e))

    def restore_parameters(self, node_id: int, subindex: int = 1) -> None:
        node = self.node(node_id)
        if node is None:
            return

        def job() -> str:
            node.restore(subindex)
            return "default parameters restored (reset the node to apply)"

        self._worker.submit(job, lambda t, e: self._report(node_id, t, e))

    def _report(self, node_id: int, text: str | None, error: str | None) -> None:
        self.message.emit(
            f"Node {node_id}: {text if error is None else error}",
            INFORMATION if error is None else WARNING,
        )

    # --- SYNC producer ---------------------------------------------------------
    @property
    def sync_running(self) -> bool:
        return self._sync_on

    def start_sync(self, period_s: float) -> None:
        """Transmit SYNC (COB-ID 0x80) so synchronous PDOs are exchanged."""
        self.stop_sync()
        if self.network is None:
            self.message.emit("SYNC: not connected", WARNING)
            return
        self.network.sync.start(period_s)  # returns None; it keeps its own task
        self._sync_on = True
        self.message.emit(f"SYNC started at {period_s * 1000:.0f} ms", INFORMATION)

    def stop_sync(self) -> None:
        if not self._sync_on:
            return
        try:
            self.network.sync.stop()
        except Exception:  # the bus went away first
            pass
        self._sync_on = False
        self.message.emit("SYNC stopped", INFORMATION)

    # --- LSS, layer setting services (CiA 305) ----------------------------------
    # LSS configures a node's node-ID and bit rate over CAN, before it has a
    # usable node-ID. Exactly one node may be in configuration state at a time.
    def _lss(self):
        return self.network.lss if self.network is not None else None

    def _lss_job(self, label: str, fn) -> None:
        if self._lss() is None:
            self.lss_result.emit("LSS: not connected")
            return

        def job() -> str:
            return fn(self._lss())

        def done(text: str | None, error: str | None) -> None:
            self.lss_result.emit(text if error is None else f"{label}: {error}")

        self._worker.submit(job, done)

    def lss_switch_global(self, configuration: bool) -> None:
        """Put every node on the bus into configuration or waiting state.

        Only safe when a single node is connected -- otherwise several nodes
        answer at once. Use ``lss_select()`` on a populated bus.
        """

        def fn(lss) -> str:
            # the state constants live on the master object, not the module
            lss.send_switch_state_global(
                lss.CONFIGURATION_STATE if configuration else lss.WAITING_STATE
            )
            return f"LSS: all nodes switched to {'configuration' if configuration else 'waiting'}"

        self._lss_job("LSS switch global", fn)

    def lss_select(self, vendor: int, product: int, revision: int, serial: int) -> None:
        """Put one node, addressed by its 0x1018 identity, into configuration state."""

        def fn(lss) -> str:
            try:
                found = lss.send_switch_state_selective(vendor, product, revision, serial)
            except lss_module.LssError:
                found = False  # nothing answered within the response timeout
            if found:
                return (
                    f"LSS: node {vendor:08X}:{product:08X}:{revision:08X}:{serial:08X}"
                    " is in configuration state"
                )
            return "LSS: no node matched that address"

        self._lss_job("LSS select", fn)

    def lss_fast_scan(self) -> None:
        """Discover an unconfigured node's identity by binary search, and leave
        it in configuration state."""

        def fn(lss) -> str:
            found, identity = lss.fast_scan()
            if not found:
                return "LSS fastscan: no unconfigured node answered"
            vendor, product, revision, serial = identity
            self.lss_found.emit(
                NodeIdentity(
                    node_id=0,
                    vendor_id=vendor,
                    product_code=product,
                    revision=revision,
                    serial=serial,
                )
            )
            return (
                f"LSS fastscan: found {vendor:08X}:{product:08X}:{revision:08X}:{serial:08X}"
                " (now in configuration state)"
            )

        self._lss_job("LSS fastscan", fn)

    def lss_set_node_id(self, node_id: int) -> None:
        def fn(lss) -> str:
            lss.configure_node_id(node_id)
            return f"LSS: node-ID set to {node_id} (store and reset for it to take effect)"

        self._lss_job("LSS node-ID", fn)

    def lss_set_bit_timing(self, table_index: int) -> None:
        rate = dict(LSS_BIT_TIMINGS).get(table_index)

        def fn(lss) -> str:
            lss.configure_bit_timing(table_index)
            return f"LSS: bit rate set to {rate} bit/s (activate or store to apply)"

        self._lss_job("LSS bit timing", fn)

    def lss_activate_bit_timing(self, delay_ms: int = 100) -> None:
        def fn(lss) -> str:
            lss.activate_bit_timing(delay_ms)
            return (
                f"LSS: every node switches bit rate in {delay_ms} ms -- "
                "reconnect this tool at the new rate"
            )

        self._lss_job("LSS activate", fn)

    def lss_store(self) -> None:
        def fn(lss) -> str:
            lss.store_configuration()
            return "LSS: configuration stored in the node"

        self._lss_job("LSS store", fn)

    def lss_inquire(self) -> None:
        """Read back the node-ID and identity of the node in configuration state."""

        def fn(lss) -> str:
            node_id = lss.inquire_node_id()
            parts = [
                lss.inquire_lss_address(cs)
                for cs in (
                    lss_module.CS_INQUIRE_VENDOR_ID,
                    lss_module.CS_INQUIRE_PRODUCT_CODE,
                    lss_module.CS_INQUIRE_REVISION_NUMBER,
                    lss_module.CS_INQUIRE_SERIAL_NUMBER,
                )
            ]
            self.lss_found.emit(
                NodeIdentity(
                    node_id=node_id,
                    vendor_id=parts[0],
                    product_code=parts[1],
                    revision=parts[2],
                    serial=parts[3],
                )
            )
            joined = ":".join(f"{v:08X}" for v in parts)
            return f"LSS: node-ID {node_id}, address {joined}"

        self._lss_job("LSS inquire", fn)

    # --- DCF (a device configuration file: an EDS plus the parameter values) ----
    def save_dcf(self, node_id: int, path: str) -> None:
        """Read every readable parameter from the node and write a DCF."""
        node = self.node(node_id)
        if node is None or not len(node.object_dictionary):
            self.message.emit(f"Node {node_id}: load an EDS first", WARNING)
            return

        def job() -> tuple[int, int]:
            variables = [
                var
                for var in _all_variables(node.object_dictionary)
                if var.readable and var.index >= 0x1000 and var.data_type != datatypes.DOMAIN
            ]
            read = 0
            values: dict[tuple[int, int], object] = {}
            for i, var in enumerate(variables):
                try:
                    value = self._variable(node, var.index, var.subindex).raw
                    values[(var.index, var.subindex)] = value
                    var.value = value
                    read += 1
                except Exception:  # not implemented by this node: leave it out
                    var.value = None
                    var.value_raw = None
                if i % 10 == 0:
                    self.dcf_progress.emit(i, len(variables))
            self.dcf_progress.emit(len(variables), len(variables))

            source = self._eds_path.get(node_id)
            text = None
            if source:
                try:
                    text = Path(source).read_text(encoding="utf-8-sig", errors="replace")
                except OSError:
                    text = None
            if text is not None:
                # A DCF is an EDS with the values filled in, so it is written
                # by filling them in -- which keeps the comments carrying the
                # units, the scaling and the descriptions. Rebuilding it from
                # the parsed dictionary loses every one of them.
                out = write_dcf(text, values_from(values), node_id)
                Path(path).write_text(out, encoding="utf-8", newline="")
            else:
                node.object_dictionary.node_id = node_id
                for var in variables:
                    if isinstance(var.value, int | float) and not isinstance(var.value, bool):
                        var.value_raw = str(var.value)
                with open(path, "w", encoding="utf-8", newline="") as f:
                    eds.export_dcf(node.object_dictionary, f)  # wants a file, not a path
            return read, len(variables)

        def done(counts: tuple[int, int] | None, error: str | None) -> None:
            if error:
                self.message.emit(f"Node {node_id}: DCF save failed ({error})", WARNING)
            else:
                read, total = counts
                self.message.emit(
                    f"Node {node_id}: DCF written, {read}/{total} parameters read", INFORMATION
                )

        self._worker.submit(job, done)

    def read_objects(self, node_id: int, wanted, done) -> None:
        """Read a named list of objects, for comparing a node against a file.

        Named rather than discovered, and that is the point. Reading a whole
        object dictionary to compare it against a file is minutes of SDO
        traffic to answer a question about the two hundred objects the file
        actually holds -- and it needs an EDS loaded, which comparing against a
        file does not. The file says what matters; this reads that.

        An object the node will not give up is left out rather than recorded as
        a failure. A device that does not implement an object is not a device
        that disagrees about it, and the comparison says "only on one side"
        because that is what is true.

        ``done(values, error)`` is called on the GUI thread.
        """
        node = self.node(node_id)
        if node is None:
            done(None, f"Node {node_id} is not on the bus")
            return
        wanted = list(wanted)

        def job() -> dict[tuple[int, int], object]:
            values: dict[tuple[int, int], object] = {}
            for i, (index, sub) in enumerate(wanted):
                try:
                    values[(index, sub)] = self._variable(node, index, sub).raw
                except Exception:  # not implemented here: absent, not different
                    pass
                if i % 10 == 0:
                    self.dcf_progress.emit(i, len(wanted))
            self.dcf_progress.emit(len(wanted), len(wanted))
            return values

        self._worker.submit(job, done)

    def apply_dcf(self, node_id: int, path: str) -> None:
        """Write the parameter values from a DCF into the node."""
        if self.node(node_id) is None:
            self.message.emit(f"Node {node_id}: not known", WARNING)
            return
        node = self.node(node_id)

        def job() -> tuple[int, int, list[tuple[int, int, str]]]:
            source = canopen.import_od(path, node_id)
            wanted = [
                var
                for var in _all_variables(source)
                if var.value is not None and var.writable and var.index >= 0x1000
            ]
            written: int = 0
            failures: list[tuple[int, int, str]] = []
            for i, var in enumerate(wanted):
                try:
                    self._variable(node, var.index, var.subindex).raw = var.value
                    written += 1
                except Exception as exc:
                    failures.append((var.index, var.subindex, _reason(exc)))
                if i % 5 == 0:
                    self.dcf_progress.emit(i, len(wanted))
            self.dcf_progress.emit(len(wanted), len(wanted))
            return written, len(wanted), failures

        def done(
            result: tuple[int, int, list[tuple[int, int, str]]] | None, error: str | None
        ) -> None:
            if error:
                self.message.emit(f"Node {node_id}: DCF apply failed ({error})", WARNING)
                return
            written, total, failures = result
            self.message.emit(
                f"Node {node_id}: {written}/{total} parameters written from the DCF", INFORMATION
            )
            # Grouped by reason rather than listed one per line. A DCF that
            # goes wrong usually goes wrong the same way two hundred times --
            # one read-only object, or one value the node's range rejects --
            # and two hundred identical lines say it worse than one does.
            for reason, where in _by_reason(failures).items():
                shown = ", ".join(f"{index:04X}:{sub:02X}" for index, sub in where[:12])
                more = f" ... and {len(where) - 12} more" if len(where) > 12 else ""
                self.message.emit(f"  {len(where)} refused -- {reason}", WARNING)
                self.message.emit(f"    {shown}{more}", WARNING)
            if written:
                self.message.emit(VERIFY_NOTE.format(node_id=node_id), INFORMATION)
            self.read_pdo_config(node_id)

        self._worker.submit(job, done)

    # --- RPDO (the node receives these, so the tester transmits them) -----------
    def rpdos(self, node_id: int) -> list[tuple[int, str, list[str]]]:
        """Configured RPDOs of a node: (number, name, mapped variable names)."""
        node = self.node(node_id)
        if node is None:
            return []
        return [
            (number, pdo_map.name, [v.name for v in pdo_map])
            for number, pdo_map in node.rpdo.map.items()
            if pdo_map.cob_id is not None and len(pdo_map.map)
        ]

    def load_pdos_from_eds(self, node_id: int) -> None:
        """Take the PDO configuration from the loaded EDS -- instant, no traffic.

        Most nodes use the mapping their EDS declares, so this is enough to see
        and to transmit them; ``read_pdo_config()`` re-reads the live mapping
        from the node for the case where it was changed at run time.
        """
        node = self.node(node_id)
        if node is None:
            return
        try:
            node.rpdo.read(from_od=True)
            node.tpdo.read(from_od=True)
        except Exception as exc:  # an EDS without PDO objects, or an odd one
            self.message.emit(f"Node {node_id}: no PDO mapping in the EDS ({exc})", WARNING)
            return
        self.forget_labels()
        self.pdo_config.emit(node_id)
        count = len(self.rpdos(node_id))
        if count:
            self.message.emit(f"Node {node_id}: {count} RPDO(s) available to transmit", INFORMATION)
            self.rpdos_read.emit(node_id)

    def read_rpdo_config(self, node_id: int) -> None:
        """Re-read a node's RPDO mapping from the node itself over SDO."""
        node = self.node(node_id)
        if node is None or not len(node.object_dictionary):
            self.message.emit(f"Node {node_id}: load an EDS first", WARNING)
            return

        def job() -> int:
            node.rpdo.read()
            return sum(1 for m in node.rpdo.map.values() if m.cob_id is not None and len(m.map))

        def done(count: int | None, error: str | None) -> None:
            if error:
                self.message.emit(
                    f"Node {node_id}: RPDO configuration read failed ({error})", WARNING
                )
            else:
                self.message.emit(f"Node {node_id}: {count} RPDO(s) configured", INFORMATION)
                self.rpdos_read.emit(node_id)

        self._worker.submit(job, done)

    def encode_rpdo(
        self, node_id: int, number: int, values: dict[str, float]
    ) -> tuple[int, bytes] | None:
        """CAN id and data for an RPDO, from physical values of its variables."""
        node = self.node(node_id)
        if node is None:
            return None
        pdo_map = node.rpdo.map.get(number)
        if pdo_map is None or pdo_map.cob_id is None:
            return None
        for var in pdo_map:
            if var.name in values:
                try:
                    var.phys = values[var.name]
                except Exception:  # value out of range for the mapped type
                    var.raw = int(values[var.name])
        return pdo_map.cob_id, bytes(pdo_map.data)


def _reason(exc: Exception) -> str:
    """Why one SDO write did not happen, in the node's own words.

    An abort code is the answer the device gave, so it is quoted as a code
    *and* as its meaning: the code is what goes in a bug report to the maker,
    the meaning is what tells the person in front of the machine whether they
    have a read-only object or a value out of range.
    """
    if isinstance(exc, canopen.SdoAbortedError):
        described = canopen.SdoAbortedError.CODES.get(exc.code)
        return f"abort 0x{exc.code:08X}" + (f", {described}" if described else "")
    return f"{type(exc).__name__}: {exc}"


def _by_reason(failures: list[tuple[int, int, str]]) -> dict[str, list[tuple[int, int]]]:
    """Group (index, sub, reason) by reason, keeping the order they happened."""
    grouped: dict[str, list[tuple[int, int]]] = {}
    for index, sub, reason in failures:
        grouped.setdefault(reason, []).append((index, sub))
    return grouped


def _all_variables(od) -> list[ODVariable]:
    """Every ODVariable in an object dictionary, records and arrays flattened."""
    out: list[ODVariable] = []
    for index in od:
        obj = od[index]
        if isinstance(obj, ODVariable):
            out.append(obj)
        elif isinstance(obj, ODRecord | ODArray):
            out.extend(obj[sub] for sub in obj)
    return out


# --- helpers used by the view ----------------------------------------------
def parse_value(text: str, od: ODVariable) -> Any:
    """Turn user text into the type the object dictionary expects."""
    text = text.strip()
    if od.data_type in INTEGER_TYPES:
        return int(text, 0)  # accepts 0x.., 0b.., decimal
    if od.data_type in datatypes.FLOAT_TYPES:
        return float(text)
    if od.data_type in (datatypes.VISIBLE_STRING, datatypes.UNICODE_STRING):
        return text
    return bytes.fromhex(text)


def format_value(value: Any, od: ODVariable | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes | bytearray):
        return value.hex(" ").upper()
    if od is not None and isinstance(value, int) and od.data_type in datatypes.UNSIGNED_TYPES:
        if od.value_descriptions.get(value):
            return f"{value} ({od.value_descriptions[value]})"
        return f"{value} (0x{value:X})" if value > 9 else str(value)
    return str(value)


def od_entries(
    od: canopen.ObjectDictionary,
) -> list[tuple[int, int | None, ODVariable | None, str]]:
    """Flatten an object dictionary for a tree: (index, subindex, variable, name)."""
    out = []
    for index in od:
        if index < 0x1000:
            continue  # data type definitions (from [DummyUsage]), not real objects
        obj = od[index]
        if isinstance(obj, ODVariable):
            out.append((index, None, obj, obj.name))
        elif isinstance(obj, ODRecord | ODArray):
            out.append((index, None, None, obj.name))
            for sub in obj:
                out.append((index, sub, obj[sub], obj[sub].name))
    return out


def type_name(var: ODVariable | None) -> str:
    if var is None or var.data_type is None:
        return ""
    return DATATYPE_NAMES.get(var.data_type, f"0x{var.data_type:04X}")
