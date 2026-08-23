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

    def on_message_received(self, msg: can.Message) -> None:
        frame = Frame(
            timestamp=time.monotonic() - self._t0,
            channel=self._channel,
            can_id=msg.arbitration_id,
            extended=msg.is_extended_id,
            fd=msg.is_fd,
            rx=msg.is_rx,  # python-can marks our own echoed frames is_rx=False
            data=bytes(msg.data),
        )
        with self._lock:
            self._batch.append(frame)

    def drain(self) -> list[Frame]:
        with self._lock:
            batch, self._batch = self._batch, []
        return batch

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

    DRAIN_PERIOD_MS = 20

    def __init__(self, channel_name: str = "CAN", clock_start: float | None = None) -> None:
        super().__init__()
        self.bus: can.BusABC | None = None
        self.notifier: can.Notifier | None = None
        #: Shown in the trace's Ch column; distinguishes one adapter from another.
        self.channel_name = channel_name
        self.description = ""
        self._collector: _Collector | None = None
        #: Channels share a clock so frames from different adapters line up.
        self._t0 = time.monotonic() if clock_start is None else clock_start
        self._shared_clock = clock_start is not None
        self._timer = QTimer(self, interval=self.DRAIN_PERIOD_MS, timeout=self._drain)

    def now(self) -> float:
        """Seconds on the shared clock: what Frame.timestamp is measured against."""
        return time.monotonic() - self._t0

    @property
    def is_connected(self) -> bool:
        return self.bus is not None

    @Slot(str, str, int, bool)
    def connect_bus(self, interface: str, channel: str, bitrate: int, fd: bool) -> None:
        if self.bus is not None:
            self.disconnect_bus()
        kwargs: dict = {"interface": interface, "channel": channel, "receive_own_messages": True}
        if interface != "virtual":
            kwargs["bitrate"] = bitrate
            kwargs["fd"] = fd
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
        fd_text = " FD" if fd else ""
        self.description = f"{interface}:{channel} @ {bitrate} bit/s{fd_text}"
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

    def _drain(self) -> None:
        if self._collector is None:
            return
        batch = self._collector.drain()
        if batch:
            self.frames.emit(batch)
        if self.notifier is not None and self.notifier.exception is not None:
            exc, self.notifier.exception = self.notifier.exception, None
            self.error.emit(f"Bus reader error: {exc}")


def available_interfaces() -> list[str]:
    """Interfaces python-can knows about, virtual first so dev works without hardware."""
    names = sorted(can.interfaces.VALID_INTERFACES)
    names.remove("virtual")
    return ["virtual", *names]
