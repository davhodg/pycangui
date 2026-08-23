"""UDS client side.  Requests run on a worker thread (they block on the ECU's
reply); every outcome comes back as a ``result`` signal with a readable line
for the pane, so the pane never touches udsoncan directly."""

from __future__ import annotations

import struct
from collections.abc import Callable
from typing import Any

import udsoncan
from PySide6.QtCore import QObject, QTimer, Signal, Slot
from udsoncan import Request, Response, services
from udsoncan.client import Client
from udsoncan.connections import BaseConnection
from udsoncan.exceptions import NegativeResponseException, TimeoutException

from pycangui.core.backends import BACKENDS
from pycangui.core.bus import BusManager, Frame
from pycangui.core.context import Context
from pycangui.core.hooks import Hooks
from pycangui.core.worker import Worker
from pycangui.uds import UdsConfig
from pycangui.uds.transport import IsoTpTransport

DEFAULT_BACKEND = "can-isotp"


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


SESSIONS = {1: "default", 2: "programming", 3: "extended", 4: "safety system"}
RESETS = {1: "hard reset", 2: "key off/on", 3: "soft reset", 4: "enable rapid power shutdown"}


class UdsManager(QObject):
    result = Signal(str)  # one readable line per request outcome
    opened = Signal(bool)  # client open state changed
    did_value = Signal(int, bytes)  # did, raw data (for scripts / future signal hub use)

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

    def read_did(self, did: int) -> None:
        def fn(c: Client) -> str:
            req = Request(services.ReadDataByIdentifier, data=struct.pack(">H", did))
            resp = c.send_request(req)
            data = bytes(resp.data[2:])  # strip echoed DID
            self.did_value.emit(did, data)
            text = self._hooks.call("uds", "did_decode", did, data)
            return f"DID {did:04X} = {text if text is not None else describe_bytes(data)}"

        self._run(f"ReadDID {did:04X}", fn)

    def write_did(self, did: int, text: str) -> None:
        def fn(c: Client) -> str:
            data = self._hooks.call("uds", "did_encode", did, text)
            if data is None:
                data = parse_bytes(text)
            req = Request(services.WriteDataByIdentifier, data=struct.pack(">H", did) + bytes(data))
            c.send_request(req)
            return f"DID {did:04X} written ({len(data)} bytes)"

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
            return f"Routine {routine_id:04X} {verb}: OK {describe_bytes(status)}"

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


# --- helpers -----------------------------------------------------------------------------
def parse_bytes(text: str) -> bytes:
    """Hex bytes if the text is valid hex, otherwise ASCII."""
    cleaned = text.replace(",", " ").replace("0x", "").strip()
    try:
        return bytes.fromhex(cleaned)
    except ValueError:
        return text.encode("ascii", "replace")


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
