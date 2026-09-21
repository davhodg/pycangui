# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""XCP pane logic: A2L handling, seed-and-key, read/write, measurement polling.

The protocol itself lives behind an :class:`~pycangui.xcp.engine.XcpEngine`
chosen from the component registry, so a different implementation (a Rust or C
library, or another transport) can be dropped in without touching this file or
the GUI. Commands run on a worker thread because they block on the slave.
"""

from __future__ import annotations

import queue
import threading

from PySide6.QtCore import QObject, QTimer, Signal

from pycangui.ccp import engine as _ccp_engine  # noqa: F401  (registers the CCP engine)
from pycangui.core import seedkey
from pycangui.core.bus import BusManager, Frame
from pycangui.core.components import COMPONENTS
from pycangui.core.context import Context
from pycangui.core.hooks import Hooks
from pycangui.core.signals import SignalHub
from pycangui.xcp import DATATYPES, decode_value, encode_value
from pycangui.xcp.a2l import A2l, Parameter
from pycangui.xcp.engine import XcpEngine, XcpError

#: An engine is named "<protocol>-<implementation>", so that the list says
#: both things it has to: which protocol it speaks, and whose code speaks
#: it. An engine calling into a Rust library would be "xcp-rust" beside
#: "xcp-builtin".
DEFAULT_COMPONENT = "xcp-builtin"


class XcpManager(QObject):
    result = Signal(str)
    connected = Signal(bool)
    a2l_loaded = Signal(int)  # number of parameters
    value = Signal(str, float)  # parameter name, physical value

    def __init__(self, bus: BusManager, hooks: Hooks, signals: SignalHub, ctx: Context) -> None:
        super().__init__()
        self._bus = bus
        self._hooks = hooks
        self._signals = signals
        self._ctx = ctx
        self.a2l: A2l | None = None
        self.engine: XcpEngine | None = None
        self.component_name = str(ctx.settings.get("components.xcp", DEFAULT_COMPONENT))
        self._connected = False
        self._polled: dict[str, Parameter] = {}
        self._jobs: queue.Queue = queue.Queue()
        self._poll_timer = QTimer(self, interval=100, timeout=self._poll)
        self._worker = threading.Thread(target=self._run, name="xcp", daemon=True)
        self._worker.start()
        bus.disconnected.connect(lambda: self.set_connected(False))
        self._make_engine()

    # --- component --------------------------------------------------------------
    @property
    def protocol(self) -> str:
        """What the engine in use speaks, for the log to say so."""
        return getattr(self.engine, "protocol", "XCP")

    @property
    def needs_station(self) -> bool:
        """Whether this engine wants a station address as well as ids."""
        return bool(getattr(self.engine, "needs_station", False))

    def set_station(self, station: int) -> None:
        """Which controller on these identifiers, for a protocol that asks."""
        if self.engine is not None and hasattr(self.engine, "set_station"):
            self.engine.set_station(station)

    def components(self) -> list[str]:
        return COMPONENTS.names("xcp")

    def set_component(self, name: str) -> None:
        if name == self.component_name and self.engine is not None:
            return
        self.disconnect_slave()
        self.component_name = name
        self._ctx.settings.set("components.xcp", name)
        self._make_engine()
        self.result.emit(f"Calibration engine: {name}")

    def _make_engine(self) -> None:
        if self.engine is not None:
            self.engine.close()
        if COMPONENTS.get("xcp", self.component_name) is None:
            # A name from a workspace written before an engine was renamed,
            # or one of your own components that failed to load this run. Say which
            # engine is being used instead rather than quietly using one:
            # connecting with the wrong protocol looks like a broken slave.
            was, self.component_name = self.component_name, COMPONENTS.names("xcp")[0]
            self.result.emit(f"No calibration engine {was!r}; using {self.component_name}")
        try:
            self.engine = COMPONENTS.create("xcp", self.component_name, self._bus, self._ctx)
        except Exception as exc:  # one of your own components may fail to construct
            self.engine = None
            self.result.emit(f"Calibration engine {self.component_name!r} failed: {exc}")

    # --- worker ------------------------------------------------------------------
    def _run(self) -> None:
        while (job := self._jobs.get()) is not None:
            fn, label = job
            try:
                text = fn()
                if text:
                    self.result.emit(text)
            except XcpError as exc:
                self.result.emit(f"{label}: {exc}")
            except seedkey.SeedKeyError as exc:
                # Its own clause because XcpError is a slave's error code
                # and this is not: nothing was asked of the slave, the
                # tool could not work out what to answer with.
                self.result.emit(f"{label}: {exc}")
            except TimeoutError:
                self.result.emit(f"{label}: timeout")
            except Exception as exc:  # report any failure, keep the worker alive
                self.result.emit(f"{label}: {type(exc).__name__}: {exc}")

    def _submit(self, label: str, fn) -> None:
        if self.engine is None:
            self.result.emit(f"{label}: no XCP engine")
            return
        self._jobs.put((fn, label))

    def shutdown(self) -> None:
        self._poll_timer.stop()
        self._jobs.put(None)
        self._worker.join(2.0)
        if self.engine is not None:
            self.engine.close()

    # --- connection ----------------------------------------------------------------
    def set_ids(self, cmd_id: int, res_id: int, extended: bool) -> None:
        if self.engine is not None:
            self.engine.set_ids(cmd_id, res_id, extended)

    @property
    def info(self):
        return self.engine.info if self.engine else None

    def connect_slave(self) -> None:
        def fn() -> str:
            info = self.engine.connect()
            self.set_connected(True)
            order = "big-endian" if info.big_endian else "little-endian"
            return (
                f"{self.protocol} connected ({self.component_name}): resources "
                f"{info.resource_names(info.resources)}, maxCTO {info.max_cto}, "
                f"maxDTO {info.max_dto}, {order}"
            )

        self._submit("CONNECT", fn)

    def disconnect_slave(self) -> None:
        if not self._connected:
            return

        def fn() -> str:
            self.engine.disconnect()
            self.set_connected(False)
            return f"{self.protocol} disconnected"

        self._submit("DISCONNECT", fn)

    def set_connected(self, on: bool) -> None:
        if on == self._connected:
            return
        self._connected = on
        if not on:
            self._poll_timer.stop()
            self._polled.clear()
        self.connected.emit(on)

    @property
    def is_connected(self) -> bool:
        return self._connected

    def unlock(self, resource: int) -> None:
        def fn() -> str:
            seed = self.engine.get_seed(resource)
            key = self._key_for(resource, bytes(seed))
            self.engine.unlock(key)
            return f"{self.protocol} resource 0x{resource:02X} unlocked"

        self._submit("UNLOCK", fn)

    def _key_for(self, resource: int, seed: bytes) -> bytes:
        """The hook first, then the seed and key DLL.

        The hook wins because it is this workspace's own answer and can be
        anything -- a device whose algorithm is three lines of Python
        should not need a compiler. The DLL is the fallback rather than
        something the hook has to reach for, because it is a standard: the
        ASAM interface is the same for every maker, so the ctypes for it
        belongs here and not copied into everybody's hooks file, where a
        fix would never reach the copies.
        """
        key = self._hooks.call("xcp", "compute_key", resource, seed)
        if key is not None:
            return bytes(key)
        dll = str(self._ctx.settings.get(seedkey.DLL_KEY, "") or "")
        if not dll:
            raise seedkey.SeedKeyError(
                "no key algorithm: choose a seed and key DLL in the XCP pane, "
                "or write hooks/xcp.py::compute_key"
            )
        other = str(self._ctx.settings.get(seedkey.PYTHON_KEY, "") or "")
        return seedkey.key_for(dll, resource, seed, other)

    # --- A2L -------------------------------------------------------------------------
    def load_a2l(self, path: str) -> None:
        self.a2l = A2l.load(path)
        self.a2l_loaded.emit(len(self.a2l.parameters))
        self.result.emit(
            f"A2L loaded: {len(self.a2l.measurements())} measurements, "
            f"{len(self.a2l.characteristics())} characteristics"
        )

    def clear_a2l(self) -> None:
        """Forget the A2L. Polling goes with it: it is named parameters that
        are polled, and there are none left to name."""
        self.a2l = None
        self._polled.clear()
        self.a2l_loaded.emit(0)

    # --- read / write ------------------------------------------------------------------
    def _read_value(self, param: Parameter) -> float:
        size = DATATYPES[param.datatype][1]
        big = bool(self.info and self.info.big_endian)
        raw = decode_value(self.engine.read(param.address, size), param.datatype, big)
        return param.conversion.to_phys(raw) if param.conversion else raw

    def read(self, name: str) -> None:
        param = self.a2l.parameters.get(name) if self.a2l else None
        if param is None:
            self.result.emit(f"read {name}: unknown parameter")
            return

        def fn() -> str:
            phys = self._read_value(param)
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
            big = bool(self.info and self.info.big_endian)
            self.engine.write(param.address, encode_value(raw, param.datatype, big))
            return f"{name} <- {phys:g}"

        self._submit(f"write {name}", fn)

    # --- polling ---------------------------------------------------------------------
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
        for name, param in list(self._polled.items()):

            def fn(p=param, n=name) -> str:
                phys = self._read_value(p)
                self.value.emit(n, phys)
                self._signals.push("XCP", n, self._bus.now(), phys, p.unit)
                return ""

            self._submit(f"poll {name}", fn)

    # --- trace labelling ---------------------------------------------------------------
    def classify(self, frame: Frame) -> str | None:
        return self.engine.owns_frame(frame) if self.engine else None
