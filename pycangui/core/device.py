# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""A device: something live whose objects are read and written by index and sub.

What a plugin drives, rather than a CANopen node: the CiA 402 pane needs a
drive's objects, the state it is in and what else writes to it, and none of
that is CANopen's alone -- an EtherCAT slave speaking CoE has all of it. A
CANopen node is one kind of device; a plugin that brings another protocol
provides the other kinds, through a ``DeviceProvider`` (plugin API 3).

Reads and writes block, and are made from inside ``background``: a drive is
worked by sequences -- read the statusword, write the controlword, read it
again -- which are short and plain written one line after another, and
long and fragile written as callbacks. A custom pane, which only asks and
shows, uses a ``Source`` instead; a device can be made into one.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Hashable
from typing import Any

from PySide6.QtCore import QObject, Signal


class Device(QObject):
    """One live device. Subclassed for each protocol; the defaults are "cannot"."""

    #: What it knows of itself changed: its dictionary (an EDS loaded), or
    #: what it is mapped to receive.
    changed = Signal()
    #: Its state changed, or it was lost or came back.
    state_changed = Signal()
    #: About to stop being reachable, on purpose, and said while it still can be
    #: written to: whoever is driving it stops now. On the GUI thread, and the
    #: device goes once every slot has returned -- a slot that writes does so
    #: there and then, not from ``background``.
    leaving = Signal()

    #: What identifies it, the same for the life of the window: a CANopen
    #: node's is its node ID.
    key: Hashable = None
    #: What to call it on screen: "Node 5".
    label = ""
    #: The states it can be put in from here, in the order a pane would offer
    #: them; empty for a device with no state to set.
    states: tuple[str, ...] = ()

    def available(self) -> bool:
        """Whether it can be read and written now."""
        return False

    def background(self, job: Callable[[], Any], done: Callable[[Any, Any], None]) -> None:
        """Run ``job`` where this device's reads and writes go, one after another;
        ``done(result, error)`` on the GUI thread."""
        raise NotImplementedError

    def read(self, index: int, sub: int) -> bytes:
        """An object's value as the device sends it. Blocks: from ``background``."""
        raise NotImplementedError

    def write(self, index: int, sub: int, data: bytes) -> None:
        """Write an object. Blocks: from ``background``."""
        raise NotImplementedError

    def has(self, index: int, sub: int = 0) -> bool | None:
        """Whether it has the object; None when there is nothing to go by."""
        return None

    def state(self) -> tuple[str | None, bool]:
        """Its state as last heard (None: not known), and whether it is lost."""
        return None, False

    def set_state(self, name: str) -> None:
        """Put it in one of ``states``."""
        raise NotImplementedError

    def receives(self) -> list[tuple[int, int, str]]:
        """Objects set by process data from something else, and what carries each:
        ``(0x60FF, 0, "RPDO2")``. A write from here is overwritten by the next one."""
        return []

    def cycles(self, index: int, sub: int = 0) -> bool:
        """Whether a write to the object is sent again every cycle, as process
        data the provider keeps sending, so a value written once is held: an
        EtherCAT slave's mapped outputs. A CANopen SDO write is sent once."""
        return False


class DeviceProvider(QObject):
    """Devices a plugin brings, by a protocol pycangui does not speak itself.

    Given to ``app.add_device_provider``. Its devices' keys must not be ints,
    which are CANopen node IDs: a tuple that starts with the plugin's name,
    ``("ethercat", 1)``, cannot be taken for one. API 3.
    """

    #: Which devices there are changed: one appeared or went.
    changed = Signal()

    def devices(self) -> list[Device]:
        return []

    def device(self, key: Hashable) -> Device | None:
        return None


def _node_id(key: Hashable) -> bool:
    return isinstance(key, int) and not isinstance(key, bool)


class Devices(QObject):
    """The devices there are: the CANopen nodes, then each provider's."""

    #: Which devices there are changed.
    changed = Signal()

    def __init__(self, canopen) -> None:
        super().__init__()
        self._canopen = canopen
        self._made: dict[Hashable, Device] = {}
        self._listed: list[Hashable] = []
        self._providers: list[DeviceProvider] = []
        #: Keys already said to be refused, so a list refilled every second
        #: does not say it every second.
        self._refused: set[Hashable] = set()
        for signal in (canopen.node_seen, canopen.eds_loaded, canopen.identified):
            signal.connect(self._check)

    def devices(self) -> list[Device]:
        found = [self.device(node_id) for node_id in self._canopen.nodes()]
        for provider in self._providers:
            found.extend(device for device in provider.devices() if self._allowed(device.key))
        return found

    def device(self, key: Hashable) -> Device | None:
        """The device by its key. One per key, kept, so its signals are the same ones."""
        if _node_id(key):
            return self._node(key)
        for provider in self._providers:
            if (found := provider.device(key)) is not None:
                return found
        return None

    def add_provider(self, provider: DeviceProvider) -> None:
        if provider in self._providers:
            return
        self._providers.append(provider)
        provider.changed.connect(self.changed)
        self.changed.emit()

    def remove_provider(self, provider: DeviceProvider) -> None:
        """Take a provider's devices away, saying ``leaving`` first for each that
        can still be reached: whatever is driving one gets to stop it."""
        if provider not in self._providers:
            return
        for device in provider.devices():
            if device.available():
                device.leaving.emit()
        self._providers.remove(provider)
        provider.changed.disconnect(self.changed)
        self.changed.emit()

    def _allowed(self, key: Hashable) -> bool:
        if not _node_id(key):
            return True
        if key not in self._refused:
            self._refused.add(key)
            logging.getLogger(__name__).warning(
                "A plugin's device has the key %r, which is a CANopen node ID: it is left out.", key
            )
        return False

    def _node(self, key: int) -> Device | None:
        if (made := self._made.get(key)) is not None:
            return made
        if not 1 <= key <= 127:
            return None
        from pycangui.canopen.device import CanopenDevice

        made = self._made[key] = CanopenDevice(self._canopen, key)
        return made

    def _check(self, *_args) -> None:
        """Said only when the list is different: node_seen comes with every heartbeat."""
        keys = list(self._canopen.nodes())
        if keys != self._listed:
            self._listed = keys
            self.changed.emit()
