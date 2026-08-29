"""Bus layer: wraps python-can and delivers frames to the GUI thread.

Design notes (Python / Qt idioms used here):

* python-can's ``Notifier`` owns the receive thread.  It fans every frame out
  to a list of *listeners*; the trace collector is one, and protocol stacks
  (canopen's ``Network``) add their own, so there is exactly one reader per bus.
* Qt widgets may only be touched from the main thread.  The collector only
  appends to a list under a lock; a ``QTimer`` on the main thread drains it
  every 20 ms and emits one batched signal.  Thousands of frames/s become
  ~50 GUI updates/s.
* ``dataclass(slots=True)`` makes a cheap record type -- the Python
  equivalent of a plain C struct.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import can
from PySide6.QtCore import QObject, QTimer, Signal, Slot

from pycangui.core.detect import coerce_channel, summarise


def frame_bits(dlc: int, extended: bool, fd: bool) -> int:
    """Roughly how many bits a frame occupies on the wire.

    Classic CAN: 47 bits of overhead for an 11-bit id, 67 for a 29-bit one,
    plus the data.  Bit stuffing adds up to a fifth more on unlucky payloads,
    so a nominal 20% is included -- bus load is an indication, not a
    measurement, and the exact figure depends on the data itself.  CAN FD with
    bit rate switching sends its data faster than its header, which this does
    not model, so FD loads read high.
    """
    overhead = 67 if extended else 47
    return int((overhead + 8 * dlc) * 1.2)


@dataclass(slots=True)
class Frame:
    """One CAN frame as seen on the bus (both directions)."""

    timestamp: float  # seconds since connect
    channel: str
    can_id: int
    extended: bool
    fd: bool
    rx: bool  # True = received, False = transmitted by us
    data: bytes
    kind: str = ""  # protocol label, filled in by the trace view (hook frame_kind)
    #: An error frame rather than traffic: the controller reporting a
    #: fault on the wire.  Its id carries error flags, not an identifier.
    error: bool = False
    group: str = "Other"  # filter group, from the CAN id (see core.classify)

    @property
    def dlc(self) -> int:
        return len(self.data)


class _Collector(can.Listener):
    """Runs on the Notifier thread; just stores frames for the GUI to drain."""

    def __init__(self, channel: str, t0: float) -> None:
        self._channel = channel
        self._t0 = t0
        self._lock = threading.Lock()
        self._batch: list[Frame] = []
        #: Counted as well as listed, so the log can say when a bus starts
        #: and stops producing them without a line per frame.
        self.errors = 0

    def on_message_received(self, msg: can.Message) -> None:
        frame = self._to_frame(msg)
        with self._lock:
            if frame.error:
                self.errors += 1
            self._batch.append(frame)

    def _to_frame(self, msg: can.Message) -> Frame:
        return Frame(
            timestamp=time.monotonic() - self._t0,
            channel=self._channel,
            can_id=msg.arbitration_id,
            extended=msg.is_extended_id,
            fd=msg.is_fd,
            rx=msg.is_rx,  # python-can marks our own echoed frames is_rx=False
            data=bytes(msg.data),
            error=bool(msg.is_error_frame),
        )

    def drain(self) -> list[Frame]:
        with self._lock:
            batch, self._batch = self._batch, []
        return batch

    def take_errors(self) -> int:
        with self._lock:
            count, self.errors = self.errors, 0
        return count

    def on_error(self, exc: Exception) -> None:
        pass  # reported through Notifier.exception, see BusManager._drain


class BusManager(QObject):
    """Owns the python-can bus and its Notifier.  Lives in the GUI thread.

    Signals:
        connected(str):   emitted after a successful connect, with a description
        disconnected():   emitted just before the bus goes away
        frames(list):     batches of Frame objects (rx and tx)
        error(str):       human readable error text
    """

    connected = Signal(str)
    disconnected = Signal()
    frames = Signal(list)
    error = Signal(str)
    note = Signal(str)  # worth saying, but not a failure

    DRAIN_PERIOD_MS = 20
    LOAD_PERIOD_MS = 500
    #: How long a newly connected bus may stay silent before saying so.  A
    #: wrong bitrate connects perfectly happily and then hears nothing, which
    #: is indistinguishable from a quiet bus unless somebody mentions it.
    QUIET_WARNING_S = 5.0
    #: How long the bus must be free of error frames before saying they stopped.
    ERROR_QUIET_S = 2.0

    def __init__(self, channel_name: str = "CAN", clock_start: float | None = None) -> None:
        super().__init__()
        self.bus: can.BusABC | None = None
        self.notifier: can.Notifier | None = None
        #: Shown in the trace's Ch column; distinguishes one adapter from another.
        self.channel_name = channel_name
        #: python-can interface name while connected, "" otherwise.  Replay asks
        #: for it: putting frames onto "virtual" is harmless, onto anything else
        #: it is real traffic on a real bus.
        self.interface = ""
        #: The backend's channel while connected -- "vcan0", "can0", 0 -- as
        #: opposed to channel_name, which is what the user called this channel.
        self.channel = ""
        self.description = ""
        self.bitrate = 0
        #: Percentage of the bus's capacity used, refreshed every LOAD_PERIOD_MS.
        self.load_percent = 0.0
        self._bits = 0
        self._bits_at = time.monotonic()
        self._connected_at = 0.0
        self._seen_a_frame = False
        self._state = ""
        self._error_frames = 0
        self._erroring = False
        self._errors_at = 0.0
        self._collector: _Collector | None = None
        #: Channels share a clock so frames from different adapters line up.
        self._t0 = time.monotonic() if clock_start is None else clock_start
        self._shared_clock = clock_start is not None
        self._timer = QTimer(self, interval=self.DRAIN_PERIOD_MS, timeout=self._drain)
        self._load_timer = QTimer(self, interval=self.LOAD_PERIOD_MS, timeout=self._update_load)
        self._load_timer.start()

    def now(self) -> float:
        """Seconds on the shared clock: what Frame.timestamp is measured against."""
        return time.monotonic() - self._t0

    @property
    def is_connected(self) -> bool:
        return self.bus is not None

    def connect_bus(
        self,
        interface: str,
        channel: str,
        bitrate: int,
        fd: bool,
        extra: dict | None = None,
    ) -> None:
        """Join a bus.

        ``extra`` is the rest of the configuration the adapter reported when
        it was detected -- an IXXAT's ``unique_hardware_id``, a Vector's
        ``serial``.  Without it a channel number is ambiguous as soon as two
        of the same adapter are plugged in, since each numbers its own
        channels from zero.
        """
        if self.bus is not None:
            self.disconnect_bus()
        # The channel is coerced because the backends disagree about its type
        # and a text box can only produce a string.
        channel_value = coerce_channel(interface, channel)
        kwargs: dict = {
            "interface": interface,
            "channel": channel_value,
            "receive_own_messages": True,
        }
        if interface != "virtual":
            kwargs["bitrate"] = bitrate
            kwargs["fd"] = fd
        # Ours win: bitrate and FD are the user's choice, not the adapter's.
        kwargs.update({k: v for k, v in (extra or {}).items() if k not in kwargs})
        try:
            self.bus = can.Bus(**kwargs)
        except Exception as exc:  # python-can raises a zoo of exception types
            self.error.emit(f"Connect failed: {exc}")
            return
        if not self._shared_clock:
            self._t0 = time.monotonic()
        self._collector = _Collector(self.channel_name, self._t0)
        self.notifier = can.Notifier(self.bus, [self._collector], timeout=0.02)
        self._timer.start()
        self.bitrate = bitrate
        self.interface = interface
        self.channel = str(channel_value)
        self._connected_at = time.monotonic()
        self._seen_a_frame = False
        self._error_frames = 0
        self._state = self._read_state()
        fd_text = " FD" if fd else ""
        # The identifying part of extra belongs in the description: with two
        # adapters attached, "ixxat:0" alone does not say which one.
        identity = summarise(extra or {})
        where = f"{interface}:{channel_value}" + (f" [{identity}]" if identity else "")
        self.description = f"{where} @ {bitrate} bit/s{fd_text}"
        self.connected.emit(self.description)

    @Slot()
    def disconnect_bus(self) -> None:
        if self.bus is None:
            return
        self.disconnected.emit()  # protocol stacks detach their listeners first
        self.bus.stop_all_periodic_tasks()
        self._timer.stop()
        self.notifier.stop(timeout=1.0)
        self._drain()
        self.bus.shutdown()
        self.bus = self.notifier = self._collector = None
        self.description = ""
        self.interface = ""
        self.channel = ""
        self.load_percent = 0.0

    def add_listener(self, listener: can.Listener) -> None:
        """Let a protocol stack see every frame (canopen.Network etc.)."""
        if self.notifier is not None:
            self.notifier.add_listener(listener)

    def remove_listener(self, listener: can.Listener) -> None:
        if self.notifier is not None and listener in self.notifier.listeners:
            self.notifier.remove_listener(listener)

    def send(self, can_id: int, data: bytes, *, extended: bool = False, fd: bool = False) -> None:
        if self.bus is None:
            self.error.emit("Not connected")
            return
        msg = can.Message(arbitration_id=can_id, data=data, is_extended_id=extended, is_fd=fd)
        try:
            self.bus.send(msg)
        except can.CanError as exc:
            self.error.emit(f"Send failed: {exc}")

    def send_periodic(
        self, can_id: int, data: bytes, period_s: float, *, extended: bool = False, fd: bool = False
    ) -> can.broadcastmanager.CyclicSendTaskABC | None:
        """Start a cyclic transmission; hardware-timed where the adapter supports it.

        Returns the task (``task.modify_data(msg)`` / ``task.stop()``), or None
        if not connected.  All tasks are stopped automatically on disconnect.
        """
        if self.bus is None:
            self.error.emit("Not connected")
            return None
        msg = can.Message(arbitration_id=can_id, data=data, is_extended_id=extended, is_fd=fd)
        try:
            return self.bus.send_periodic(msg, period_s)
        except can.CanError as exc:
            self.error.emit(f"Cyclic send failed: {exc}")
            return None

    # --- what the controller is doing ---------------------------------------------
    def _read_state(self) -> str:
        """The controller's state, or "" if the backend does not report one.

        Every backend answers -- python-can's base class returns ACTIVE -- so
        a backend that does not really know simply never appears to change.
        """
        if self.bus is None:
            return ""
        try:
            return str(getattr(self.bus, "state", "")).rsplit(".", 1)[-1]
        except Exception:  # reading it talks to the driver, which can fail
            return ""

    def _report_state(self) -> None:
        """Say when the controller changes state.

        This is the difference between a quiet bus and a broken one.  An
        adapter that has gone bus off -- the wrong bitrate, a shorted line, no
        termination -- stays connected and simply hears nothing, which is
        exactly what an idle bus looks like from the outside.
        """
        if not self.is_connected:
            return
        state = self._read_state()
        if not state or state == self._state:
            return
        was, self._state = self._state, state
        if state.upper() == "ACTIVE":
            self.note.emit(f"{self.channel_name}: bus active")
        else:
            self.note.emit(
                f"{self.channel_name}: bus state {was or 'unknown'} -> {state}.  "
                "The controller is not taking part in traffic; check the bitrate, "
                "the wiring and the termination."
            )

    def _report_error_frames(self, now: float) -> None:
        """Say when error frames start and stop, not that each one happened.

        The frames themselves go to the trace like any others, under their own
        filter group, because that is where you look at frames.  The log gets
        the condition: a bus in trouble produces thousands a second, and a
        line each would bury everything else in it.
        """
        if self._collector is None:
            return
        count = self._collector.take_errors()
        if count:
            self._error_frames += count
            self._errors_at = now
            if not self._erroring:
                self._erroring = True
                self.note.emit(
                    f"{self.channel_name}: error frames on the bus.  The controller is "
                    "rejecting what it sees; check the bitrate, the wiring and the "
                    "termination.  They are listed in the trace under Bus errors."
                )
        elif self._erroring and now - self._errors_at > self.ERROR_QUIET_S:
            self._erroring = False
            self.note.emit(
                f"{self.channel_name}: error frames stopped ({self._error_frames} in total)"
            )

    def _quiet_advice(self) -> str:
        """Why a connected channel might have heard nothing, for this channel.

        A virtual channel has no bitrate, no wiring and no termination, so
        offering those as the likely causes -- which is the right answer for
        an adapter -- is worse than saying nothing.  It carries only what
        pycangui itself puts on it.
        """
        seconds = f"{self.QUIET_WARNING_S:.0f} s"
        if self.interface == "virtual":
            return (
                f"connected to {self.description}, and nothing has been received in "
                f"{seconds}.  A virtual channel is a loopback inside pycangui: it only "
                "carries what pycangui puts on it.  Start Tools > Demo CANopen device, "
                "replay a log onto it, or send something from the Transmit pane."
            )
        return (
            f"connected to {self.description} but nothing has been received in {seconds}.  "
            "If the bus is not idle, the usual cause is the wrong bitrate; wiring and "
            "termination are the others."
        )

    def _update_load(self) -> None:
        now = time.monotonic()
        elapsed = now - self._bits_at
        capacity = self.bitrate * elapsed
        if self.is_connected and capacity > 0:
            self.load_percent = min(100.0, 100.0 * self._bits / capacity)
        else:
            self.load_percent = 0.0
        self._bits = 0
        self._report_state()
        self._report_error_frames(now)
        self._bits_at = now

    def _drain(self) -> None:
        if self._collector is None:
            return
        batch = self._collector.drain()
        if batch:
            self._bits += sum(frame_bits(f.dlc, f.extended, f.fd) for f in batch)
            self._seen_a_frame = True
            self.frames.emit(batch)
        elif (
            not self._seen_a_frame
            and self._connected_at
            and time.monotonic() - self._connected_at > self.QUIET_WARNING_S
        ):
            # Said once: after this the flag stops the check, whether or not a
            # frame ever turns up.
            self._seen_a_frame = True
            self.note.emit(f"{self.channel_name}: {self._quiet_advice()}")
        if self.notifier is not None and self.notifier.exception is not None:
            exc, self.notifier.exception = self.notifier.exception, None
            self.error.emit(f"Bus reader error: {exc}")


def available_interfaces() -> list[str]:
    """Interfaces python-can knows about, virtual first so dev works without hardware."""
    names = sorted(can.interfaces.VALID_INTERFACES)
    names.remove("virtual")
    return ["virtual", *names]
