"""Several CAN channels at once.

A *channel* is one CAN bus: a second port on a multi-channel adapter, or a
second adapter entirely.  Two objects share the work:

* :class:`Channels` owns every channel's :class:`~pycangui.core.bus.BusManager`
  and merges their traffic into one signal.  The trace, the recorder and the
  decoders listen to that, so everything is seen on one timeline -- which is
  the point of having two channels at all (watching an ECU forward messages
  from one bus to another).
* :class:`ActiveBus` presents *one* channel with exactly the interface a single
  ``BusManager`` has, so the protocol stacks (CANopen, UDS, J1939, XCP) work
  unchanged and simply follow the channel you select.  Switching channel looks
  to them like a disconnect followed by a connect, which is what it is.

All channels share one clock, so timestamps from different adapters can be
compared directly.
"""

from __future__ import annotations

import time

import can
from PySide6.QtCore import QObject, Signal, Slot

from pycangui.core.bus import BusManager, Frame

DEFAULT_CHANNEL = "CAN 1"


class Channels(QObject):
    """Every channel, and their merged traffic."""

    frames = Signal(list)  # batches of Frame from *all* channels
    channel_added = Signal(str)
    channel_removed = Signal(str)
    state_changed = Signal(str, bool)  # channel name, connected
    active_changed = Signal(str)
    error = Signal(str)  # "channel: text"
    note = Signal(str)  # worth saying, but not a failure

    def __init__(self) -> None:
        super().__init__()
        self._buses: dict[str, BusManager] = {}
        self._connections: dict[str, tuple] = {}
        self._active = ""
        self._t0 = time.monotonic()  # one clock for every channel
        self.add(DEFAULT_CHANNEL)

    # --- the collection --------------------------------------------------------
    def names(self) -> list[str]:
        return list(self._buses)

    def get(self, name: str) -> BusManager | None:
        return self._buses.get(name)

    def add(self, name: str) -> BusManager:
        if name in self._buses:
            return self._buses[name]
        bus = BusManager(channel_name=name, clock_start=self._t0)
        self._buses[name] = bus
        # Keep the connections so they can be undone: a queued signal arriving
        # after this object has gone is delivered to a dead C++ object, which
        # Qt punishes with a segmentation fault rather than an exception.
        self._connections[name] = (
            (bus.frames, self.frames),
            (bus.connected, lambda _d, n=name: self.state_changed.emit(n, True)),
            (bus.disconnected, lambda n=name: self.state_changed.emit(n, False)),
            (bus.error, lambda text, n=name: self.error.emit(f"{n}: {text}")),
            (bus.note, self.note),
        )
        for signal, slot in self._connections[name]:
            signal.connect(slot)
        self.channel_added.emit(name)
        if not self._active:
            self.set_active(name)
        return bus

    def remove(self, name: str) -> None:
        bus = self._buses.pop(name, None)
        if bus is None:
            return
        bus.disconnect_bus()
        self._disconnect(name)
        self.channel_removed.emit(name)
        if self._active == name:
            self.set_active(next(iter(self._buses), ""))

    def rename(self, old: str, new: str) -> None:
        if old not in self._buses or new in self._buses or not new:
            return
        bus = self._buses.pop(old)
        bus.channel_name = new
        self._buses[new] = bus
        self.channel_removed.emit(old)
        self.channel_added.emit(new)
        if self._active == old:
            self.set_active(new)

    # --- the active channel ------------------------------------------------------
    @property
    def active(self) -> str:
        return self._active

    def active_bus(self) -> BusManager | None:
        return self._buses.get(self._active)

    def set_active(self, name: str) -> None:
        if name == self._active:
            return
        self._active = name if name in self._buses else ""
        self.active_changed.emit(self._active)

    @property
    def any_connected(self) -> bool:
        return any(bus.is_connected for bus in self._buses.values())

    def disconnect_all(self) -> None:
        for bus in self._buses.values():
            bus.disconnect_bus()

    def _disconnect(self, name: str) -> None:
        for signal, slot in self._connections.pop(name, ()):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):  # already gone
                pass

    def shutdown(self) -> None:
        """Stop every channel and drop every connection made to it."""
        self.disconnect_all()
        for name in list(self._connections):
            self._disconnect(name)
        self._buses.clear()


class ActiveBus(QObject):
    """One channel, wearing a ``BusManager``'s interface.

    Protocol stacks hold this instead of a bus and never notice that the
    channel underneath can change.
    """

    connected = Signal(str)
    disconnected = Signal()
    frames = Signal(list)
    error = Signal(str)

    def __init__(self, channels: Channels) -> None:
        super().__init__()
        self._channels = channels
        self._bound: BusManager | None = None
        channels.active_changed.connect(self._rebind)
        self._rebind(channels.active)

    # --- following the selection ---------------------------------------------------
    @Slot(str)
    def _rebind(self, _name: str) -> None:
        new = self._channels.active_bus()
        if new is self._bound:
            return
        if self._bound is not None:
            was_connected = self._bound.is_connected
            self._unbind()
            if was_connected:
                self.disconnected.emit()  # the stacks tear down cleanly
        self._bound = new
        if new is not None:
            new.frames.connect(self.frames)
            new.connected.connect(self.connected)
            new.disconnected.connect(self.disconnected)
            new.error.connect(self.error)
            if new.is_connected:
                self.connected.emit(new.description)

    def close(self) -> None:
        """Stop following the selection and drop the connections to the bus."""
        try:
            self._channels.active_changed.disconnect(self._rebind)
        except (RuntimeError, TypeError):
            pass
        self._unbind()
        self._bound = None

    def _unbind(self) -> None:
        if self._bound is None:
            return
        for signal, slot in (
            (self._bound.frames, self.frames),
            (self._bound.connected, self.connected),
            (self._bound.disconnected, self.disconnected),
            (self._bound.error, self.error),
        ):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass

    # --- the BusManager interface ----------------------------------------------------
    @property
    def bus(self) -> can.BusABC | None:
        return self._bound.bus if self._bound else None

    @property
    def notifier(self) -> can.Notifier | None:
        return self._bound.notifier if self._bound else None

    @property
    def is_connected(self) -> bool:
        return bool(self._bound and self._bound.is_connected)

    @property
    def channel_name(self) -> str:
        return self._bound.channel_name if self._bound else ""

    @property
    def interface(self) -> str:
        return self._bound.interface if self._bound else ""

    @property
    def description(self) -> str:
        return self._bound.description if self._bound else ""

    def now(self) -> float:
        return self._bound.now() if self._bound else 0.0

    def add_listener(self, listener: can.Listener) -> None:
        if self._bound:
            self._bound.add_listener(listener)

    def remove_listener(self, listener: can.Listener) -> None:
        if self._bound:
            self._bound.remove_listener(listener)

    def send(self, can_id: int, data: bytes, *, extended: bool = False, fd: bool = False) -> None:
        if self._bound is None:
            self.error.emit("No channel selected")
            return
        self._bound.send(can_id, data, extended=extended, fd=fd)

    def send_periodic(self, can_id: int, data: bytes, period_s: float, **kwargs):
        if self._bound is None:
            self.error.emit("No channel selected")
            return None
        return self._bound.send_periodic(can_id, data, period_s, **kwargs)


__all__ = ["DEFAULT_CHANNEL", "ActiveBus", "Channels", "Frame"]
