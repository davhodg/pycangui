"""Bus layer: wraps python-can and delivers frames to the GUI thread.

Design notes (Python / Qt idioms used here):

* python-can's ``Bus.recv()`` blocks, so it runs in its own ``QThread``.  Qt
  widgets may only be touched from the main thread, so the reader thread never
  calls into the UI; it emits a *signal* and Qt queues the call onto the main
  thread for us.
* Frames are delivered in batches (a list) rather than one signal per frame.
  At a few thousand frames/s, one queued call per frame would swamp the event
  loop; one call per ~20 ms keeps the UI responsive.
* ``dataclass(slots=True)`` makes a cheap record type -- the Python
  equivalent of a plain C struct.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import can
from PySide6.QtCore import QObject, QThread, Signal, Slot


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

    @property
    def dlc(self) -> int:
        return len(self.data)


class _Reader(QThread):
    """Background thread that pulls frames from python-can."""

    frames = Signal(list)  # list[Frame], emitted every BATCH_PERIOD_S

    BATCH_PERIOD_S = 0.02

    def __init__(self, bus: can.BusABC, channel_name: str, t0: float) -> None:
        super().__init__()
        self._bus = bus
        self._channel = channel_name
        self._t0 = t0
        self._stop = False

    def run(self) -> None:
        batch: list[Frame] = []
        last_emit = time.monotonic()
        while not self._stop:
            msg = self._bus.recv(timeout=self.BATCH_PERIOD_S)
            if msg is not None:
                batch.append(
                    Frame(
                        timestamp=time.monotonic() - self._t0,
                        channel=self._channel,
                        can_id=msg.arbitration_id,
                        extended=msg.is_extended_id,
                        fd=msg.is_fd,
                        rx=msg.is_rx,  # python-can marks our own echoed frames is_rx=False
                        data=bytes(msg.data),
                    )
                )
            now = time.monotonic()
            if batch and (now - last_emit) >= self.BATCH_PERIOD_S:
                self.frames.emit(batch)
                batch = []
                last_emit = now
        if batch:
            self.frames.emit(batch)

    def stop(self) -> None:
        self._stop = True
        self.wait(1000)


class BusManager(QObject):
    """Owns the python-can bus and the reader thread.  Lives in the GUI thread.

    Signals:
        connected(str):   emitted after a successful connect, with a description
        disconnected():   emitted after disconnect
        frames(list):     batches of Frame objects (rx and tx)
        error(str):       human readable error text
    """

    connected = Signal(str)
    disconnected = Signal()
    frames = Signal(list)
    error = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._bus: can.BusABC | None = None
        self._reader: _Reader | None = None
        self._t0 = 0.0

    @property
    def is_connected(self) -> bool:
        return self._bus is not None

    @Slot(str, str, int, bool)
    def connect_bus(self, interface: str, channel: str, bitrate: int, fd: bool) -> None:
        if self._bus is not None:
            self.disconnect_bus()
        kwargs: dict = {"interface": interface, "channel": channel, "receive_own_messages": True}
        if interface != "virtual":
            kwargs["bitrate"] = bitrate
            kwargs["fd"] = fd
        try:
            self._bus = can.Bus(**kwargs)
        except Exception as exc:  # python-can raises a zoo of exception types
            self.error.emit(f"Connect failed: {exc}")
            return
        self._t0 = time.monotonic()
        self._reader = _Reader(self._bus, channel, self._t0)
        self._reader.frames.connect(self.frames)  # signal-to-signal forwarding
        self._reader.start()
        fd_text = " FD" if fd else ""
        self.connected.emit(f"{interface}:{channel} @ {bitrate} bit/s{fd_text}")

    @Slot()
    def disconnect_bus(self) -> None:
        if self._reader is not None:
            self._reader.stop()
            self._reader = None
        if self._bus is not None:
            self._bus.shutdown()
            self._bus = None
            self.disconnected.emit()

    def send(self, can_id: int, data: bytes, *, extended: bool = False, fd: bool = False) -> None:
        if self._bus is None:
            self.error.emit("Not connected")
            return
        msg = can.Message(arbitration_id=can_id, data=data, is_extended_id=extended, is_fd=fd)
        try:
            self._bus.send(msg)
        except can.CanError as exc:
            self.error.emit(f"Send failed: {exc}")


def available_interfaces() -> list[str]:
    """Interfaces python-can knows about, virtual first so dev works without hardware."""
    names = sorted(can.interfaces.VALID_INTERFACES)
    names.remove("virtual")
    return ["virtual", *names]
