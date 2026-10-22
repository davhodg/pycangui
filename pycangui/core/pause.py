# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Holding back what pycangui sends by itself, and stopping all of it.

Two things, kept apart because one comes back and the other does not.

**Pause** holds back the traffic pycangui sends without anybody asking at the
time: polling, in custom panes, the motor pane and XCP or CCP; CANopen's SYNC
and TIME producers; the reads CANopen makes of a node unasked -- identifying
one first heard, reading its PDO configuration again after a boot-up. All of
it goes on again when the pause lifts. A flash holds it while it runs, so a
node rebooting into its bootloader is not identified over SDO half way
through, and somebody can hold it by hand. It is held by name and lifts when
nobody holds it any more: a flash ending does not undo a pause somebody set
by hand.

What a person does goes out whatever: a button, the console, a script. So do
the keep-alives -- UDS Tester Present and the J1939 DM13 hold -- which are
what keep a programming session open or a network quiet, and holding them
back during a flash would undo the very thing a flash needs.

**Stop all** is for good: everything that repeats is stopped -- cyclic
messages, polling, SYNC and TIME, a plugin's own -- and stays stopped until
somebody starts it again. Plugins hear it through ``app.on_stop_all``.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

#: Who holds a pause set from the status bar or the Tools menu.
BY_HAND = "by hand"


class TrafficPause(QObject):
    """Who is holding pycangui's own traffic back, and the word to stop it all."""

    #: Paused, or going again.
    changed = Signal(bool)
    #: Who holds it changed, paused or not: for what shows it.
    holders_changed = Signal()
    #: Stop everything that repeats. Not undone by anything.
    stop_all_requested = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._holders: list[str] = []

    @property
    def paused(self) -> bool:
        return bool(self._holders)

    @property
    def holders(self) -> list[str]:
        """Who holds it, in the order they took it."""
        return list(self._holders)

    @property
    def by_hand(self) -> bool:
        return BY_HAND in self._holders

    def hold(self, who: str) -> None:
        """Pause on behalf of ``who``. Holding twice is holding once."""
        if who in self._holders:
            return
        was = self.paused
        self._holders.append(who)
        self.holders_changed.emit()
        if not was:
            self.changed.emit(True)

    def release(self, who: str) -> None:
        """``who`` no longer needs the pause; it lifts when nobody does."""
        if who not in self._holders:
            return
        self._holders.remove(who)
        self.holders_changed.emit()
        if not self.paused:
            self.changed.emit(False)

    def set_by_hand(self, on: bool) -> None:
        if on:
            self.hold(BY_HAND)
        else:
            self.release(BY_HAND)

    def stop_all(self) -> None:
        """Stop everything that repeats, for good."""
        self.stop_all_requested.emit()


def described(holders: list[str]) -> str:
    """Who holds a pause, for a line or a label: ``UDS transfer and by hand``."""
    if len(holders) <= 1:
        return "".join(holders)
    return ", ".join(holders[:-1]) + " and " + holders[-1]
