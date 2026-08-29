"""UDS client side.  Requests run on a worker thread (they block on the ECU's
reply); every outcome comes back as a ``result`` signal with a readable line
for the pane, so the pane never touches udsoncan directly."""

from __future__ import annotations

import struct
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import udsoncan
from PySide6.QtCore import QObject, QTimer, Signal, Slot
from udsoncan import DataFormatIdentifier, Filesize, MemoryLocation, Request, Response, services
from udsoncan.client import Client
from udsoncan.connections import BaseConnection
from udsoncan.exceptions import NegativeResponseException, TimeoutException

from pycangui.core.backends import BACKENDS
from pycangui.core.bus import BusManager, Frame
from pycangui.core.context import Context
from pycangui.core.hooks import Hooks
from pycangui.core.worker import Worker
from pycangui.uds import UdsConfig
from pycangui.uds.images import Image, ImageError, Segment
from pycangui.uds.images import write as write_image
from pycangui.uds.standard import memory_record
from pycangui.uds.transport import IsoTpTransport

DEFAULT_BACKEND = "can-isotp"


class TransferCancelledError(Exception):
    """Asked to stop, or the ECU stopped first."""


class _TransportConnection(BaseConnection):
    """Adapts any IsoTpTransport to what udsoncan's Client expects."""

    def __init__(self, transport: IsoTpTransport) -> None:
        super().__init__(name="pycangui")
        self._transport = transport
        self._opened = False

    def open(self):
        self._transport.open()
        self._opened = True
        return self

    def close(self) -> None:
        self._transport.close()
        self._opened = False

    def is_open(self) -> bool:
        return self._opened

    def specific_send(self, payload: bytes) -> None:
        self._transport.send(payload)

    def specific_wait_frame(self, timeout: float = 2) -> bytes:
        data = self._transport.recv(timeout)
        if data is None:
            raise TimeoutException(f"no ISO-TP frame in {timeout} s")
        return data

    def empty_rxqueue(self) -> None:
        while not self._transport.empty:
            self._transport.recv(0)


#: RequestFileTransfer modes of operation (ISO 14229-1:2013 Annex G), in the
#: order worth offering: the ones that write, then the ones that read, then
#: the two that are neither.
FILE_MODES = {
    1: "add file",
    3: "replace file",
    4: "read file",
    5: "read directory",
    2: "delete file",
    6: "resume file",
}

#: Modes that send a local file to the ECU, and modes that bring one back.
FILE_MODES_SENDING = (1, 3, 6)
FILE_MODES_RECEIVING = (4, 5)

#: RoutineControl identifiers that go with a memory download.  Erase is the
#: one ISO 14229-1 names (Annex F); what to run afterwards to have the ECU
#: check what it was given is manufacturer specific, and 0x0202 is only the
#: number the HIS/AUTOSAR flash bootloaders settled on.
ERASE_MEMORY = 0xFF00
CHECK_MEMORY = 0x0202

SESSIONS = {1: "default", 2: "programming", 3: "extended", 4: "safety system"}
RESETS = {1: "hard reset", 2: "key off/on", 3: "soft reset", 4: "enable rapid power shutdown"}


