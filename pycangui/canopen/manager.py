"""CANopen master side, built on the `canopen` package.

Threads, and why:

* The bus Notifier thread delivers frames to ``canopen.Network`` (heartbeats,
  PDOs, SDO responses).  Our callbacks there only *emit signals*; Qt queues
  them to the GUI thread.
* SDO transfers block until the node answers (or time out), so they run on a
  worker thread fed by a queue.  Each job is a plain function; its result or
  exception comes back through the ``_Worker.done`` signal.
* The GUI thread only ever touches ``self.network`` to add/replace nodes and
  send NMT commands, both of which are quick.
"""

from __future__ import annotations

from typing import Any

import canopen
from canopen.nmt import NMT_COMMANDS, NMT_STATES
from canopen.objectdictionary import ODArray, ODRecord, ODVariable, datatypes, eds
from PySide6.QtCore import QObject, Signal, Slot

from pycangui.canopen import NodeIdentity, PdoConfig, PdoEntry
from pycangui.core.bus import BusManager
from pycangui.core.worker import Worker

DATATYPE_NAMES: dict[int, str] = {
    v: k for k, v in vars(datatypes).items() if isinstance(v, int) and k.isupper()
}
INTEGER_TYPES = {*datatypes.SIGNED_TYPES, *datatypes.UNSIGNED_TYPES, datatypes.BOOLEAN}


