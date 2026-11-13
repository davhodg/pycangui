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
from dataclasses import dataclass
from itertools import groupby

from PySide6.QtCore import QObject, QTimer, Signal

from pycangui.ccp import engine as _ccp_engine  # noqa: F401  (registers the CCP engine)
from pycangui.core import named_values, seedkey, workspace_files
from pycangui.core.bus import BusManager, Frame
from pycangui.core.components import COMPONENTS
from pycangui.core.context import Context
from pycangui.core.hooks import Hooks
from pycangui.core.signals import SignalHub
from pycangui.uds import images
from pycangui.xcp import DATATYPES, RESOURCE_CAL, checksum, decode_value, encode_value
from pycangui.xcp import transfer as plans
from pycangui.xcp.a2l import MOST_AT_ONCE, A2l, Area, Parameter
from pycangui.xcp.engine import UnsupportedError, XcpEngine, XcpError

#: An engine is named "<protocol>-<implementation>", so that the list says
#: both things it has to: which protocol it speaks, and whose code speaks
#: it. An engine calling into a Rust library would be "xcp-rust" beside
#: "xcp-builtin".
DEFAULT_COMPONENT = "xcp-builtin"

#: How a file is written to the slave: PROGRAM, for flash, or DOWNLOAD, for
#: memory that is written like RAM.
PROGRAM, DOWNLOAD = "program", "download"
#: How what was written is checked afterwards.
VERIFY_CHECKSUM, VERIFY_READ_BACK, VERIFY_NONE = "checksum", "read back", "none"


@dataclass(frozen=True)
class FlashOptions:
    """How the Transfer tab's Program button goes about it."""

    write_with: str = PROGRAM
    #: Erase each area the file has bytes for before writing any of them.
    clear: bool = True
    #: Seed and key for the resource first: PGM to program, CAL to download.
    unlock: bool = True
    verify: str = VERIFY_CHECKSUM
    #: The checksum to compare with. None takes the one an XCP slave says it
    #: used; CCP does not say, so for CCP one has to be chosen to compare.
    checksum_kind: int | None = None
    #: PROGRAM_RESET at the end, which XCP has and CCP does not.
    reset: bool = False
    #: Write the file's bytes that are in no area too.
    outside_too: bool = False


class _CancelledError(Exception):
    pass


class VerifyError(Exception):
    """What was written does not read back, or sum, as the file has it."""


