"""XCP-on-CAN master.  Command/response runs on a worker thread (each command
waits for the slave's reply on the shared bus); results come back as signals.

Measurement polling reads each selected parameter with SHORT_UPLOAD on a timer
and pushes the physical value into the signal hub, so XCP values plot alongside
CANopen / DBC / J1939 signals.
"""

from __future__ import annotations

import queue
import struct
import threading

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from pycangui.core.bus import BusManager, Frame
from pycangui.core.hooks import Hooks
from pycangui.core.signals import SignalHub
from pycangui.xcp import (
    CMD_CONNECT,
    CMD_DISCONNECT,
    CMD_DOWNLOAD,
    CMD_GET_SEED,
    CMD_SET_MTA,
    CMD_SHORT_UPLOAD,
    CMD_UNLOCK,
    ERROR_CODES,
    PID_ERR,
    PID_RES,
    ConnectInfo,
    decode_value,
    encode_value,
)
from pycangui.xcp.a2l import A2l, Parameter


class XcpError(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(ERROR_CODES.get(code, f"0x{code:02X}"))
        self.code = code


class XcpManager(QObject):
    result = Signal(str)
    connected = Signal(bool)
    a2l_loaded = Signal(int)  # number of parameters
    value = Signal(str, float)  # parameter name, physical value

    def __init__(self, bus: BusManager, hooks: Hooks, signals: SignalHub) -> None:
        super().__init__()
        self._bus = bus
        self._hooks = hooks
        self._signals = signals
        self.a2l: A2l | None = None
        self.info: ConnectInfo | None = None
        self.cmd_id = 0x7A0  # master -> slave (XCP ids are project specific)
        self.res_id = 0x7A1  # slave -> master
        self.extended_id = False
        self._resp: queue.Queue = queue.Queue()
        self._connected = False
        self._polled: dict[str, Parameter] = {}
        self._worker: threading.Thread | None = None
        self._jobs: queue.Queue = queue.Queue()
        self._poll_timer = QTimer(self, interval=100, timeout=self._poll)
        bus.frames.connect(self._on_frames)
        bus.disconnected.connect(lambda: self.set_connected(False))
        self._start_worker()

    # --- worker (serialises blocking command/response) ---------------------------
    def _start_worker(self) -> None:
        self._worker = threading.Thread(target=self._run, name="xcp", daemon=True)
        self._worker.start()

    def _run(self) -> None:
        while (job := self._jobs.get()) is not None:
            fn, label = job
            try:
                text = fn()
                if text:
                    self.result.emit(text)
            except XcpError as exc:
                self.result.emit(f"{label}: {exc}")
            except TimeoutError:
                self.result.emit(f"{label}: timeout")
            except Exception as exc:  # report any failure, keep the worker alive
                self.result.emit(f"{label}: {type(exc).__name__}: {exc}")

    def _submit(self, label: str, fn) -> None:
        self._jobs.put((fn, label))

    def shutdown(self) -> None:
        self._poll_timer.stop()
        self._jobs.put(None)
        if self._worker is not None:
            self._worker.join(2.0)

    # --- low level -----------------------------------------------------------------
    @Slot(list)
    def _on_frames(self, frames: list[Frame]) -> None:
        for f in frames:
            if f.rx and f.can_id == self.res_id and f.extended == self.extended_id:
                self._resp.put(f.data)

    def _command(self, pid: int, payload: bytes = b"", timeout: float = 1.0) -> bytes:
        while not self._resp.empty():  # drop stale
            self._resp.get_nowait()
        self._bus.send(self.cmd_id, bytes([pid]) + payload, extended=self.extended_id)
        try:
            data = self._resp.get(timeout=timeout)
        except queue.Empty as exc:
            raise TimeoutError from exc
        if not data:
            raise TimeoutError
        if data[0] == PID_ERR:
            raise XcpError(data[1] if len(data) > 1 else 0x31)
        if data[0] != PID_RES:
            raise XcpError(0x31)
        return bytes(data[1:])

    # --- connection ----------------------------------------------------------------
    def set_ids(self, cmd_id: int, res_id: int, extended: bool) -> None:
        self.cmd_id, self.res_id, self.extended_id = cmd_id, res_id, extended

    def connect_slave(self) -> None:
        def fn() -> str:
            r = self._command(CMD_CONNECT, bytes([0x00]))
            # CONNECT response: resource, commModeBasic, maxCTO, maxDTO(2), protoVer, transVer
            resource = r[0]
            comm = r[1]
            max_cto = r[2]
            big_endian = bool(comm & 0x01)
            max_dto = struct.unpack(">H" if big_endian else "<H", r[3:5])[0]
            self.info = ConnectInfo(resource, 0, big_endian, max_cto, max_dto)
            self.set_connected(True)
            names = self.info.resource_names(resource)
            order = "big-endian" if big_endian else "little-endian"
            return f"XCP connected: resources {names}, maxCTO {max_cto}, maxDTO {max_dto}, {order}"

        self._submit("CONNECT", fn)

    def disconnect_slave(self) -> None:
        def fn() -> str:
            if self._connected:
                self._command(CMD_DISCONNECT)
            self.set_connected(False)
            return "XCP disconnected"

        self._submit("DISCONNECT", fn)

    def set_connected(self, on: bool) -> None:
        if on == self._connected:
            return
        self._connected = on
        if not on:
            self._poll_timer.stop()
            self._polled.clear()
            self.info = None
        self.connected.emit(on)

    @property
    def is_connected(self) -> bool:
        return self._connected

    def unlock(self, resource: int) -> None:
        def fn() -> str:
            seed = self._command(CMD_GET_SEED, bytes([0x00, resource]))
            seed_len = seed[0]
            seed_bytes = seed[1 : 1 + seed_len]
            key = self._hooks.call("xcp", "compute_key", resource, bytes(seed_bytes))
            if key is None:
                return "XCP unlock: no key algorithm (implement hooks/xcp.py::compute_key)"
            self._command(CMD_UNLOCK, bytes([len(key), *key]))
            return f"XCP resource 0x{resource:02X} unlocked"

        self._submit("UNLOCK", fn)

    # --- A2L -----------------------------------------------------------------------
    def load_a2l(self, path: str) -> None:
        self.a2l = A2l.load(path)
        self.a2l_loaded.emit(len(self.a2l.parameters))
        self.result.emit(
            f"A2L loaded: {len(self.a2l.measurements())} measurements, "
            f"{len(self.a2l.characteristics())} characteristics"
        )

    # --- read / write --------------------------------------------------------------
    def _read_raw(self, param: Parameter) -> float:
        size = {
            "UBYTE": 1,
            "SBYTE": 1,
            "UWORD": 2,
            "SWORD": 2,
            "ULONG": 4,
            "SLONG": 4,
            "A_UINT64": 8,
            "A_INT64": 8,
            "FLOAT32_IEEE": 4,
            "FLOAT64_IEEE": 8,
        }[param.datatype]
        big = self.info.big_endian if self.info else False
        addr = struct.pack(">I" if big else "<I", param.address)
        data = self._command(CMD_SHORT_UPLOAD, bytes([size, 0x00, 0x00]) + addr)
        return decode_value(data, param.datatype, big)

    def read(self, name: str) -> None:
        param = self.a2l.parameters.get(name) if self.a2l else None
        if param is None:
            self.result.emit(f"read {name}: unknown parameter")
            return

        def fn() -> str:
            raw = self._read_raw(param)
            phys = param.conversion.to_phys(raw) if param.conversion else raw
            self.value.emit(name, phys)
            unit = f" {param.unit}" if param.unit else ""
            return f"{name} = {phys:g}{unit}"

        self._submit(f"read {name}", fn)

    def write(self, name: str, text: str) -> None:
        param = self.a2l.parameters.get(name) if self.a2l else None
        if param is None or not param.writable:
            self.result.emit(f"write {name}: not a writable characteristic")
            return

        def fn() -> str:
            phys = float(text)
            raw = param.conversion.to_raw(phys) if param.conversion else phys
            big = self.info.big_endian if self.info else False
            data = encode_value(raw, param.datatype, big)
            addr = struct.pack(">I" if big else "<I", param.address)
            self._command(CMD_SET_MTA, bytes([0x00, 0x00, 0x00]) + addr)
            self._command(CMD_DOWNLOAD, bytes([len(data)]) + data)
            return f"{name} <- {phys:g}"

        self._submit(f"write {name}", fn)

    # --- polling -------------------------------------------------------------------
    def set_polled(self, name: str, on: bool) -> None:
        param = self.a2l.parameters.get(name) if self.a2l else None
        if param is None:
            return
        if on:
            self._polled[name] = param
            if self._connected and not self._poll_timer.isActive():
                self._poll_timer.start()
        else:
            self._polled.pop(name, None)
            if not self._polled:
                self._poll_timer.stop()

    def _poll(self) -> None:
        if not self._connected or not self._polled:
            return
        # One batch of reads per tick, queued on the worker; values feed the hub.
        for name, param in list(self._polled.items()):

            def fn(p=param, n=name) -> str:
                raw = self._read_raw(p)
                phys = p.conversion.to_phys(raw) if p.conversion else raw
                self.value.emit(n, phys)
                self._signals.push("XCP", n, self._bus.now(), phys, p.unit)
                return ""

            self._submit(f"poll {name}", fn)

    # --- trace labelling -----------------------------------------------------------
    def classify(self, frame: Frame) -> str | None:
        if frame.extended != self.extended_id:
            return None
        if frame.can_id == self.cmd_id:
            return "XCP cmd"
        if frame.can_id == self.res_id:
            return "XCP resp"
        return None
