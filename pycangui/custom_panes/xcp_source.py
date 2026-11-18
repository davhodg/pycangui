# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""An XCP or CCP slave as a source for a custom pane: its measurements, by name.

The key is the A2L name, and the A2L says the rest of what a field needs --
the unit (PHYS_UNIT), the places after the point (FORMAT), what a value is
called (COMPU_VTAB), the limits. Values arrive already converted, physical
rather than raw, so the display carries no scaling of its own: the conversion
is the A2L's, done once, in the manager.

Measurements are read; characteristics are read and written, the value typed
run back through the conversion by the manager and read back afterwards, so
the field shows what the slave holds rather than what was sent. Limits are
checked by the pane before anything is sent, as an EDS's are.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QTimer

from pycangui.canopen.display import Display
from pycangui.custom_panes.model import XCP
from pycangui.custom_panes.source import Key, Source

#: What ``objects`` says a curve or a map is, so it arrives as a table field.
TABLE = "table"


class XcpSource(Source):
    """The slave the XCP pane is connected to."""

    protocol = XCP
    live = True
    writable = True

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
        description = param.description
        if param.is_table and param.sizes:
            shape = " by ".join(str(size) for size in param.sizes)
            description = f"{description} ({shape})" if description else shape
        return Display(
            name=param.name,
            description=description,
            unit=param.unit,
            decimals=param.decimals,
            choices=dict(param.choices),
            low=param.lower,
            high=param.upper,
        )

    def request(self, key: Key) -> None:
        self.manager.read_value(str(key))

    def write(self, key: Key, raw: Any) -> None:
        try:
            phys = float(raw)
        except (TypeError, ValueError):
            text = f"{raw!r} is not a number"
            QTimer.singleShot(0, self, lambda: self.value.emit(key, None, text))
            return
        self.manager.write_value(str(key), phys)

    def objects(self) -> list[tuple[Key, str, str]]:
        """What a pane can show: single values, and curves and maps.

        A characteristic the slave lets be written is ``rw``, so it arrives as
        a field to type into; a curve or a map is ``table``, a button opening
        it. Arrays and text are left out, being neither one value nor a table.
        """
        a2l = self.manager.a2l
        if a2l is None:
            return []
        every = sorted([*a2l.measurements(), *a2l.characteristics()], key=lambda p: p.name.lower())
        out = []
        for param in every:
            if not param.readable or param.is_text:
                continue
            if param.is_table:
                out.append((param.name, param.name, TABLE))
            elif not param.is_array:
                out.append((param.name, param.name, "rw" if param.writable else "ro"))
        return out