class CanopenManager(QObject):
    node_seen = Signal(int, str)  # node_id, NMT state from heartbeat
    identified = Signal(object)  # NodeIdentity
    eds_loaded = Signal(int, str, str)  # node_id, path, product name
    sdo_result = Signal(int, int, int, object, object)  # node_id, index, sub, value, error|None
    pdo_update = Signal(int, str, dict)  # node_id, pdo name, {variable name: value}
    emcy = Signal(int, str)  # node_id, description
    rpdos_read = Signal(int)  # node_id: its RPDO configuration is now known
    pdo_config = Signal(int)  # node_id: its PDO configuration changed
    dcf_progress = Signal(int, int)  # done, total (while reading or writing a DCF)
    message = Signal(str)  # for the Event Log pane

    def __init__(self, bus: BusManager) -> None:
        super().__init__()
        self._bus = bus
        self.network: canopen.Network | None = None
        self._sync_on = False
        self._worker = Worker()
        self._worker.start()
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
        if self.network is None:
            return
        for listener in self.network.listeners:
            self._bus.remove_listener(listener)
        self.network = None

    def shutdown(self) -> None:
        self._worker.stop()

    # --- callbacks on the Notifier thread: emit only -------------------------
    def _on_heartbeat(self, can_id: int, data: bytearray, _timestamp: float) -> None:
        if data:
            state = NMT_STATES.get(data[0] & 0x7F, f"0x{data[0]:02X}")
            self.node_seen.emit(can_id - 0x700, state)

    def _on_pdo(self, node_id: int, pdo_map: canopen.pdo.base.PdoMap) -> None:
        values = {var.name: var.raw for var in pdo_map}
        self.pdo_update.emit(node_id, pdo_map.name, values)

    def _on_emcy(self, node_id: int, err: canopen.emcy.EmcyError) -> None:
        self.emcy.emit(node_id, str(err))

    # --- nodes ---------------------------------------------------------------
    def node(self, node_id: int) -> canopen.RemoteNode | None:
        if self.network is None:
            return None
        return self.network.nodes.get(node_id)

    def _ensure_node(self, node_id: int) -> canopen.RemoteNode:
        node = self.node(node_id)
        if node is None:
            node = canopen.RemoteNode(node_id, None)  # empty object dictionary
            self.network.add_node(node)
            node.emcy.add_callback(lambda err, n=node_id: self._on_emcy(n, err))
        return node

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
                self.message.emit(f"Node {node_id}: identify failed ({error})")
            else:
                self.identified.emit(identity)

        self._worker.submit(job, done)

    def load_eds(self, node_id: int, path: str) -> None:
        if self.network is None:
            return

        def job() -> canopen.RemoteNode:
            return canopen.RemoteNode(node_id, path)  # parses the EDS, may raise

        def done(node: canopen.RemoteNode | None, error: str | None) -> None:
            if error or self.network is None:
                self.message.emit(f"Node {node_id}: EDS load failed ({error})")
                return
            self.network.add_node(node)  # replaces any existing node object
            node.emcy.add_callback(lambda err, n=node_id: self._on_emcy(n, err))
            self.eds_loaded.emit(
                node_id, path, node.object_dictionary.device_information.product_name or ""
            )
            self.load_pdos_from_eds(node_id)
            self.subscribe_pdos(node_id)

        self._worker.submit(job, done)

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
                self.message.emit(f"Node {node_id}: PDO configuration read failed ({error})")
            elif names:
                self.message.emit(f"Node {node_id}: decoding {', '.join(names)}")

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
            self.message.emit(f"Node {node_id}: load an EDS first")
            return

        def job() -> int:
            node.tpdo.read()
            node.rpdo.read()
            return len(self.pdo_configs(node_id))

        def done(count: int | None, error: str | None) -> None:
            if error:
                self.message.emit(f"Node {node_id}: PDO configuration read failed ({error})")
                return
            self.message.emit(f"Node {node_id}: {count} PDO(s) configured")
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
            self.message.emit(f"Node {config.node_id}: {config.direction}{config.number} unknown")
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
                self.message.emit(f"Node {config.node_id}: PDO write failed ({error})")
            else:
                self.message.emit(f"Node {config.node_id}: {text}")
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
        self.message.emit(f"Node {node_id}: {text if error is None else error}")

    # --- SYNC producer ---------------------------------------------------------
    @property
    def sync_running(self) -> bool:
        return self._sync_on

    def start_sync(self, period_s: float) -> None:
        """Transmit SYNC (COB-ID 0x80) so synchronous PDOs are exchanged."""
        self.stop_sync()
        if self.network is None:
            self.message.emit("SYNC: not connected")
            return
        self.network.sync.start(period_s)  # returns None; it keeps its own task
        self._sync_on = True
        self.message.emit(f"SYNC started at {period_s * 1000:.0f} ms")

    def stop_sync(self) -> None:
        if not self._sync_on:
            return
        try:
            self.network.sync.stop()
        except Exception:  # the bus went away first
            pass
        self._sync_on = False
        self.message.emit("SYNC stopped")

    # --- DCF (a device configuration file: an EDS plus the parameter values) ----
    def save_dcf(self, node_id: int, path: str) -> None:
        """Read every readable parameter from the node and write a DCF."""
        node = self.node(node_id)
        if node is None or not len(node.object_dictionary):
            self.message.emit(f"Node {node_id}: load an EDS first")
            return

        def job() -> tuple[int, int]:
            variables = [
                var
                for var in _all_variables(node.object_dictionary)
                if var.readable and var.index >= 0x1000 and var.data_type != datatypes.DOMAIN
            ]
            read = 0
            for i, var in enumerate(variables):
                try:
                    value = self._variable(node, var.index, var.subindex).raw
                    var.value = value
                    # canopen writes value_raw verbatim, and formats negative
                    # numbers as "0x-4D2", which its own reader then rejects.
                    # Write plain decimal for numbers so DCFs round trip.
                    if isinstance(value, int | float) and not isinstance(value, bool):
                        var.value_raw = str(value)
                    read += 1
                except Exception:  # not implemented by this node: leave it out
                    var.value = None
                    var.value_raw = None
                if i % 10 == 0:
                    self.dcf_progress.emit(i, len(variables))
            self.dcf_progress.emit(len(variables), len(variables))
            node.object_dictionary.node_id = node_id
            with open(path, "w", encoding="utf-8", newline="") as f:
                eds.export_dcf(node.object_dictionary, f)  # wants a file, not a path
            return read, len(variables)

        def done(counts: tuple[int, int] | None, error: str | None) -> None:
            if error:
                self.message.emit(f"Node {node_id}: DCF save failed ({error})")
            else:
                read, total = counts
                self.message.emit(f"Node {node_id}: DCF written, {read}/{total} parameters read")

        self._worker.submit(job, done)

    def apply_dcf(self, node_id: int, path: str) -> None:
        """Write the parameter values from a DCF into the node."""
        if self.node(node_id) is None:
            self.message.emit(f"Node {node_id}: not known")
            return
        node = self.node(node_id)

        def job() -> tuple[int, int, list[str]]:
            source = canopen.import_od(path, node_id)
            wanted = [
                var
                for var in _all_variables(source)
                if var.value is not None and var.writable and var.index >= 0x1000
            ]
            written, failures = 0, []
            for i, var in enumerate(wanted):
                try:
                    self._variable(node, var.index, var.subindex).raw = var.value
                    written += 1
                except Exception as exc:
                    failures.append(f"{var.index:04X}:{var.subindex:02X} ({exc})")
                if i % 5 == 0:
                    self.dcf_progress.emit(i, len(wanted))
            self.dcf_progress.emit(len(wanted), len(wanted))
            return written, len(wanted), failures

        def done(result: tuple[int, int, list[str]] | None, error: str | None) -> None:
            if error:
                self.message.emit(f"Node {node_id}: DCF apply failed ({error})")
                return
            written, total, failures = result
            self.message.emit(f"Node {node_id}: {written}/{total} parameters written from the DCF")
            for failure in failures[:10]:
                self.message.emit(f"  not written: {failure}")
            if len(failures) > 10:
                self.message.emit(f"  ... and {len(failures) - 10} more")
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
            self.message.emit(f"Node {node_id}: no PDO mapping in the EDS ({exc})")
            return
        self.pdo_config.emit(node_id)
        count = len(self.rpdos(node_id))
        if count:
            self.message.emit(f"Node {node_id}: {count} RPDO(s) available to transmit")
            self.rpdos_read.emit(node_id)

    def read_rpdo_config(self, node_id: int) -> None:
        """Re-read a node's RPDO mapping from the node itself over SDO."""
        node = self.node(node_id)
        if node is None or not len(node.object_dictionary):
            self.message.emit(f"Node {node_id}: load an EDS first")
            return

        def job() -> int:
            node.rpdo.read()
            return sum(1 for m in node.rpdo.map.values() if m.cob_id is not None and len(m.map))

        def done(count: int | None, error: str | None) -> None:
            if error:
                self.message.emit(f"Node {node_id}: RPDO configuration read failed ({error})")
            else:
                self.message.emit(f"Node {node_id}: {count} RPDO(s) configured")
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
