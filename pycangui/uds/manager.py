# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""UDS client side. Requests run on a worker thread (they block on the ECU's
reply); every outcome comes back as a ``result`` signal with a readable line
for the pane, so the pane never touches udsoncan directly."""

from __future__ import annotations

import struct
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import can
import udsoncan
from PySide6.QtCore import QObject, QTimer, Signal, Slot
from udsoncan import (
    Baudrate,
    CommunicationType,
    DataFormatIdentifier,
    Filesize,
    MemoryLocation,
    Request,
    Response,
    services,
)
from udsoncan.client import Client
from udsoncan.connections import BaseConnection
from udsoncan.exceptions import NegativeResponseException, TimeoutException

from pycangui.core import seedkey
from pycangui.core.bus import BusManager, Frame
from pycangui.core.components import COMPONENTS
from pycangui.core.context import Context
from pycangui.core.hooks import Hooks
from pycangui.core.worker import Worker
from pycangui.uds import NO_ID, TIMING_AT_LEAST, TIMING_FORCED, UdsConfig, functional
from pycangui.uds.dtc import BY_SUBFUNCTION, DEFAULT_STANDARD, DTC, EXTENDED, SNAPSHOT
from pycangui.uds.images import Image, ImageError, Segment
from pycangui.uds.images import write as write_image
from pycangui.uds.standard import SESSIONS, memory_record, security_pair, seed_subfunction
from pycangui.uds.transport import FrameRefusedError, IsoTpTransport

DEFAULT_COMPONENT = "can-isotp"


class TransferCancelledError(Exception):
    """Asked to stop, or the ECU stopped first."""


class RefusedError(Exception):
    """A hook said this image must not be written to this ECU."""


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

#: RoutineControl identifiers that go with a memory download. Erase is the
#: one ISO 14229-1 names (Annex F); what to run afterwards to have the ECU
#: check what it was given is manufacturer specific, and 0x0202 is only the
#: number the HIS/AUTOSAR flash bootloaders settled on.
ERASE_MEMORY = 0xFF00
CHECK_MEMORY = 0x0202

RESETS = {1: "hard reset", 2: "key off/on", 3: "soft reset", 4: "enable rapid power shutdown"}
#: CommunicationControl (0x28) control types, as ISO 14229-1 numbers them.
#: 4 and 5 add enhanced address information, which a request from here has
#: no way to give, so they are left out.
COMM_CONTROLS = {
    0: "enable Rx and Tx",
    1: "enable Rx, disable Tx",
    2: "disable Rx, enable Tx",
    3: "disable Rx and Tx",
}
#: Which messages CommunicationControl acts on: the communicationType bits.
COMM_MESSAGES = {1: "normal messages", 2: "network management", 3: "both"}
#: The bitrates LinkControl can name by a fixed identifier on CAN, the ones
#: ISO 14229-1 lists; anything else would need a specific-rate request.
LINK_BITRATES = (125_000, 250_000, 500_000, 1_000_000)
#: Negative responses that mean an ECU does not offer a report at all, as
#: against one it offers and refused this time. Read all says so in one line
#: and goes on to the next report.
NOT_OFFERED = {0x11, 0x12, 0x7E, 0x7F}
#: The reports Read all adds after the everyday ones: which failed first and
#: last, what is building up, and what a clear cannot remove.
READ_ALL_EXTRAS = (0x0B, 0x0C, 0x0D, 0x0E, 0x14, 0x15)


#: How long to leave the channel alone after telling ECUs to change rate,
#: before closing it to reopen at the new one. The request has no answer to
#: wait for -- it is suppressed, because an answer would come at the new rate
#: -- and sending only hands the frame to the adapter: closing straight away
#: can drop it before it has been on the wire, and an ECU needs a moment to
#: act on it. On the worker, so the window does not wait.
LINK_SETTLE_S = 0.1


class UdsManager(QObject):
    result = Signal(str)  # one readable line per request outcome
    opened = Signal(bool)  # client open state changed
    did_value = Signal(int, bytes)  # did, raw data (for scripts / future signal hub use)
    progress = Signal(str, int, int)  # what, bytes done, bytes expected
    transferring = Signal(bool)  # a transfer started or finished
    #: Tester present turned itself off, and why: the adapter would not send it.
    tester_present_stopped = Signal(str)
    #: The session closed and opened again underneath the pane -- following a
    #: baud rate change -- with tester present to be carried on.
    tester_present_resumed = Signal()
    #: The channel's rate now, and the rate it came from, or 0 when it is back
    #: on its own: what the pane needs to offer the way back.
    rate_moved = Signal(int, int)

    def __init__(self, bus: BusManager, hooks: Hooks, ctx: Context) -> None:
        super().__init__()
        self._bus = bus
        self._hooks = hooks
        self._ctx = ctx
        self.config = UdsConfig()
        self.client: Client | None = None
        self.component_name = ctx.settings.get("components.isotp", DEFAULT_COMPONENT)
        self._transport: IsoTpTransport | None = None
        self._worker = Worker()  # starts itself the first time it is used
        self._cancel = threading.Event()
        self._busy = False
        #: What a transfer is waiting on, so a timeout can say which request
        #: it was: "Download: timeout" alone does not say where it stopped.
        self._step = ""
        #: How long each DID's value is, learned by reading it, for splitting
        #: snapshots -- which carry DIDs and not their lengths. Kept for the
        #: session: None for one the ECU would not read, so it is not asked again.
        self._did_sizes: dict[int, int | None] = {}
        #: (P2, P2*) as the ECU last gave them in a session response, in
        #: seconds, or None before it has. Kept apart from what is in use, so
        #: changing the timing choice can go back to them.
        self._ecu_timing: tuple[float, float] | None = None
        self.standard_version = DEFAULT_STANDARD
        self._tp_timer = QTimer(self, timeout=self._tester_present_tick)
        #: The channel's own bitrate while a LinkControl has it somewhere else,
        #: so the way back knows where back is. None when it is on its own.
        self._own_rate: int | None = None
        #: Set while pycangui itself closes and reopens the channel to follow
        #: a rate change, so that disconnect is not taken for the user's.
        self._following = False
        bus.disconnected.connect(self._on_disconnected)

    # --- lifecycle -------------------------------------------------------------
    @property
    def bus(self) -> BusManager:
        """The channel underneath, so the pane can follow what it opened as."""
        return self._bus

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
            self._transport = COMPONENTS.create(
                "isotp", self.component_name, self._bus, config, self._ctx
            )
        except Exception as exc:
            self.result.emit(f"UDS transport {self.component_name!r} failed: {exc}")
            return
        conn = _TransportConnection(self._transport)
        cfg = dict(udsoncan.configs.default_client_config)
        cfg.update(
            {
                "p2_timeout": config.p2_timeout_s,
                "p2_star_timeout": config.p2_star_timeout_s,
                # No cap on the whole request. Each 0x78 "response pending"
                # starts P2* again, and ISO 14229 has the client wait for as
                # long as they keep coming: an erase of a large flash keeps an
                # ECU busy for many times P2*. A cap of P2* + 1 s cut one off
                # at 6 s, a second before its positive response.
                "request_timeout": None,
                "security_algo": self._security_algo,
                "exception_on_negative_response": True,
                "exception_on_unexpected_response": False,
                "standard_version": self.standard_version,
            }
        )
        self.client = Client(conn, config=cfg)
        self._ecu_timing = None
        self._did_sizes = {}
        self._apply_timing(self.client)
        self.client.open()
        ids = f"tx {config.tx_id:X} rx {config.rx_id:X}"
        ext = " (29-bit)" if config.extended_id else ""
        self.result.emit(f"UDS open [{self.component_name}]: {ids}{ext}")
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

    @Slot()
    def _on_disconnected(self) -> None:
        self.close()
        if not self._following and self._own_rate is not None:
            # Disconnected by somebody, not by following the ECUs: the next
            # connect is at the channel's own rate, so there is no way back
            # left to offer.
            self._own_rate = None
            self.rate_moved.emit(0, 0)

    def shutdown(self) -> None:
        self.close()  # stops the ISO-TP threads before the bus disappears
        self._worker.stop()
        try:
            self._bus.disconnected.disconnect(self._on_disconnected)
        except (RuntimeError, TypeError):
            pass

    # --- component --------------------------------------------------------------
    def components(self) -> list[str]:
        return COMPONENTS.names("isotp")

    def set_component(self, name: str) -> None:
        if name == self.component_name:
            return
        was_open = self.is_open
        self.close()
        self.component_name = name
        self._ctx.settings.set("components.isotp", name)
        self.result.emit(f"UDS transport: {name}")
        if was_open:
            self.open(self.config)

    # --- trace labelling -------------------------------------------------------
    def classify(self, frame: Frame) -> str | None:
        """Name the frames at the addresses this pane is set to.

        Whether or not a session is open: the addresses are a statement
        about what is on this bus, and a request sent before anybody pressed
        Connect is still a UDS request. Only those addresses, though -- the
        whole 0x7E0 to 0x7EF range used to be read as UDS wherever it
        appeared, which is a guess on a bus that happens to use those ids
        for something else.
        """
        for can_id, label in (
            (self.config.functional_id, "UDS func"),
            (self.config.tx_id, "UDS req"),
            (self.config.rx_id, "UDS resp"),
        ):
            if can_id != NO_ID and frame.can_id == can_id:
                return label
        return None

    # --- hooks ---------------------------------------------------------------------
    def _security_algo(self, level: int, seed: bytes, params: Any) -> bytes:
        """The hook first, then the seed and key DLL.

        The same order and the same DLL as XCP unlocking, because it is the
        same question asked by a different protocol: an ECU that ships one
        algorithm ships one file. The hook wins because it is this
        workspace's own answer; the DLL is the standard one, so it lives in
        pycangui rather than in a copy of the ctypes for it in everybody's
        hooks file.
        """
        key = self._hooks.call("uds", "security_key", level, seed)
        if key is not None:
            return bytes(key)
        dll = str(self._ctx.settings.get(seedkey.DLL_KEY, "") or "")
        if not dll:
            raise RuntimeError(
                f"no security algorithm for level {level}: choose a seed and key DLL, "
                "or write hooks/uds.py::security_key"
            )
        other = str(self._ctx.settings.get(seedkey.PYTHON_KEY, "") or "")
        return seedkey.key_for(dll, level, seed, other, seedkey.UDS)

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

    def _to_all(self, service: str, label: str, payload: bytes, *, suppress: bool = True) -> bool:
        """Send to every ECU if this service is ticked to go that way. Whether it was.

        Positive answers suppressed unless asked for: most of these are
        about the whole bus, and what is worth hearing is which ECU objected.
        On the pane's worker, behind whatever it has already asked, so a
        functional request never lands in the middle of a physical one.
        """
        if not self.config.goes_to_all(service):
            return False
        if self.client is None:
            self.result.emit(f"{label}: UDS not open")
            return True
        wire = functional.suppressed(payload) if suppress else payload
        config, bus, fd = self.config, self._bus, self._fd()
        p2, p2_star = self.timing_in_use()

        def job() -> str:
            try:
                answers = functional.request(bus, config, wire, p2_s=p2, p2_star_s=p2_star, fd=fd)
            except (can.CanError, ValueError) as exc:
                return f"{label} (all ECUs): {exc}"
            return functional.describe(label, answers, quiet_positive=wire != payload)

        self._worker.submit(job, lambda text, error: self.result.emit(error or text))
        return True

    def _fd(self) -> bool:
        """Whether a frame this pane sends goes out as CAN FD."""
        return bool(self.config.can_fd and self._bus.fd)

    # --- services ----------------------------------------------------------------------
    # --- timing ------------------------------------------------------------------------
    def set_timing(self, timing: str, p2_s: float, p2_star_s: float) -> None:
        """Whose P2 and P2* to wait for, from now on, on an open session too."""
        self.config.timing = timing
        self.config.p2_timeout_s = p2_s
        self.config.p2_star_timeout_s = p2_star_s
        if self.client is not None:
            self._apply_timing(self.client)

    def _apply_timing(self, c: Client) -> None:
        """Put the chosen timing into udsoncan's client.

        udsoncan waits for its session timing whenever it has one, whether or
        not it was told to use the server's, so that is set here outright:
        the ECU's values, the larger of theirs and the tester's, or none at
        all so that the tester's own are what it falls back on.
        """
        cfg = self.config
        c.config["p2_timeout"] = cfg.p2_timeout_s
        c.config["p2_star_timeout"] = cfg.p2_star_timeout_s
        c.config["use_server_timing"] = cfg.timing != TIMING_FORCED
        ecu, now = self._ecu_timing, c.session_timing
        if ecu is None or cfg.timing == TIMING_FORCED:
            now.p2_server_max = now.p2_star_server_max = None
        elif cfg.timing == TIMING_AT_LEAST:
            now.p2_server_max = max(ecu[0], cfg.p2_timeout_s)
            now.p2_star_server_max = max(ecu[1], cfg.p2_star_timeout_s)
        else:
            now.p2_server_max, now.p2_star_server_max = ecu

    def timing_in_use(self) -> tuple[float, float]:
        """(P2, P2*) in seconds that a request is waiting for now."""
        if self.client is None:
            return self.config.p2_timeout_s, self.config.p2_star_timeout_s
        now = self.client.session_timing
        return (
            self.client.config["p2_timeout"] if now.p2_server_max is None else now.p2_server_max,
            self.client.config["p2_star_timeout"]
            if now.p2_star_server_max is None
            else now.p2_star_server_max,
        )

    def change_session(self, session: int) -> None:
        if self._to_all("session", "DiagnosticSessionControl", bytes([0x10, session])):
            return

        def fn(c: Client) -> str:
            r = c.change_session(session)
            timing = ""
            sd = r.service_data
            if sd.p2_server_max is not None:
                self._ecu_timing = (sd.p2_server_max, sd.p2_star_server_max)
                self._apply_timing(c)
                p2, p2s = sd.p2_server_max * 1000, sd.p2_star_server_max * 1000
                timing = f"P2 {p2:.0f} ms, P2* {p2s:.0f} ms"
                used = tuple(round(t * 1000) for t in self.timing_in_use())
                if used != (round(p2), round(p2s)):
                    timing += f"; waiting {used[0]} ms and {used[1]} ms ({self.config.timing})"
                timing = f" ({timing})"
            return f"Session -> {SESSIONS.get(session, session)}{timing}"

        self._run("DiagnosticSessionControl", fn)

    def unlock(self, level: int) -> None:
        """Unlock a security level, counting from 1.

        A level is a pair of sub-functions: an odd one asking for the seed
        and the even one after it carrying the key, so level 2 is 03 and
        04. The pane holds the level and this works the pair out, which is
        the way round that cannot be misread -- the number on screen used
        to be the request sub-function under a label saying "level", and
        udsoncan rounds an even one down without saying so, so asking for
        02 quietly unlocked level 1.

        The hook and the seed and key DLL are handed the request
        sub-function rather than the level, because that is what an ECU's
        own algorithm is written against.
        """
        seed_sub = seed_subfunction(level)

        def fn(c: Client) -> str:
            c.unlock_security_access(seed_sub)
            return f"Security level {level} ({security_pair(seed_sub)}): unlocked"

        self._run("SecurityAccess", fn)

    def tester_present(self) -> None:
        if self._to_all("tester", "TesterPresent", bytes([0x3E, 0x00])):
            return
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

        # quiet: only failures are reported
        client, config, bus, fd = self.client, self.config, self._bus, self._fd()
        everyone = config.goes_to_all("tester")

        def job() -> str | Exception:
            try:
                if everyone:
                    # 3E 80: every ECU's session kept, and none of them answering.
                    functional.send_only(bus, config, bytes([0x3E, 0x80]), fd=fd)
                else:
                    client.tester_present()
                return ""
            except (NegativeResponseException, TimeoutException) as exc:
                return f"TesterPresent: {exc}"
            except (FrameRefusedError, can.CanError) as exc:
                return exc

        def done(outcome, error: str | None) -> None:
            # A frame the adapter refuses is a bus nothing is acknowledging.
            # Carrying on would fill its queue again every couple of seconds,
            # so it stops, and says so, rather than going on failing quietly.
            if isinstance(outcome, FrameRefusedError | can.CanError):
                self._tp_timer.stop()
                self.tester_present_stopped.emit(f"Tester present stopped: {outcome}")
            elif outcome or error:
                self.result.emit(outcome or error)

        self._worker.submit(job, done)

    def ecu_reset(self, reset_type: int) -> None:
        if self._to_all("reset", "ECUReset", bytes([0x11, reset_type])):
            return

        def fn(c: Client) -> str:
            c.ecu_reset(reset_type)
            return f"ECUReset ({RESETS.get(reset_type, reset_type)}) OK"

        self._run("ECUReset", fn)

    def communication_control(self, control: int, messages: int) -> None:
        """CommunicationControl (0x28): what the ECU sends and listens to.

        Usually to quieten the rest of the bus before a flash -- disable Tx of
        normal messages -- and to give it back afterwards. The subnet is 0,
        the one every tester uses: the network the request arrives on.
        """
        # The communication type byte: the message kinds in its low two bits,
        # the subnet (0) above them.
        if self._to_all("comm", "CommunicationControl", bytes([0x28, control, messages & 0x03])):
            return

        def fn(c: Client) -> str:
            kind = CommunicationType(
                subnet=0, normal_msg=bool(messages & 1), network_management_msg=bool(messages & 2)
            )
            c.communication_control(control, kind)
            return (
                f"CommunicationControl: {COMM_CONTROLS.get(control, control)}, "
                f"{COMM_MESSAGES.get(messages, messages)}"
            )

        self._run("CommunicationControl", fn)

    def change_bitrate(self, bitrate: int) -> None:
        """LinkControl (0x87): move the ECUs to another bitrate, and follow them.

        Verify first, then transition, as ISO 14229-1 has it: each ECU says
        whether it can before anything changes. To every ECU when the service
        is ticked to go that way, which is the usual case -- an ECU left
        behind at the old rate sees nothing but errors from the rest -- and
        then the transition goes out with its answer suppressed, since after
        it nobody at the old rate is listening. To the one ECU otherwise.

        Then the channel follows: it is closed and opened again at the new
        rate, the session reopened, and tester present carried on at once,
        because an ECU whose session times out falls back to its own rate.
        Not on a channel whose rate pycangui does not set -- socketcan's is
        the kernel's -- where nothing is sent at all.
        """
        label = "LinkControl"
        if self.client is None:
            self.result.emit(f"{label}: UDS not open")
            return
        if not self._bus.sets_bitrate:
            self.result.emit(
                f"{label}: pycangui does not set this channel's bitrate ({self._bus.interface} "
                "has it from outside), so it could not follow the ECUs to another one. "
                "Nothing was sent."
            )
            return
        kbit = f"{bitrate // 1000} kbit/s"
        baud = Baudrate(bitrate, Baudrate.Type.Fixed)
        client, config, bus, fd = self.client, self.config, self._bus, self._fd()
        everyone = config.goes_to_all("link")
        p2, p2_star = self.timing_in_use()

        def job() -> tuple[bool, str]:
            try:
                if not everyone:
                    client.link_control(1, baud)
                    client.link_control(3)
                    time.sleep(LINK_SETTLE_S)
                    return True, f"{label}: the ECU is changing to {kbit}"
                verify = bytes([0x87, 0x01]) + baud.get_bytes()
                answers = functional.request(bus, config, verify, p2_s=p2, p2_star_s=p2_star, fd=fd)
                said = functional.describe(f"{label} verify {kbit}", answers, False)
                if any(a.objected for a in answers) or not any(a.positive for a in answers):
                    return False, f"{said}. Nothing was changed."
                functional.send_only(bus, config, functional.suppressed(bytes([0x87, 0x03])), fd=fd)
                time.sleep(LINK_SETTLE_S)  # see LINK_SETTLE_S
                return True, f"{said}; told to change to {kbit}"
            except NegativeResponseException as exc:
                return False, f"{label}: NRC 0x{exc.response.code:02X} {exc.response.code_name}"
            except TimeoutException:
                return False, f"{label}: timeout (no response). Nothing was changed."
            except (can.CanError, FrameRefusedError) as exc:
                return False, f"{label}: {exc}"

        def done(outcome, error: str | None) -> None:
            if error:
                self.result.emit(f"{label}: {error}")
                return
            moved, text = outcome
            self.result.emit(text)
            if moved:
                if self._own_rate is None:
                    self._own_rate = self._bus.bitrate
                self._follow(bitrate)

        self._worker.submit(job, done)

    def back_to_own_rate(self) -> None:
        """End the session, which takes the ECUs back to their own rate, and follow.

        ISO 14229-1 gives no request to undo LinkControl: the new rate lasts
        for the session it was set in. So this returns to the default session
        -- to every ECU if the change went to every ECU -- and reopens the
        channel at the rate it had before.
        """
        own = self._own_rate
        if own is None or self.client is None:
            return
        client, config, bus, fd = self.client, self.config, self._bus, self._fd()
        everyone = config.goes_to_all("link")

        def job() -> str:
            try:
                if everyone:
                    functional.send_only(bus, config, bytes([0x10, 0x81]), fd=fd)
                    time.sleep(LINK_SETTLE_S)
                    return "DiagnosticSessionControl (all ECUs): back to the default session"
                client.change_session(1)
                return "Session -> default"
            except (NegativeResponseException, TimeoutException, can.CanError) as exc:
                return f"DiagnosticSessionControl: {exc}; following back anyway"

        def done(text, error: str | None) -> None:
            self.result.emit(error or text)
            self._follow(own)

        self._worker.submit(job, done)

    def _follow(self, bitrate: int) -> None:
        """Reopen the channel at ``bitrate``, the session with it, and tester present.

        On the GUI thread: the channel is closed and opened like any other,
        so everything on it -- the other panes, a recording -- sees a
        disconnect and a connect.
        """
        keep_tp = self._tp_timer.isActive()
        config = self.config
        self._following = True
        try:
            reopened = self._bus.reconnect_at(bitrate)
        finally:
            self._following = False
        if not reopened:
            self._own_rate = None
            self.result.emit(
                f"Could not reopen the channel at {bitrate // 1000} kbit/s; it is disconnected."
            )
            self.rate_moved.emit(0, 0)
            return
        if self._own_rate == bitrate:
            self._own_rate = None  # home again
        self.result.emit(f"Channel reopened at {bitrate // 1000} kbit/s")
        self.open(config)
        if keep_tp and self.client is not None:
            self.set_tester_present(True)
            self.tester_present_resumed.emit()  # the pane's tick box, which closing cleared
            self._tester_present_tick()  # now, not in two seconds: S3 is running
        self.rate_moved.emit(bitrate, self._own_rate or 0)

    def did_label(self, did: int) -> str:
        """ "F190 (VIN)" -- the number, and what the identifier is called."""
        name = self._hooks.call("uds", "did_label", did)
        return f"{did:04X} ({name})" if name else f"{did:04X}"

    def sessions(self) -> dict[int, str]:
        """The sessions the pane offers: ISO's four, plus whatever the hook adds."""
        offered = self._hooks.call("uds", "sessions")
        return dict(offered) if offered else dict(SESSIONS)

    def did_choices(self) -> dict[int, str]:
        offered = self._hooks.call("uds", "did_choices")
        return dict(offered) if offered else {}

    def did_description(self, did: int) -> str:
        return self._hooks.call("uds", "did_description", did) or ""

    def routine_choices(self) -> dict[int, str]:
        offered = self._hooks.call("uds", "routine_choices")
        return dict(offered) if offered else {}

    def routine_description(self, routine_id: int) -> str:
        return self._hooks.call("uds", "routine_description", routine_id) or ""

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
        """The everyday one: every DTC matching a status mask."""
        self.read_dtc_information(0x02, status_mask=status_mask)

    def read_dtc_information(self, subfunction: int, **params: int) -> None:
        """Any of the reports ReadDTCInformation (0x19) offers.

        `params` are the ones that report takes, named as udsoncan names them
        -- see pycangui.uds.dtc, which is also what the pane uses to decide
        which boxes to leave enabled.
        """
        report = BY_SUBFUNCTION.get(subfunction)
        name = report.name if report else f"subfunction 0x{subfunction:02X}"

        def fn(c: Client) -> str:
            if subfunction == 0x06:
                record = params.get(EXTENDED, 0xFF)
                lines = self._extended_data(c, params[DTC], record)
                return "\n".join(
                    [f"{name} ({dtc_code(params[DTC])}, record {record:02X}):", *lines]
                )
            if subfunction == 0x04:
                record = params.get(SNAPSHOT, 0xFF)
                lines = self._snapshots(c, params[DTC], record)
                return "\n".join(
                    [f"{name} ({dtc_code(params[DTC])}, record {record:02X}):", *lines]
                )
            r = c.read_dtc_information(subfunction, **params)
            return self._describe_dtc_report(name, r.service_data, params)

        def job(client: Client) -> str:
            try:
                return fn(client)
            except NotImplementedError as exc:
                # udsoncan refuses the mirror memory reports unless the client
                # is told to encode to an older edition. Its own message says
                # so, and is better than anything invented here.
                return f"{name}: {exc}"

        self._run(f"ReadDTCInformation 0x{subfunction:02X}", job)

    def _describe_dtc_report(self, name: str, data: Any, asked: dict | None = None) -> str:
        """One report, said in whatever terms it answered in.

        The reports do not share a shape: some come back with a count, some
        with a list, some with a record and nothing else. Printing only the
        fields that are actually set is what keeps a count from being reported
        as "0 DTCs".
        """
        # What was asked for, on the same line as what came back: two
        # reports of the same name with different masks are otherwise
        # indistinguishable once they are in the log.
        wanted = ", ".join(
            f"{key.replace('_', ' ')} 0x{value:06X}"
            if key == "dtc"
            else f"{key.replace('_', ' ')} 0x{value:02X}"
            for key, value in (asked or {}).items()
        )
        lines = [f"{name}" + (f" ({wanted})" if wanted else "") + ":"]
        if (count := getattr(data, "dtc_count", None)) is not None:
            lines[0] += f" {count} DTC(s)"
        if (available := getattr(data, "status_availability", None)) is not None:
            byte = available.get_byte_as_int()
            lines.append(f"  status bits the ECU supports: 0x{byte:02X} {status_flags(available)}")
        if (memory := getattr(data, "memory_selection_echo", None)) is not None:
            lines.append(f"  memory selection {memory:02X}")
        if (group := getattr(data, "functional_group_id", None)) is not None:
            lines.append(f"  functional group {group:02X}")
        for record in getattr(data, "extended_data", None) or []:
            lines.append(f"  extended data: {describe_bytes(bytes(record))}")

        dtcs = getattr(data, "dtcs", None) or []
        for d in dtcs:
            lines.extend(self._describe_dtc(d))
        if len(lines) == 1 and not dtcs:
            lines[0] += " nothing reported"
        return "\n".join(lines)

    def _describe_dtc(self, d: Any) -> list[str]:
        desc = self._hooks.call("uds", "dtc_description", d.id)
        status = f" status 0x{d.status.get_byte_as_int():02X} {status_flags(d.status)}".rstrip()
        lines = [f"  {dtc_code(d.id)} ({d.id:06X}){status}{' - ' + desc if desc else ''}".rstrip()]
        if getattr(d, "severity", None) is not None and d.severity.get_byte_as_int():
            lines.append(f"    severity 0x{d.severity.get_byte_as_int():02X}")
        if getattr(d, "fault_counter", None) is not None:
            lines.append(f"    fault detection counter {d.fault_counter}")
        for snapshot in getattr(d, "snapshots", None) or []:
            lines.append(f"    snapshot {_snapshot_text(snapshot)}")
        for record in getattr(d, "extended_data", None) or []:
            lines.append(f"    extended data {_record_text(record)}")
        return lines

    # --- the reports whose records the ECU sizes -----------------------------------
    def _dtc_records(self, c: Client, subfunction: int, dtc: int, record: int) -> bytes:
        """The records of a 0x04 or 0x06 answer, after the DTC and its status.

        Asked for and read here rather than through udsoncan, which will only
        split them if it is told every record's size beforehand, and fails
        otherwise.
        """
        request = Request(
            services.ReadDTCInformation,
            subfunction=subfunction,
            data=dtc.to_bytes(3, "big") + bytes([record]),
        )
        answer = bytes(c.send_request(request).data)
        return answer[5:]  # the sub-function echo, the DTC and its status

    def _extended_data(self, c: Client, dtc: int, record: int = 0xFF) -> list[str]:
        """One line per extended data record, named where hooks/uds.py names it."""
        data = self._dtc_records(c, 0x06, dtc, record)
        return split_extended(
            data,
            lambda number: self._hooks.call("uds", "extended_data_record", number),
            single=record != 0xFF,
        )

    def _snapshots(self, c: Client, dtc: int, record: int = 0xFF) -> list[str]:
        """Each snapshot record, with every DID in it named and decoded."""
        data = self._dtc_records(c, 0x04, dtc, record)
        # One record asked for is already named by whoever asked.
        return split_snapshots(
            data, lambda did: self._did_size(c, did), self._did_text, headed=record == 0xFF
        )

    def _did_size(self, c: Client, did: int) -> int | None:
        """How long a DID's value is: hooks/uds.py::did_size, else read it once.

        A snapshot holds DIDs, the same ones ReadDataByIdentifier reads, but
        not their lengths. The hook is somebody's knowledge of the ECU and
        costs nothing to ask; failing it, the answer to reading the DID has
        the length in it -- if the ECU will read that DID in this session.
        """
        if did not in self._did_sizes:
            size = self._hooks.call("uds", "did_size", did)
            if size is not None:
                self._did_sizes[did] = size
                return size
            try:
                answer = c.send_request(
                    Request(services.ReadDataByIdentifier, data=struct.pack(">H", did))
                )
                if bytes(answer.data[:2]) == struct.pack(">H", did):
                    size = len(answer.data) - 2
            except (NegativeResponseException, TimeoutException):
                pass
            self._did_sizes[did] = size
        return self._did_sizes[did]

    def _did_text(self, did: int, data: bytes) -> str:
        text = self._hooks.call("uds", "did_decode", did, data)
        return f"{self.did_label(did)} = {text if text is not None else describe_bytes(data)}"

    def read_all_dtcs(self, status_mask: int = 0xFF, supported: bool = False) -> None:
        """Everything the ECU holds about its faults, as one report.

        How many and which DTCs match the status mask, every extended data
        record of each, its severity, then which snapshots there are and each
        of them, then the first and most recent failed and confirmed DTCs,
        the fault detection counters and the permanent ones -- and, asked for,
        every DTC the ECU supports. Each part goes to the log as it arrives.
        A report the ECU does not offer is one line, and the rest carries on.
        """

        def section(title: str, lines: list[str]) -> None:
            self.result.emit("\n".join([title, *lines]) if lines else title)

        def report(c: Client, subfunction: int, **params: int) -> None:
            name = BY_SUBFUNCTION[subfunction].name
            try:
                r = c.read_dtc_information(subfunction, **params)
            except NegativeResponseException as exc:
                section(_refused(name, exc), [])
                return
            if subfunction == 0x14:
                # A counter per DTC and no status: the status the list would
                # otherwise print is a zero the ECU never sent.
                counted = r.service_data.dtcs or []
                section(
                    f"{name}: {len(counted)} DTC(s)",
                    [f"  {dtc_code(d.id)} ({d.id:06X}) counter {d.fault_counter}" for d in counted],
                )
                return
            # Which status bits the ECU supports was said with the list, and
            # is the same answer under every report after it.
            text = self._describe_dtc_report(name, r.service_data)
            kept = [
                line for line in text.splitlines() if "status bits the ECU supports" not in line
            ]
            section("\n".join(kept), [])

        def fn(c: Client) -> str:
            self.result.emit("Read all DTC data")
            try:
                count = c.read_dtc_information(0x01, status_mask=status_mask)
                found = count.service_data.dtc_count
                plural = "" if found == 1 else "s"
                section(f"{found} DTC{plural} match status mask 0x{status_mask:02X}", [])
            except NegativeResponseException as exc:
                section(_refused("Number of DTCs", exc), [])

            try:
                listed = c.read_dtc_information(0x02, status_mask=status_mask).service_data.dtcs
            except NegativeResponseException as exc:
                section(_refused("DTCs by status mask", exc), [])
                listed = []
            extended, severity = True, True
            for index, d in enumerate(listed):
                first, *rest = self._describe_dtc(d)
                lines = list(rest)
                if extended:
                    try:
                        lines += [f"    {line}" for line in self._extended_data(c, d.id)]
                    except NegativeResponseException as exc:
                        extended = exc.response.code not in NOT_OFFERED
                        lines.append(f"    {_refused('extended data', exc)}")
                if severity:
                    try:
                        found = c.read_dtc_information(0x09, dtc=d.id).service_data.dtcs
                        for known in found:
                            byte = known.severity.get_byte_as_int()
                            lines.append(f"    severity 0x{byte:02X}")
                    except NegativeResponseException as exc:
                        severity = exc.response.code not in NOT_OFFERED
                        if severity:
                            lines.append(f"    {_refused('severity', exc)}")
                section(f"Fault {index + 1} of {len(listed)}: {first.strip()}", lines)

            try:
                identified = c.read_dtc_information(0x03).service_data.dtcs
            except NegativeResponseException as exc:
                section(_refused("Snapshot identification", exc), [])
                identified = []
            pairs = [(d.id, s) for d in identified for s in getattr(d, "snapshots", [])]
            section(f"{len(pairs)} snapshot{'' if len(pairs) == 1 else 's'} stored", [])
            for dtc, number in pairs:
                number = getattr(number, "record_number", number)
                title = f"{dtc_code(dtc)} ({dtc:06X}), snapshot record {number:02X}"
                if desc := self._hooks.call("uds", "dtc_description", dtc):
                    title += f" - {desc}"
                try:
                    section(title, [f"  {line}" for line in self._snapshots(c, dtc, number)])
                except NegativeResponseException as exc:
                    section(title, [f"  {_refused('snapshot', exc)}"])

            for subfunction in READ_ALL_EXTRAS:
                report(c, subfunction)
            if supported:
                report(c, 0x0A)
            return "End of DTC data"

        self._run("Read all DTCs", fn)

    def clear_dtcs(self, group: int = 0xFFFFFF) -> None:
        # No sub-function, so no suppressing: each ECU says it has cleared.
        request = bytes([0x14]) + group.to_bytes(3, "big")
        if self._to_all("clear", "ClearDiagnosticInformation", request, suppress=False):
            return

        def fn(c: Client) -> str:
            c.clear_dtc(group)
            return f"DTCs cleared (group {group:06X})"

        self._run("ClearDiagnosticInformation", fn)

    def set_standard(self, year: int) -> None:
        """Which edition of ISO 14229-1 requests are built to.

        udsoncan enforces it: the 2020 edition withdrew the mirror memory
        reports, and it refuses to build one while 2020 is chosen.
        """
        self.standard_version = year
        if self.client is not None:
            self.client.config["standard_version"] = year
        self.result.emit(f"UDS: building requests to ISO 14229-1:{year}")

    def set_dtc_setting(self, on: bool) -> None:
        """ControlDTCSetting (0x85): whether the ECU may record new DTCs.

        Turned off while working on a vehicle, so that pulling a connector
        does not leave a fault behind. The ECU turns it back on by itself when
        the session ends, which is a thing worth remembering when it looks as
        though the setting did not take.
        """
        setting = 1 if on else 2  # ISO 14229-1: on = 1, off = 2
        if self._to_all("dtc_setting", "ControlDTCSetting", bytes([0x85, setting])):
            return

        def fn(c: Client) -> str:
            c.control_dtc_setting(setting)
            return f"DTC setting {'on' if on else 'off'}"

        self._run("ControlDTCSetting", fn)

    def routine(self, control: int, routine_id: int, data: bytes) -> None:
        names = {1: "start", 2: "stop", 3: "result"}

        def fn(c: Client) -> str:
            r = c.routine_control(routine_id, control, data or None)
            status = bytes(r.service_data.routine_status_record or b"")
            verb = names.get(control, control)
            return f"Routine {self.routine_label(routine_id)} {verb}: OK {describe_bytes(status)}"

        self._run(f"RoutineControl {routine_id:04X}", fn)

    def raw(self, payload: bytes) -> None:
        # As typed: a raw request is somebody's exact bytes, suppress bit and all.
        if self._to_all("raw", "Raw", payload, suppress=False):
            return

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

    def background(self, job, done=None) -> None:
        """Run ``job`` off the GUI thread, on this protocol's own worker.

        The UDS counterpart of ``canopen.background`` and for the same
        reason: one worker per protocol keeps requests sequential, and a
        second thread sending requests into one session would have its
        answers matched to the wrong question. A job joins the queue behind
        whatever the pane has already asked for.

        ``done(result, error)`` is called on the GUI thread; without one the
        answer goes to the pane's log.
        """
        self._worker.submit(job, done or self._said)

    def _said(self, result, error: str | None) -> None:
        if error:
            self.result.emit(str(error))
        elif result is not None:
            self.result.emit(str(result))

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
        # Tester present is stopped rather than left ticking. The worker runs
        # one job at a time, so every tick raised during a long transfer would
        # queue behind it and then arrive in a burst once it finished; the
        # transfer is itself enough to keep the session alive.
        resume_tester_present = self._tp_timer.isActive()
        self._tp_timer.stop()
        self.transferring.emit(True)

        def job() -> str:
            self._step = ""
            try:
                return fn(client)
            except TransferCancelledError as exc:
                return f"{label}: cancelled{exc}"
            except RefusedError as exc:
                # Loud, and stated as a refusal rather than as a failure:
                # nothing went wrong, something was prevented.
                return f"{label} REFUSED by hooks/uds.py::before_download: {exc}"
            except ImageError as exc:
                return f"{label}: {exc}"
            except NegativeResponseException as exc:
                r = exc.response
                return f"{label}: NRC 0x{r.code:02X} {r.code_name}"
            except TimeoutException:
                waiting = f" to {self._step}" if self._step else ""
                return f"{label}: timeout (no response{waiting})"
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
        the number zero -- and then refuses the zero it just produced. An
        image that starts at address 0 is an ordinary thing for a bootloader
        to be given, so the floor is put in here.
        """
        return max(8, ((value.bit_length() + 7) // 8) * 8)

    def _memory(self, address: int, size: int, width: int | None) -> MemoryLocation:
        """Where to write, and how wide to say it.

        `width` is in bits, or None for the narrowest that fits. Some
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
        service id and the block sequence counter come out of it first. Those
        two bytes are the usual reason a download runs perfectly until the ECU
        answers 0x31 to the last block.
        """
        if override > 0:
            return override
        return max(1, (reported or 4) - 2)

    def _send_blocks(
        self, c: Client, data: bytes, size: int, label: str, done: int, total: int
    ) -> int:
        """TransferData until the bytes run out. Returns the new running total."""
        sequence = 1  # ISO 14229: the first block is 1, and 0xFF is followed by 0
        blocks = -(-len(data) // size)
        for start in range(0, len(data), size):
            if self._cancel.is_set():
                raise TransferCancelledError(f" after {done} of {total} bytes")
            block = data[start : start + size]
            self._step = f"TransferData block {start // size + 1} of {blocks}"
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
            self._step = f"TransferData after {got} of {expected} bytes"
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
        this routine for the purpose. What goes in the option record is not
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
        self._step = f"the erase of {segment.address:08X}"
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
        self._step = f"routine {routine:04X} over {segment.address:08X}"
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

        One RequestDownload per segment. A file with gaps in it has them for a
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
            # Before a single byte: the one chance to say "that image is not
            # for this controller". Inside the job rather than in front of
            # it, so the hook has the open session to read an identifier
            # with, and before the erase, because an erase that runs against
            # the wrong image has already done the damage.
            if refused := self._hooks.call("uds", "before_download", image, c):
                raise RefusedError(str(refused))
            done = 0
            if erase:
                for segment in image.segments:
                    self._erase(c, segment, width)
            for index, segment in enumerate(image.segments, 1):
                # Said as it goes out, not once it is accepted: a request the
                # ECU never answers should still be in the log as asked.
                which = f" (segment {index} of {count})" if count > 1 else ""
                self.result.emit(
                    f"RequestDownload {segment.address:08X}: {len(segment)} bytes{which}"
                )
                self._step = f"RequestDownload {segment.address:08X}"
                r = c.request_download(self._memory(segment.address, len(segment), width), dfi=fmt)
                size = self._block_size(r.service_data.max_length, block_size)
                address = f"{segment.address:08X}"
                self.result.emit(f"RequestDownload {address}: accepted, blocks of {size}")
                done = self._send_blocks(c, segment.data, size, "Download", done, total)
                self._step = "RequestTransferExit"
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
            self.result.emit(f"RequestUpload {address:08X}: {size} bytes")
            self._step = f"RequestUpload {address:08X}"
            r = c.request_upload(self._memory(address, size, width), dfi=fmt)
            block = self._block_size(r.service_data.max_length, block_size)
            self.result.emit(f"RequestUpload {address:08X}: accepted, blocks of {block}")
            data = self._receive_blocks(c, size, "Upload")
            self._step = "RequestTransferExit"
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

        No memory address anywhere. The path on the ECU says what is being
        written or read, so a raw binary needs nothing else to place it.
        """
        name = FILE_MODES.get(mode, str(mode)).capitalize()
        fmt = DataFormatIdentifier(compression=(dfi >> 4) & 0xF, encryption=dfi & 0xF)

        def fn(c: Client) -> str:
            payload = Path(local_path).read_bytes() if mode in FILE_MODES_SENDING else b""
            size_arg = Filesize(uncompressed=len(payload)) if mode in FILE_MODES_SENDING else None
            self._step = f"RequestFileTransfer {ecu_path}"
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
                self._step = "RequestTransferExit"
                c.request_transfer_exit()
                return f"{name} complete: {len(payload)} bytes to {ecu_path}"

            expected = (
                data.dirinfo_length
                if mode == 5
                else (data.filesize.uncompressed if data.filesize else 0)
            ) or 0
            self.result.emit(f"{name} {ecu_path}: {expected} bytes in blocks of {size}")
            content = self._receive_blocks(c, expected, name)
            self._step = "RequestTransferExit"
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


