# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""A curve, a map or an axis of points, as a table: its axes along the top and
down the side, its values in between.

Every cell is one value with a name of its own -- ``IgnitionMap[2][3]``,
``IgnitionMap:x[2]``, ``SpeedAxis[2]`` -- read, written and plotted the same
way as one value of an array. A map's values are named ``[x][y]`` whichever
way round they are stored. The cells of a fixed axis are worked out from the
file and are not in the slave at all, so they cannot be typed into.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from pycangui.ui import fonts, icons
from pycangui.xcp.a2l import AXIS_PTS, MAP, Axis, Parameter
from pycangui.xcp.manager import XcpManager

ROLE_NAME = Qt.UserRole
#: How long after the last value arrives the columns are fitted to them.
FIT_AFTER_MS = 50
NOT_READ_YET = (
    "The number of points is kept in the slave, so the table is laid out when it\n"
    "has been read. Press Read."
)


def _with_unit(text: str, unit: str) -> str:
    return f"{text} ({unit})" if unit and unit != "raw" else text


def _axis_name(axis: Axis) -> str:
    """What an axis is of, with its unit; by its letter where the file names nothing."""
    what = axis.input if axis.input != "NO_INPUT_QUANTITY" else f"the {axis.letter} axis"
    return _with_unit(what, axis.unit)