class UdsManager(QObject):
    result = Signal(str)  # one readable line per request outcome
    opened = Signal(bool)  # client open state changed
    did_value = Signal(int, bytes)  # did, raw data (for scripts / future signal hub use)
    progress = Signal(str, int, int)  # what, bytes done, bytes expected
    transferring = Signal(bool)  # a transfer started or finished

    def __init__(self, bus: BusManager, hooks: Hooks, ctx: Context) -> None:
        super().__init__()
        self._bus = bus
        self._hooks = hooks
        self._ctx = ctx
        self.config = UdsConfig()
        self.client: Client | None = None
        self.backend_name = ctx.settings.get("backends.isotp", DEFAULT_BACKEND)
        self._transport: IsoTpTransport | None = None
        self._worker = Worker()  # starts itself the first time it is used
        self._cancel = threading.Event()
        self._busy = False
        self._tp_timer = QTimer(self, timeout=self._tester_present_tick)
        bus.disconnected.connect(self.close)

    # --- lifecycle -------------------------------------------------------------
    @property
    def is_open(self) -> bool:
        return self.client is not None

    def open(self, config: UdsConfig) -> None:
        self.close()
        if self._bus.bus is None:
            self.result.emit("UDS: not connected to a bus")
            return
        self.config = config
        try:
            self._transport = BACKENDS.create(
                "isotp", self.backend_name, self._bus, config, self._ctx
            )
        except Exception as exc:
            self.result.emit(f"UDS transport {self.backend_name!r} failed: {exc}")
            return
        conn = _TransportConnection(self._transport)
        cfg = dict(udsoncan.configs.default_client_config)
        cfg.update(
            {
                "p2_timeout": config.p2_timeout_s,
                "p2_star_timeout": config.p2_star_timeout_s,
                "request_timeout": config.p2_star_timeout_s + 1,
                "security_algo": self._security_algo,
                "exception_on_negative_response": True,
                "exception_on_unexpected_response": False,
            }
        )
        self.client = Client(conn, config=cfg)
        self.client.open()
        ids = f"tx {config.tx_id:X} rx {config.rx_id:X}"
        ext = " (29-bit)" if config.extended_id else ""
        self.result.emit(f"UDS open [{self.backend_name}]: {ids}{ext}")
        self.opened.emit(True)

    @Slot()
    def close(self) -> None:
        self.set_tester_present(False)
        if self.client is not None:
            try:
                self.client.close()
            finally:
                self.client = None
        if self._transport is not None:
            self._transport.close()
            self._transport = None
            self.result.emit("UDS closed")
            self.opened.emit(False)

    def shutdown(self) -> None:
        self.close()  # stops the ISO-TP threads before the bus disappears
        self._worker.stop()
        try:
            self._bus.disconnected.disconnect(self.close)
        except (RuntimeError, TypeError):
            pass

    # --- backend ---------------------------------------------------------------
    def backends(self) -> list[str]:
        return BACKENDS.names("isotp")

    def set_backend(self, name: str) -> None:
        if name == self.backend_name:
            return
        was_open = self.is_open
        self.close()
        self.backend_name = name
        self._ctx.settings.set("backends.isotp", name)
        self.result.emit(f"UDS transport: {name}")
        if was_open:
            self.open(self.config)

    # --- trace labelling -------------------------------------------------------
    def classify(self, frame: Frame) -> str | None:
        """Kind labels for the configured ids (the standard 0x7Ex range is in core.classify)."""
        if self.client is None:
            return None
        if frame.can_id == self.config.tx_id:
            return "UDS req"
        if frame.can_id == self.config.rx_id:
            return "UDS resp"
        return None

    # --- hooks ---------------------------------------------------------------------
    def _security_algo(self, level: int, seed: bytes, params: Any) -> bytes:
        key = self._hooks.call("uds", "security_key", level, seed)
        if key is None:
            raise RuntimeError(
                f"no security algorithm for level {level}: implement hooks/uds.py::security_key"
            )
        return bytes(key)

    # --- request plumbing ----------------------------------------------------------
    def _run(self, label: str, fn: Callable[[Client], str]) -> None:
        client = self.client
        if client is None:
            self.result.emit(f"{label}: UDS not open")
            return

        def job() -> str:
            try:
                return fn(client)
            except NegativeResponseException as exc:
                r = exc.response
                return f"{label}: NRC 0x{r.code:02X} {r.code_name}"
            except TimeoutException:
                return f"{label}: timeout (no response)"

        def done(text: str | None, error: str | None) -> None:
            self.result.emit(text if error is None else f"{label}: {error}")

        self._worker.submit(job, done)

    # --- services ----------------------------------------------------------------------
    def change_session(self, session: int) -> None:
        def fn(c: Client) -> str:
            r = c.change_session(session)
            timing = ""
            sd = r.service_data
            if sd.p2_server_max is not None:
                p2, p2s = sd.p2_server_max * 1000, sd.p2_star_server_max * 1000
                timing = f" (P2 {p2:.0f} ms, P2* {p2s:.0f} ms)"
            return f"Session -> {SESSIONS.get(session, session)}{timing}"

        self._run("DiagnosticSessionControl", fn)

    def unlock(self, level: int) -> None:
        def fn(c: Client) -> str:
            c.unlock_security_access(level)
            return f"Security level {level}: unlocked"

        self._run("SecurityAccess", fn)

    def tester_present(self) -> None:
        self._run("TesterPresent", lambda c: (c.tester_present(), "TesterPresent OK")[1])

    def set_tester_present(self, on: bool) -> None:
        if on and self.client is not None:
            self._tp_timer.start(int(self.config.tester_present_s * 1000))
        else:
            self._tp_timer.stop()

    def _tester_present_tick(self) -> None:
        if self.client is None:
            self._tp_timer.stop()
            return

        def fn(c: Client) -> str:
            c.tester_present()
            return ""

        # quiet: only failures are reported
        client = self.client

        def job() -> str:
            try:
                return fn(client)
            except (NegativeResponseException, TimeoutException) as exc:
                return f"TesterPresent: {exc}"

        self._worker.submit(job, lambda t, e: self.result.emit(t or e) if (t or e) else None)

    def ecu_reset(self, reset_type: int) -> None:
        def fn(c: Client) -> str:
            c.ecu_reset(reset_type)
            return f"ECUReset ({RESETS.get(reset_type, reset_type)}) OK"

        self._run("ECUReset", fn)

    def did_label(self, did: int) -> str:
        """ "F190 (VIN)" -- the number, and what the identifier is called."""
        name = self._hooks.call("uds", "did_label", did)
        return f"{did:04X} ({name})" if name else f"{did:04X}"

    def routine_label(self, routine_id: int) -> str:
        """ "FF00 (Erase memory)" -- the number, and what the routine is."""
        name = self._hooks.call("uds", "routine_label", routine_id)
        return f"{routine_id:04X} ({name})" if name else f"{routine_id:04X}"

    def read_did(self, did: int) -> None:
        def fn(c: Client) -> str:
            req = Request(services.ReadDataByIdentifier, data=struct.pack(">H", did))
            resp = c.send_request(req)
            data = bytes(resp.data[2:])  # strip echoed DID
            self.did_value.emit(did, data)
            text = self._hooks.call("uds", "did_decode", did, data)
            value = text if text is not None else describe_bytes(data)
            return f"DID {self.did_label(did)} = {value}"

        self._run(f"ReadDID {did:04X}", fn)

    def write_did(self, did: int, text: str) -> None:
        def fn(c: Client) -> str:
            data = self._hooks.call("uds", "did_encode", did, text)
            if data is None:
                data = parse_bytes(text)
            req = Request(services.WriteDataByIdentifier, data=struct.pack(">H", did) + bytes(data))
            c.send_request(req)
            return f"DID {self.did_label(did)} written ({len(data)} bytes)"

        self._run(f"WriteDID {did:04X}", fn)

    def read_dtcs(self, status_mask: int) -> None:
        def fn(c: Client) -> str:
            r = c.get_dtc_by_status_mask(status_mask)
            dtcs = r.service_data.dtcs
            if not dtcs:
                return f"DTCs (mask 0x{status_mask:02X}): none"
            lines = [f"DTCs (mask 0x{status_mask:02X}): {len(dtcs)}"]
            for d in dtcs:
                desc = self._hooks.call("uds", "dtc_description", d.id)
                lines.append(
                    f"  {dtc_code(d.id)} ({d.id:06X}) status 0x{d.status.get_byte_as_int():02X}"
                    f" {status_flags(d.status)}{' - ' + desc if desc else ''}"
                )
            return "\n".join(lines)

        self._run("ReadDTCInformation", fn)

    def clear_dtcs(self, group: int = 0xFFFFFF) -> None:
        def fn(c: Client) -> str:
            c.clear_dtc(group)
            return f"DTCs cleared (group {group:06X})"

        self._run("ClearDiagnosticInformation", fn)

    def routine(self, control: int, routine_id: int, data: bytes) -> None:
        names = {1: "start", 2: "stop", 3: "result"}

        def fn(c: Client) -> str:
            r = c.routine_control(routine_id, control, data or None)
            status = bytes(r.service_data.routine_status_record or b"")
            verb = names.get(control, control)
            return f"Routine {self.routine_label(routine_id)} {verb}: OK {describe_bytes(status)}"

        self._run(f"RoutineControl {routine_id:04X}", fn)

    def raw(self, payload: bytes) -> None:
        def fn(c: Client) -> str:
            c.conn.send(payload)
            raw = c.conn.wait_frame(timeout=self.config.p2_star_timeout_s)
            if raw is None:
                return f"Raw {payload.hex(' ').upper()}: timeout"
            resp = Response.from_payload(raw)
            if resp.positive:
                return f"Raw {payload.hex(' ').upper()} -> {bytes(raw).hex(' ').upper()}"
            return f"Raw {payload.hex(' ').upper()} -> NRC 0x{resp.code:02X} {resp.code_name}"

        self._run("Raw", fn)

    # --- transfers (0x34 / 0x35 / 0x36 / 0x37 / 0x38) --------------------------------
    @property
    def is_transferring(self) -> bool:
        return self._busy

    def cancel_transfer(self) -> None:
        """Stop after the block in flight.

        Not part way through one: the ECU has already been promised a
        TransferData, and abandoning it half sent leaves the connection out of
        step for every request after it.
        """
        if self._busy:
            self._cancel.set()
            self.result.emit("Transfer: stopping after this block")

    def _run_transfer(self, label: str, fn: Callable[[Client], str]) -> None:
        """Like _run, but for something that takes minutes rather than one reply."""
        client = self.client
        if client is None:
            self.result.emit(f"{label}: UDS not open")
            return
        if self._busy:
            self.result.emit(f"{label}: a transfer is already running")
            return
        self._busy = True
        self._cancel.clear()
        # Tester present is stopped rather than left ticking.  The worker runs
        # one job at a time, so every tick raised during a long transfer would
        # queue behind it and then arrive in a burst once it finished; the
        # transfer is itself enough to keep the session alive.
        resume_tester_present = self._tp_timer.isActive()
        self._tp_timer.stop()
        self.transferring.emit(True)

        def job() -> str:
            try:
                return fn(client)
            except TransferCancelledError as exc:
                return f"{label}: cancelled{exc}"
            except ImageError as exc:
                return f"{label}: {exc}"
            except NegativeResponseException as exc:
                r = exc.response
                return f"{label}: NRC 0x{r.code:02X} {r.code_name}"
            except TimeoutException:
                return f"{label}: timeout (no response)"
            except OSError as exc:
                return f"{label}: {exc}"

        def done(text: str | None, error: str | None) -> None:
            self._busy = False
            self.transferring.emit(False)
            if resume_tester_present and self.client is not None:
                self.set_tester_present(True)
            self.result.emit(text if error is None else f"{label}: {error}")

        self._worker.submit(job, done)

    @staticmethod
    def _narrowest(value: int) -> int:
        """Bits needed to write this number, never fewer than eight.

        udsoncan works this out from the bit length, which makes it zero for
        the number zero -- and then refuses the zero it just produced.  An
        image that starts at address 0 is an ordinary thing for a bootloader
        to be given, so the floor is put in here.
        """
        return max(8, ((value.bit_length() + 7) // 8) * 8)

    def _memory(self, address: int, size: int, width: int | None) -> MemoryLocation:
        """Where to write, and how wide to say it.

        `width` is in bits, or None for the narrowest that fits.  Some
        bootloaders insist on a fixed width whatever the numbers are, and
        answer anything else with NRC 0x13.
        """
        return MemoryLocation(
            address=address,
            memorysize=size,
            address_format=width or self._narrowest(address),
            memorysize_format=width or self._narrowest(size),
        )

    @staticmethod
    def _block_size(reported: int | None, override: int) -> int:
        """How many data bytes fit in one TransferData.

        maxNumberOfBlockLength counts the whole request message, so the
        service id and the block sequence counter come out of it first.  Those
        two bytes are the usual reason a download runs perfectly until the ECU
        answers 0x31 to the last block.
        """
        if override > 0:
            return override
        return max(1, (reported or 4) - 2)

    def _send_blocks(
        self, c: Client, data: bytes, size: int, label: str, done: int, total: int
    ) -> int:
        """TransferData until the bytes run out.  Returns the new running total."""
        sequence = 1  # ISO 14229: the first block is 1, and 0xFF is followed by 0
        for start in range(0, len(data), size):
            if self._cancel.is_set():
                raise TransferCancelledError(f" after {done} of {total} bytes")
            block = data[start : start + size]
            c.transfer_data(sequence, block)
            sequence = (sequence + 1) % 256
            done += len(block)
            self.progress.emit(label, done, total)
        return done

    def _receive_blocks(self, c: Client, expected: int, label: str) -> bytes:
        """Empty TransferData requests until the ECU has given `expected` bytes."""
        chunks: list[bytes] = []
        got = 0
        sequence = 1
        while got < expected:
            if self._cancel.is_set():
                raise TransferCancelledError(f" after {got} of {expected} bytes")
            r = c.transfer_data(sequence)
            block = bytes(r.service_data.parameter_records or b"")
            if not block:
                raise TransferCancelledError(
                    f": the ECU stopped sending after {got} of {expected} bytes"
                )
            chunks.append(block)
            got += len(block)
            sequence = (sequence + 1) % 256
            self.progress.emit(label, min(got, expected), expected)
        return b"".join(chunks)[:expected]

    def _erase(self, c: Client, segment: Segment, width: int | None) -> None:
        """RoutineControl start 0xFF00 over one segment's addresses.

        Flash has to be erased before it can be written, and ISO 14229-1 names
        this routine for the purpose.  What goes in the option record is not
        standardised; an address and length in the usual format is what most
        bootloaders expect, and hooks/uds.py::erase_options is where to change
        it for one that does not.
        """
        options = self._hooks.call("uds", "erase_options", segment.address, len(segment), width)
        record = (
            bytes(options)
            if options is not None
            else memory_record(segment.address, len(segment), width)
        )
        self.result.emit(
            f"Erase {segment.address:08X}+{len(segment)}: "
            f"routine {self.routine_label(ERASE_MEMORY)}"
        )
        c.routine_control(ERASE_MEMORY, 1, record or None)

    def _check(self, c: Client, routine: int, segment: Segment, width: int | None) -> str:
        """Whatever the ECU is asked to run once a segment has been sent."""
        options = self._hooks.call(
            "uds", "check_options", routine, segment.address, len(segment), segment.data, width
        )
        record = (
            bytes(options)
            if options is not None
            else memory_record(segment.address, len(segment), width)
        )
        r = c.routine_control(routine, 1, record or None)
        status = bytes(r.service_data.routine_status_record or b"")
        return f"Check {segment.address:08X}: routine {self.routine_label(routine)} " + (
            f"OK {describe_bytes(status)}" if status else "OK"
        )

    def download(
        self,
        image: Image,
        block_size: int = 0,
        dfi: int = 0,
        width: int | None = None,
        erase: bool = False,
        check: int = 0,
    ) -> None:
        """Send a firmware image to the ECU: 0x34, 0x36 per block, then 0x37.

        One RequestDownload per segment.  A file with gaps in it has them for a
        reason, and filling them would write bytes the file never contained
        over whatever the ECU had at those addresses.

        `erase` runs the erase routine over every segment *before* the first
        one is written, rather than each just before its own download: two
        segments can share a flash block, and erasing between them would take
        the first one back out again.

        `check` is the routine to run after each segment has been sent, or 0
        for none.
        """
        fmt = DataFormatIdentifier(compression=(dfi >> 4) & 0xF, encryption=dfi & 0xF)
        total = image.size
        count = len(image.segments)

        def fn(c: Client) -> str:
            done = 0
            if erase:
                for segment in image.segments:
                    self._erase(c, segment, width)
            for index, segment in enumerate(image.segments, 1):
                r = c.request_download(self._memory(segment.address, len(segment), width), dfi=fmt)
                size = self._block_size(r.service_data.max_length, block_size)
                which = f" (segment {index} of {count})" if count > 1 else ""
                self.result.emit(
                    f"RequestDownload {segment.address:08X}: "
                    f"{len(segment)} bytes in blocks of {size}{which}"
                )
                done = self._send_blocks(c, segment.data, size, "Download", done, total)
                c.request_transfer_exit()
                if check:
                    self.result.emit(self._check(c, check, segment, width))
            return f"Download complete: {total} bytes from {Path(image.path).name}"

        self._run_transfer("Download", fn)

    def upload(
        self,
        path: str,
        address: int,
        size: int,
        block_size: int = 0,
        dfi: int = 0,
        width: int | None = None,
    ) -> None:
        """Read memory out of the ECU into a file: 0x35, 0x36, then 0x37.

        What comes back is written exactly as it arrived, at the address it was
        asked for, in whichever format the chosen name asks for.
        """
        fmt = DataFormatIdentifier(compression=(dfi >> 4) & 0xF, encryption=dfi & 0xF)

        def fn(c: Client) -> str:
            r = c.request_upload(self._memory(address, size, width), dfi=fmt)
            block = self._block_size(r.service_data.max_length, block_size)
            self.result.emit(f"RequestUpload {address:08X}: {size} bytes in blocks of {block}")
            data = self._receive_blocks(c, size, "Upload")
            c.request_transfer_exit()
            written = write_image(path, address, data)
            return f"Upload complete: {len(data)} bytes to {Path(path).name} ({written})"

        self._run_transfer("Upload", fn)

    def file_transfer(
        self,
        mode: int,
        ecu_path: str,
        local_path: str = "",
        block_size: int = 0,
        dfi: int = 0,
    ) -> None:
        """RequestFileTransfer (0x38): the ECU's own filesystem, addressed by name.

        No memory address anywhere.  The path on the ECU says what is being
        written or read, so a raw binary needs nothing else to place it.
        """
        name = FILE_MODES.get(mode, str(mode)).capitalize()
        fmt = DataFormatIdentifier(compression=(dfi >> 4) & 0xF, encryption=dfi & 0xF)

        def fn(c: Client) -> str:
            payload = Path(local_path).read_bytes() if mode in FILE_MODES_SENDING else b""
            size_arg = Filesize(uncompressed=len(payload)) if mode in FILE_MODES_SENDING else None
            r = c.request_file_transfer(moop=mode, path=ecu_path, dfi=fmt, filesize=size_arg)
            data = r.service_data
            if mode == 2:  # delete: there is nothing to transfer
                return f"Deleted {ecu_path}"

            size = self._block_size(data.max_length, block_size)
            if mode in FILE_MODES_SENDING:
                # Resume is the whole point of mode 6: the ECU says how much of
                # the file it already has, and the rest is sent from there.
                start = (data.fileposition or 0) if mode == 6 else 0
                if start:
                    self.result.emit(f"Resuming {ecu_path} at {start} of {len(payload)} bytes")
                rest = payload[start:]
                self.result.emit(f"{name} {ecu_path}: {len(rest)} bytes in blocks of {size}")
                self._send_blocks(c, rest, size, name, start, len(payload))
                c.request_transfer_exit()
                return f"{name} complete: {len(payload)} bytes to {ecu_path}"

            expected = (
                data.dirinfo_length
                if mode == 5
                else (data.filesize.uncompressed if data.filesize else 0)
            ) or 0
            self.result.emit(f"{name} {ecu_path}: {expected} bytes in blocks of {size}")
            content = self._receive_blocks(c, expected, name)
            c.request_transfer_exit()
            if not local_path:  # a directory listing is read here, not saved
                return f"{ecu_path}:\n{as_text(content)}"
            Path(local_path).write_bytes(content)
            return f"{name} complete: {len(content)} bytes to {Path(local_path).name}"

        self._run_transfer(name, fn)


# --- helpers -----------------------------------------------------------------------------
def parse_bytes(text: str) -> bytes:
    """Hex bytes if the text is valid hex, otherwise ASCII."""
    cleaned = text.replace(",", " ").replace("0x", "").strip()
    try:
        return bytes.fromhex(cleaned)
    except ValueError:
        return text.encode("ascii", "replace")


def as_text(data: bytes) -> str:
    """A directory listing as the ECU wrote it, or hex if it is not text.

    ISO 14229-1 Annex G gives directory information as XML, so printing it a
    byte at a time would be hiding the answer rather than giving it.
    """
    try:
        text = data.decode("utf-8").strip()
    except UnicodeDecodeError:
        return describe_bytes(data)
    if text and all(c.isprintable() or c in " \r\n\t" for c in text):
        return text
    return describe_bytes(data)


def describe_bytes(data: bytes) -> str:
    if not data:
        return "(empty)"
    text = data.hex(" ").upper()
    if len(data) >= 2 and all(32 <= b < 127 for b in data):
        text += f'  "{data.decode("ascii")}"'
    return text


def dtc_code(dtc: int) -> str:
    """P0123-style code from a 3-byte DTC number."""
    letter = "PCBU"[(dtc >> 22) & 3]
    return (
        f"{letter}{(dtc >> 20) & 3}{(dtc >> 16) & 0xF:X}{(dtc >> 8) & 0xFF:02X}"[:5]
        + f"-{dtc & 0xFF:02X}"
    )


def status_flags(status: udsoncan.Dtc.Status) -> str:
    names = []
    for attr, short in (
        ("test_failed", "testFailed"),
        ("test_failed_this_operation_cycle", "failedThisCycle"),
        ("pending", "pending"),
        ("confirmed", "confirmed"),
        ("test_not_completed_since_last_clear", "notCompletedSinceClear"),
        ("test_failed_since_last_clear", "failedSinceClear"),
        ("test_not_completed_this_operation_cycle", "notCompletedThisCycle"),
        ("warning_indicator_requested", "warningIndicator"),
    ):
        if getattr(status, attr, False):
            names.append(short)
    return "[" + ", ".join(names) + "]" if names else ""