def _snapshot_text(snapshot: Any) -> str:
    """A snapshot is a record number and either raw bytes or decoded data."""
    number = getattr(snapshot, "record_number", None)
    head = f"{number:02X}" if isinstance(number, int) else "?"
    if data := getattr(snapshot, "raw_data", None):
        return f"{head}: {describe_bytes(bytes(data))}"
    if (data := getattr(snapshot, "data", None)) is not None:
        return f"{head}: {data}"
    return head


def _record_text(record: Any) -> str:
    number = getattr(record, "record_number", None)
    head = f"{number:02X}" if isinstance(number, int) else "?"
    if data := getattr(record, "raw_data", None):
        return f"{head}: {describe_bytes(bytes(data))}"
    return head


def _refused(what: str, exc: NegativeResponseException) -> str:
    """One line for a report the ECU would not give."""
    code = exc.response.code
    if code in NOT_OFFERED:
        return f"{what}: not supported by this ECU (NRC 0x{code:02X})"
    return f"{what}: NRC 0x{code:02X} {exc.response.code_name}"


def split_extended(data: bytes, describe, single: bool = False) -> list[str]:
    """Extended data records, one per line, as far as their sizes are known.

    Each is a record number and then its bytes, run together; ``describe``
    gives (size, name) for a number, or None. The first one it does not know
    ends the splitting, since nothing after it can be found, and the rest is
    shown as it came. ``single`` is an answer to a request for one record,
    which is then the whole of what follows its number, known or not.
    """
    lines: list[str] = []
    at = 0
    while at < len(data):
        number = data[at]
        known = describe(number)
        if known is None:
            rest = data[at + 1 :]
            if single:
                lines.append(f"{number:02X} = {describe_bytes(rest)}")
            else:
                lines.append(
                    f"{number:02X} onwards, not split = {describe_bytes(data[at:])} "
                    "(sizes from EXTENDED_DATA_RECORDS in hooks/uds.py)"
                )
            break
        size, name = known
        lines.append(f"{number:02X} {name} = {describe_bytes(data[at + 1 : at + 1 + size])}")
        at += 1 + size
    return lines or ["nothing recorded"]


def split_snapshots(data: bytes, size_of, text_of, headed: bool = True) -> list[str]:
    """Snapshot records, each DID in them on a line of its own.

    A record is its number, how many DIDs it holds, and then each DID with its
    value. ``size_of`` gives a DID's length, or None; ``text_of`` the line
    for a DID and its value. A DID of unknown length ends the splitting, and
    the rest is shown as it came. ``headed`` puts each record's number on a
    line of its own, for an answer that may hold several.
    """
    lines: list[str] = []
    at = 0
    while at + 2 <= len(data):
        number, count = data[at], data[at + 1]
        indent = "  " if headed else ""
        if headed:
            lines.append(f"record {number:02X}")
        at += 2
        for _ in range(count):
            if at + 2 > len(data):
                break
            did = int.from_bytes(data[at : at + 2], "big")
            size = size_of(did)
            if size is None:
                rest = describe_bytes(data[at + 2 :])
                lines.append(f"{indent}{did:04X} onwards, not split = {rest}")
                return lines
            lines.append(f"{indent}{text_of(did, data[at + 2 : at + 2 + size])}")
            at += 2 + size
    return lines or ["nothing recorded"]


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
