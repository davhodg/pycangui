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
from canopen.objectdictionary import ODArray, ODRecord, ODVariable, datatypes
from PySide6.QtCore import QObject, Signal, Slot

from pycangui.canopen import NodeIdentity
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
    message = Signal(str)  # for the Log pane

    def __init__(self, bus: BusManager) -> None:
        super().__init__()
        self._bus = bus
        self.network: canopen.Network | None = None
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
            self.load_rpdos_from_eds(node_id)
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

    def load_rpdos_from_eds(self, node_id: int) -> None:
        """Take the RPDO mapping from the loaded EDS -- instant, no bus traffic.

        Most nodes use the mapping their EDS declares, so this is enough to
        transmit them; ``read_rpdo_config()`` re-reads the live mapping from
        the node for the case where it was changed at run time.
        """
        node = self.node(node_id)
        if node is None:
            return
        try:
            node.rpdo.read(from_od=True)
        except Exception as exc:  # an EDS without PDO objects, or an odd one
            self.message.emit(f"Node {node_id}: no RPDO mapping in the EDS ({exc})")
            return
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
