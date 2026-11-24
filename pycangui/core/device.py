# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""A device: something live whose objects are read and written by index and sub.

What a plugin drives, rather than a CANopen node: the CiA 402 pane needs a
drive's objects, the state it is in and what else writes to it, and none of
that is CANopen's alone -- an EtherCAT slave speaking CoE has all of it. A
CANopen node is one kind of device; a plugin that brings another protocol
provides the other kinds (plugin API 3).

Reads and writes block, and are made from inside ``background``: a drive is
worked by sequences -- read the statusword, write the controlword, read it
again -- which are short and plain written one line after another, and
long and fragile written as callbacks. A custom pane, which only asks and
shows, uses a ``Source`` instead; a device can be made into one.
"""

from __future__ import annotations

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


class Devices(QObject):
    """The devices there are: CANopen nodes now, and a plugin's later."""

    #: Which devices there are changed.
    changed = Signal()

    def __init__(self, canopen) -> None:
        super().__init__()
        self._canopen = canopen
        self._made: dict[Hashable, Device] = {}
        self._listed: list[Hashable] = []
        for signal in (canopen.node_seen, canopen.eds_loaded, canopen.identified):
            signal.connect(self._check)

    def devices(self) -> list[Device]:
        return [self.device(node_id) for node_id in self._canopen.nodes()]

    def device(self, key: Hashable) -> Device | None:
        """The device by its key. One per key, kept, so its signals are the same ones."""
        if (made := self._made.get(key)) is not None:
            return made
        if not isinstance(key, int) or isinstance(key, bool) or not 1 <= key <= 127:
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