class XcpManager(QObject):
    result = Signal(str)
    connected = Signal(bool)
    a2l_loaded = Signal(int)  # number of parameters
    value = Signal(str, float)  # parameter name, physical value
    progress = Signal(str, int, int)  # what, done, out of
    transferring = Signal(bool)  # a transfer started or finished
    transferred = Signal(bool)  # a transfer finished: True if all of it was done

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
        #: A transfer is running; polling holds back until it is done.
        self._busy = False
        self._cancel = threading.Event()
        self._poll_timer = QTimer(self, interval=100, timeout=self._poll)
        self._worker = threading.Thread(target=self._run, name="xcp", daemon=True)
        self._worker.start()
        bus.disconnected.connect(lambda: self.set_connected(False))
        #: Measurement polling waits while pycangui's own traffic is paused.
        self._paused = False
        if (pause := getattr(ctx, "pause", None)) is not None:
            self._paused = pause.paused
            pause.changed.connect(self.set_paused)
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
        protocol = seedkey.CCP if self.protocol == "CCP" else seedkey.XCP
        return seedkey.key_for(dll, resource, seed, other, protocol)

    # --- A2L -------------------------------------------------------------------------
    def load_a2l(self, path: str, progress=None) -> None:
        """Read an A2L and make it the one in use.

        ``progress`` is told how far the reading has got, 0 to 1, and may
        raise to stop it: the A2L there was stays the one in use.
        """
        self.a2l = A2l.load(path, progress)
        self.a2l_loaded.emit(len(self.a2l.parameters))
        where = workspace_files.shown(path, self._ctx.workspace_dir)
        self.result.emit(
            f"A2L loaded: {where} ({len(self.a2l.measurements())} measurements, "
            f"{len(self.a2l.characteristics())} characteristics)"
        )
        # Listed, so the file is seen whole, and said to be more than is read.
        if unread := self.a2l.unreadable():
            self.result.emit(
                f"A2L: {len(unread)} of them are listed but not read -- curves, maps, "
                "arrays and text. Hover over one for why."
            )
        for protocol, info in sorted(self.a2l.checksums.items()):
            most = f", up to 0x{info.max_block:X} bytes at a time" if info.max_block else ""
            what = checksum.name(info.kind) if info.kind else f"procedure 0x{info.code:X}"
            self.result.emit(f"A2L: {protocol} checksum {what}{most}")
        if (ids := self.a2l.xcp_on_can) is not None:
            self.result.emit(
                f"A2L: it gives XCP on CAN as command 0x{ids.command_id:X}, "
                f"response 0x{ids.response_id:X}"
                + (", 29-bit" if ids.extended else "")
                + (f", {ids.bitrate} bit/s" if ids.bitrate else "")
            )

    def clear_a2l(self) -> None:
        """Forget the A2L. Polling goes with it: it is named parameters that
        are polled, and there are none left to name."""
        self.a2l = None
        self._polled.clear()
        self.a2l_loaded.emit(0)

    # --- read / write ------------------------------------------------------------------
    def _big_endian(self, param: Parameter) -> bool:
        """Which way round this parameter's bytes are.

        The parameter's own BYTE_ORDER if it has one, then the file's, then
        what the slave said on connecting: the A2L describes the memory, and
        the slave's answer is only a guess at it where the file says nothing.
        """
        if param.big_endian is not None:
            return param.big_endian
        if self.a2l is not None and self.a2l.big_endian is not None:
            return self.a2l.big_endian
        return bool(self.info and self.info.big_endian)

    def _read_raw(self, param: Parameter) -> float:
        """The stored value, whole: before any bit mask or conversion."""
        size = DATATYPES[param.datatype][1]
        data = self.engine.read(param.address, size, **_space(param))
        return decode_value(data, param.datatype, self._big_endian(param))

    def _read_value(self, param: Parameter) -> float:
        raw = self._read_raw(param)
        if (mask := _mask(param)) is not None:
            raw = (int(raw) & mask) >> _shift(mask)
        return param.conversion.to_phys(raw) if param.conversion else raw

    def shown(self, name: str, phys: float) -> str:
        """A value as it is shown: by name where the A2L names it, *Run (1)*."""
        param = self.a2l.find(name) if self.a2l else None
        named = named_values.shown(phys, param.choices) if param and param.choices else None
        return named or f"{phys:g}"

    def read(self, name: str) -> None:
        param = self.a2l.find(name) if self.a2l else None
        if param is None:
            self.result.emit(f"read {name}: unknown parameter")
            return
        if not param.readable:
            self.result.emit(f"read {name}: {param.unreadable}")
            return
        if param.is_array:
            if param.count > MOST_AT_ONCE:
                self.result.emit(
                    f"read {name}: it is {param.count} values, and up to {MOST_AT_ONCE} are "
                    "read in one go. Open it and read the ones wanted."
                )
                return
            self._read_array(param)
            return

        def fn() -> str:
            phys = self._read_value(param)
            self.value.emit(name, phys)
            unit = f" {param.unit}" if param.unit else ""
            return f"{name} = {self.shown(name, phys)}{unit}"

        self._submit(f"read {name}", fn)

    def _read_array(self, array: Parameter) -> None:
        """Every value of an array, one read each, as one job.

        One job so that the values come out together and in order, and so
        that a row of a hundred is one line in the output and not a hundred.
        """
        elements = self.a2l.elements(array)

        def fn() -> str:
            shown = []
            for element in elements:
                phys = self._read_value(element)
                self.value.emit(element.name, phys)
                shown.append(self.shown(element.name, phys))
            listed = ", ".join(shown[:ARRAY_SHOWN])
            more = f", ... ({len(shown)} values)" if len(shown) > ARRAY_SHOWN else ""
            unit = f" {array.unit}" if array.unit else ""
            return f"{array.name} = [{listed}{more}]{unit}"

        self._submit(f"read {array.name}", fn)

    def beyond_limits(self, name: str, text: str) -> tuple[float, float, float] | None:
        """A value that is outside the limits the A2L gives its characteristic.

        As ``(value, lower, upper)``, or None for one that is within them, or
        that the file gives no usable limits for, or that is not a number --
        which the write itself will say.
        """
        param = self.a2l.find(name) if self.a2l else None
        if param is None or param.lower is None or param.upper is None:
            return None
        if param.lower >= param.upper:
            return None  # 0 and 0: a generator that had nothing to say
        try:
            phys = float(named_values.plain(text, param.choices))
        except ValueError:
            return None
        if param.lower <= phys <= param.upper:
            return None
        return phys, param.lower, param.upper

    def write(self, name: str, text: str, beyond_limits: bool = False) -> None:
        """Write a characteristic. A value outside the A2L's limits is refused
        unless ``beyond_limits`` says it is meant: the pane asks first, and a
        script has to say so in as many words."""
        param = self.a2l.find(name) if self.a2l else None
        if param is None or not param.writable:
            why = param.unreadable if param is not None and param.unreadable else ""
            self.result.emit(f"write {name}: {why or 'not a writable characteristic'}")
            return
        if not beyond_limits and (outside := self.beyond_limits(name, text)) is not None:
            value, lower, upper = outside
            self.result.emit(
                f"write {name}: {value:g} is outside the A2L's limits, {lower:g} to {upper:g} "
                "-- not written"
            )
            return

        def fn() -> str:
            # A name, a number, or both as they are shown: Run, 1, Run (1).
            phys = float(named_values.plain(text, param.choices))
            raw = param.conversion.to_raw(phys) if param.conversion else phys
            if (mask := _mask(param)) is not None:
                # Only its own bits: the rest of the stored value is somebody
                # else's, and is read first so that it can be put back.
                kept = int(self._read_raw(param)) & ~mask
                raw = kept | ((round(raw) << _shift(mask)) & mask)
            data = encode_value(raw, param.datatype, self._big_endian(param))
            self.engine.write(param.address, data, **_space(param))
            return f"{name} <- {self.shown(name, phys)}"

        self._submit(f"write {name}", fn)

    # --- polling ---------------------------------------------------------------------
    def set_polled(self, name: str, on: bool) -> None:
        param = self.a2l.find(name) if self.a2l else None
        if param is None or not param.readable or param.is_array:
            return  # one of an array's values is plotted, not the row of them
        if on:
            self._polled[name] = param
            if self._connected and not self._poll_timer.isActive():
                self._poll_timer.start()
        else:
            self._polled.pop(name, None)
            if not self._polled:
                self._poll_timer.stop()

    def set_paused(self, on: bool) -> None:
        """Hold measurement polling back, or let it go on: see core/pause.py.

        What is ticked stays ticked, and a read or a write asked for by hand
        still goes out.
        """
        self._paused = on

    @property
    def paused(self) -> bool:
        return self._paused

    def _poll(self) -> None:
        if not self._connected or not self._polled or self._paused or self._busy:
            return
        for name, param in list(self._polled.items()):

            def fn(p=param, n=name) -> str:
                phys = self._read_value(p)
                self.value.emit(n, phys)
                self._signals.push("XCP", n, self._bus.now(), phys, p.unit)
                if p.choices:
                    self._signals.set_choices(f"XCP/{n}", p.choices)
                return ""

            self._submit(f"poll {name}", fn)

    # --- transfers: the Transfer tab ------------------------------------------------------
    @property
    def is_transferring(self) -> bool:
        return self._busy

    def cancel_transfer(self) -> None:
        """Stop after the piece being sent now, never part way through a command."""
        if self._busy:
            self._cancel.set()
            self.result.emit("Transfer: stopping after this piece")

    def _transfer(self, label: str, fn) -> None:
        """Run a transfer on the worker: minutes rather than one answer, with
        progress, a Cancel, and polling held back meanwhile."""
        if self.engine is None:
            self.result.emit(f"{label}: no XCP engine")
            return
        if self._busy:
            self.result.emit(f"{label}: a transfer is already running")
            return
        if not self._connected:
            self.result.emit(f"{label}: not connected")
            return
        self._busy = True
        self._cancel.clear()
        self.transferring.emit(True)

        def job() -> str:
            done = False
            try:
                text = fn()
                done = True
                return text
            except _CancelledError:
                return f"{label}: cancelled"
            except (UnsupportedError, VerifyError, images.ImageError) as exc:
                return f"{label}: {exc}"
            finally:
                self._busy = False
                self.transferring.emit(False)
                self.transferred.emit(done)

        self._jobs.put((job, label))

    def _step(self) -> None:
        if self._cancel.is_set():
            raise _CancelledError

    def _unlock_now(self, resource: int) -> str:
        """Seed and key on the worker, as one step of a transfer."""
        seed = bytes(self.engine.get_seed(resource))
        if not seed:
            return f"resource 0x{resource:02X} is not protected"
        self.engine.unlock(self._key_for(resource, seed))
        return f"resource 0x{resource:02X} unlocked"

    def plan(self, image: images.Image, areas: list[Area], outside_too: bool = False):
        return plans.make(image.segments, areas, outside_too)

    def flash(self, image: images.Image, areas: list[Area], options: FlashOptions) -> None:
        """Write a file to the slave, area by area, in the order the areas give."""
        plan = self.plan(image, areas, options.outside_too)
        label = "Program" if options.write_with == PROGRAM else "Download"
        if not plan.runs:
            self.result.emit(f"{label}: nothing in the file is inside the areas ticked")
            return

        def fn() -> str:
            engine = self.engine
            said = self.result.emit
            for address, size in plan.outside:
                said(f"{label}: 0x{address:X}+0x{size:X} is in no area, and is left out")
            if options.write_with == PROGRAM:
                if options.unlock:
                    said(f"{label}: {self._unlock_now(engine.pgm_resource)}")
                engine.program_start()
                if options.clear:
                    for n, area in enumerate(plan.clears):
                        self._step()
                        self.progress.emit(f"Erasing {area.name}", n, len(plan.clears))
                        engine.program_clear(area.address, area.size, **_space(area))
                        said(f"{label}: erased {area.name}, 0x{area.address:X}+0x{area.size:X}")
                write = engine.program
            else:
                if options.unlock:
                    said(f"{label}: {self._unlock_now(RESOURCE_CAL)}")
                write = engine.write
            done, total = 0, plan.size
            for name, runs in groupby(plan.runs, key=lambda r: r.area):
                written = 0
                for run in runs:
                    for piece in plans.chunks(run):
                        self._step()
                        write(piece.address, piece.data, **_space(piece))
                        done += len(piece.data)
                        written += len(piece.data)
                        self.progress.emit(f"Writing {name}", done, total)
                if options.write_with == PROGRAM:
                    engine.program_end()
                said(f"{label}: wrote {written} bytes to {name}")
            verified = self._verify(plan.runs, options) if options.verify != VERIFY_NONE else ""
            if options.reset and options.write_with == PROGRAM:
                engine.program_reset()
                self.set_connected(False)
                said(f"{label}: PROGRAM_RESET -- the slave restarts, and is disconnected")
            return f"{label}: {done} bytes in {len(plan.clears)} area(s){verified}"

        self._transfer(label, fn)

    def _verify(self, runs, options: FlashOptions) -> str:
        if options.verify == VERIFY_READ_BACK:
            total, done = sum(len(r.data) for r in runs), 0
            for run in runs:
                for piece in plans.chunks(run):
                    self._step()
                    got = self.engine.read(piece.address, len(piece.data), **_space(piece))
                    if got != piece.data:
                        at = _first_difference(got, piece.data)
                        raise VerifyError(
                            f"reading back, 0x{piece.address + at:X} in {run.area} "
                            "is not what was written"
                        )
                    done += len(piece.data)
                    self.progress.emit("Reading back", done, total)
            return ", read back and the same"
        for run in runs:
            for address, data in self._blocks(run.address, run.data):
                self._step()
                kind, value, width = self.engine.checksum(address, len(data), **_space(run))
                line, same = self._compare(kind, value, width, data, options.checksum_kind)
                self.result.emit(f"Checksum {run.area} 0x{address:X}+0x{len(data):X}: {line}")
                if same is False:
                    raise VerifyError(f"the checksum of {run.area} is not the file's")
        return ", checksums checked"

    def a2l_checksum(self):
        """What the A2L says about this protocol's checksum, if anything."""
        return self.a2l.checksums.get(self.protocol) if self.a2l is not None else None

    def _blocks(self, address: int, data):
        """A stretch to be summed, in blocks no bigger than the A2L allows.

        ``data`` is the file's bytes, or a size where there is no file.
        """
        size = data if isinstance(data, int) else len(data)
        info = self.a2l_checksum()
        most = info.max_block if info and info.max_block else size
        for at in range(0, size, max(most, 1)):
            part = min(most, size - at)
            yield address + at, (part if isinstance(data, int) else data[at : at + part])

    def _compare(self, kind, value, width, data, chosen) -> tuple[str, bool | None]:
        """What the slave said, and whether it is the file's: None where it
        cannot be said, for a checksum nobody here knows how to make."""
        digits = 2 * width
        line = f"0x{value:0{digits}X}"
        # Chosen in the tab, then what the slave says it used, then the A2L.
        info = self.a2l_checksum()
        use = next((k for k in (chosen, kind, info and info.kind) if k is not None), None)
        if kind is not None:
            line += f" ({checksum.name(kind)})"
        if data is None:
            return line, None
        if use is None and info is not None:
            return (
                line + f", not compared: the A2L's procedure 0x{info.code:X} is the maker's own",
                None,
            )
        big_endian = bool(self.info and self.info.big_endian)
        expected = checksum.compute(use, data, big_endian) if use is not None else None
        if expected is None:
            return line + ", not compared: no algorithm here to compare it with", None
        expected &= (1 << (8 * width)) - 1
        if expected == value:
            return line + ", the same as the file", True
        return line + f", the file's is 0x{expected:0{digits}X} by {checksum.name(use)}", False

    def upload(self, path: str, areas: list[Area]) -> None:
        """Read the areas out of the slave into one file."""

        def fn() -> str:
            total, done, segments = sum(a.size for a in areas), 0, []
            for area in areas:
                data = bytearray()
                for at in range(0, area.size, plans.CHUNK):
                    self._step()
                    size = min(plans.CHUNK, area.size - at)
                    data += self.engine.read(area.address + at, size, **_space(area))
                    done += size
                    self.progress.emit(f"Reading {area.name}", done, total)
                segments.append(images.Segment(area.address, bytes(data)))
            fmt = images.write_segments(path, segments)
            where = workspace_files.shown(path, self._ctx.workspace_dir)
            return f"Upload: {done} bytes from {len(areas)} area(s) to {where}, {fmt}"

        if not areas:
            self.result.emit("Upload: no areas ticked to read")
            return
        self._transfer("Upload", fn)

    def build_checksums(
        self, areas: list[Area], image: images.Image | None = None, kind: int | None = None
    ) -> None:
        """BUILD_CHECKSUM over each area, or with a file, over the file's
        bytes in each area, so that there is something to compare it with."""
        if image is not None:
            runs = self.plan(image, areas).runs
            ranges = [
                (Area(r.area, r.address, len(r.data), extension=r.extension), r.data) for r in runs
            ]
        else:
            ranges = [(area, None) for area in areas]

        def fn() -> str:
            differ = []
            for n, (area, data) in enumerate(ranges):
                self.progress.emit(f"Checksum {area.name}", n, len(ranges))
                for address, part in self._blocks(
                    area.address, area.size if data is None else data
                ):
                    self._step()
                    size = part if isinstance(part, int) else len(part)
                    found, value, width = self.engine.checksum(address, size, **_space(area))
                    sent = None if isinstance(part, int) else part
                    line, same = self._compare(found, value, width, sent, kind)
                    self.result.emit(f"Checksum {area.name} 0x{address:X}+0x{size:X}: {line}")
                    if same is False and area.name not in differ:
                        differ.append(area.name)
            if differ:
                raise VerifyError(f"not the file's in {', '.join(differ)}")
            return ""

        if not ranges:
            self.result.emit("Checksum: nothing to sum -- no areas ticked, or none the file is in")
            return
        self._transfer("Checksum", fn)

    # --- trace labelling ---------------------------------------------------------------
    def classify(self, frame: Frame) -> str | None:
        return self.engine.owns_frame(frame) if self.engine else None


#: How many of an array's values the output line lists before it counts the rest.
ARRAY_SHOWN = 16


def _first_difference(got: bytes, wanted: bytes) -> int:
    """Where two runs of bytes first differ, or where the shorter one ends."""
    for at, (a, b) in enumerate(zip(got, wanted, strict=False)):
        if a != b:
            return at
    return min(len(got), len(wanted))


def _space(param) -> dict:
    """The address extension, as a keyword and only when there is one: an
    engine written before extensions takes an address and nothing else.
    For a parameter, an area or a run of a file alike."""
    return {"extension": param.extension} if param.extension else {}


def _mask(param: Parameter) -> int | None:
    """A parameter's bit mask, where it has one that means anything.

    Not for a float: a mask over the bits of one is not a number.
    """
    if not param.bit_mask or DATATYPES[param.datatype][0] in "efd":
        return None
    return param.bit_mask


def _shift(mask: int) -> int:
    """How far down a masked value is moved: to its own lowest bit."""
    return (mask & -mask).bit_length() - 1
