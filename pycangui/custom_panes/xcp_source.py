# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""An XCP or CCP slave as a source for a custom pane: its measurements, by name.

The key is the A2L name, and the A2L says the rest of what a field needs --
the unit (PHYS_UNIT), the places after the point (FORMAT), what a value is
called (COMPU_VTAB), the limits. Values arrive already converted, physical
rather than raw, so the display carries no scaling of its own: the conversion
is the A2L's, done once, in the manager.

Read only, for now. Measurements are what a slave reports, and reading them
is enough to prove the pane does not care what its keys are; writing
characteristics -- with their conversion run backwards and their limits
checked -- comes next.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QTimer

from pycangui.canopen.display import Display
from pycangui.custom_panes.model import XCP
from pycangui.custom_panes.source import Key, Source


class XcpSource(Source):
    """The slave the XCP pane is connected to."""

    protocol = XCP
    live = True
    writable = False

    def __init__(self, manager) -> None:
        super().__init__()
        self.manager = manager
        self.label = self._label()
        manager.answered.connect(self._on_answered)
        manager.connected.connect(self._on_changed)
        manager.a2l_loaded.connect(self._on_changed)

    def _label(self) -> str:
        what = getattr(self.manager, "protocol", "XCP")
        said = getattr(self.manager, "slave_id", "")
        return f"{what} slave {said}" if said else f"{what} slave"

    def _on_changed(self, *_args) -> None:
        # Connected to another slave, or another A2L read: the names, units
        # and limits may all be different ones now.
        self.label = self._label()
        self.changed.emit()

    def _on_answered(self, name: str, value: Any, error: Any) -> None:
        self.value.emit(name, value, error)

    def _parameter(self, key: Key):
        a2l = self.manager.a2l
        return a2l.find(str(key)) if a2l is not None else None

    def display(self, key: Key) -> Display:
        param = self._parameter(key)
        if param is None:
            return Display(name=str(key))
        return Display(
            name=param.name,
            description=param.description,
            unit=param.unit,
            decimals=param.decimals,
            choices=dict(param.choices),
            low=param.lower,
            high=param.upper,
        )

    def request(self, key: Key) -> None:
        self.manager.read_value(str(key))

    def write(self, key: Key, raw: Any) -> None:
        """Not yet: say so against the field rather than drop it."""
        QTimer.singleShot(
            0, self, lambda: self.value.emit(key, None, "writing is not offered here yet")
        )

    def objects(self) -> list[tuple[Key, str, str]]:
        """The A2L's measurements that are one value each, by name.

        Arrays, curves, maps and text are left out: a pane field shows one
        value, and they are not one.
        """
        a2l = self.manager.a2l
        if a2l is None:
            return []
        return [
            (param.name, param.name, "ro")
            for param in sorted(a2l.measurements(), key=lambda p: p.name.lower())
            if param.readable and not (param.is_array or param.is_table or param.is_text)
        ]