class TableWindow(QDialog):
    """One curve, map or AXIS_PTS, in a window of its own beside the pane."""

    def __init__(
        self,
        manager: XcpManager,
        name: str,
        meant: Callable[[str, str], bool],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.manager = manager
        self.name = name
        #: Asked before a value outside the A2L's limits is written.
        self._meant = meant
        self.setWindowTitle(name)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self.table = QTableWidget()
        self.table.setFont(fonts.mono())
        self.table.itemChanged.connect(self._on_changed)
        self.about = QLabel()
        self.about.setWordWrap(True)
        read = icons.beside(QPushButton("Read"), icons.READ)
        read.setToolTip("Read the axes and the values from the slave again.")
        read.clicked.connect(lambda: self.manager.read(self.name))
        row = QHBoxLayout()
        row.addWidget(self.about, 1)
        row.addWidget(read)
        layout = QVBoxLayout(self)
        layout.addLayout(row)
        layout.addWidget(self.table, 1)
        #: Each value's cell, by the value's name.
        self._cells: dict[str, QTableWidgetItem] = {}
        #: What each value last read as, to put back when a write is not gone through with.
        self._last: dict[str, str] = {}
        #: The names of the parameters whose reshaping changes this table.
        self._sources: set[str] = {name}
        self._updating = False
        # The columns fit what is in them once the values are in, a moment
        # after the last arrives rather than once for each of a hundred.
        self._fit = QTimer(self, singleShot=True, interval=FIT_AFTER_MS)
        self._fit.timeout.connect(self.table.resizeColumnsToContents)
        manager.value.connect(self._on_value)
        manager.reshaped.connect(self._on_reshaped)
        self.build()
        self.resize(640, 320)

    # --- laying it out ------------------------------------------------------------------
    def build(self) -> None:
        """Lay the table out from the parameter as it now is."""
        self._updating = True
        self.table.clear()
        self._cells.clear()
        a2l = self.manager.a2l
        table = a2l.find(self.name) if a2l is not None else None
        if table is None or not table.sizes:
            self.table.setRowCount(0)
            self.table.setColumnCount(0)
            self.about.setText(table.unreadable if table and table.unreadable else NOT_READ_YET)
            self._updating = False
            return
        self._sources = {self.name} | {a.points for a in table.axes if a.points}
        # Which value is which is in the axes, not in a count from 1.
        self.table.horizontalHeader().hide()
        self.table.verticalHeader().show()
        if table.shape == AXIS_PTS:
            self._axis_points(table)
        elif table.shape == MAP:
            self._map(table)
        else:
            self._curve(table)
        self.table.resizeColumnsToContents()
        self._updating = False

    def _axis_points(self, table: Parameter) -> None:
        count = table.sizes[0]
        self.table.setRowCount(1)
        self.table.setColumnCount(count)
        self.table.setVerticalHeaderLabels([_with_unit(table.name, table.unit)])
        for i in range(count):
            self.table.setItem(0, i, self._value(f"{table.name}[{i}]"))
        self.about.setText(f"{count} axis points, shared by the curves and maps that name them.")

    def _curve(self, table: Parameter) -> None:
        x = table.axes[0]
        count = table.sizes[0]
        self.table.setRowCount(2)
        self.table.setColumnCount(count)
        self.table.setVerticalHeaderLabels([self._axis_label(x), _with_unit("value", table.unit)])
        for i in range(count):
            self.table.setItem(0, i, self._axis_cell(x, i))
            self.table.setItem(1, i, self._value(f"{table.name}[{i}]"))
        self.about.setText(f"{count} points against {_axis_name(x)}.")

    def _map(self, table: Parameter) -> None:
        x, y = table.axes
        across, down = table.sizes
        self.table.setRowCount(down + 1)
        self.table.setColumnCount(across + 1)
        corner = QTableWidgetItem(_with_unit("", table.unit))
        corner.setFlags(Qt.ItemIsEnabled)
        self.table.setItem(0, 0, corner)
        # The axes are the first row and column, so the headers say nothing.
        self.table.verticalHeader().hide()
        for i in range(across):
            self.table.setItem(0, i + 1, self._axis_cell(x, i))
        for j in range(down):
            self.table.setItem(j + 1, 0, self._axis_cell(y, j))
            for i in range(across):
                self.table.setItem(j + 1, i + 1, self._value(f"{table.name}[{i}][{j}]"))
        self.about.setText(
            f"{across} by {down}: {_axis_name(x)} across the top, {_axis_name(y)} down the side."
        )

    @staticmethod
    def _axis_label(axis: Axis) -> str:
        return f"{axis.letter}: {_axis_name(axis)}"

    def _axis_cell(self, axis: Axis, index: int) -> QTableWidgetItem:
        if axis.points:
            item = self._value(f"{axis.points}[{index}]")
        else:
            # A fixed axis: worked out from the file, not kept in the slave.
            raw = axis.fixed[index]
            phys = axis.conversion.to_phys(raw) if axis.conversion else raw
            places = axis.decimals
            item = QTableWidgetItem(f"{phys:.{places}f}" if places is not None else f"{phys:g}")
            item.setFlags(Qt.ItemIsEnabled)
            item.setToolTip("A fixed axis: the A2L gives these points, and the slave keeps none.")
        item.setBackground(self.palette().alternateBase())
        return item

    def _value(self, name: str) -> QTableWidgetItem:
        item = QTableWidgetItem(self._last.get(name, ""))
        item.setData(ROLE_NAME, name)
        found = self.manager.a2l.find(name)
        flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if found is not None and found.writable:
            flags |= Qt.ItemIsEditable
        item.setFlags(flags)
        item.setToolTip(name)
        self._cells[name] = item
        return item

    # --- what the slave says, and what is typed -------------------------------------------
    @Slot(str, float)
    def _on_value(self, name: str, value: float) -> None:
        shown = self.manager.shown(name, value)
        self._last[name] = shown
        if (item := self._cells.get(name)) is not None:
            was, self._updating = self._updating, True
            item.setText(shown)
            self._updating = was
            self._fit.start()

    @Slot(str)
    def _on_reshaped(self, name: str) -> None:
        if name in self._sources:
            self.build()

    def _on_changed(self, item: QTableWidgetItem) -> None:
        if self._updating:
            return
        name = item.data(ROLE_NAME)
        if not name:
            return
        if not self._meant(name, item.text()):
            self._updating = True
            item.setText(self._last.get(name, ""))  # as it was: nothing was written
            self._updating = False
            return
        self.manager.write(name, item.text(), beyond_limits=True)
