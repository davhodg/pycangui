# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Signals pane: every live signal in the hub, grouped by source, with its
latest value and two checkboxes: Plot Y1 draws it against the plot's left
axis, Plot Y2 against the second axis on the right."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pycangui.core.signals import SignalHub

ROLE_KEY = Qt.UserRole

#: The two checkbox columns: Plot Y1, the left axis, and Plot Y2, the right.
PLOT = 3
RIGHT = 4
Y1_TIP = "Plot the signal against the left Y axis."
Y2_TIP = (
    "Plot the signal against a second Y axis, on the right of the plot, with a\n"
    "scale of its own.  A signal is on one axis at a time."
)


class SignalsView(QWidget):
    plot_toggled = Signal(str, bool)  # key, on
    axis_toggled = Signal(str, bool)  # key, on the right hand axis
    #: *Unplot all* was pressed: whatever remembers what was plotted forgets
    #: the lot, including signals that have no row yet to untick.
    all_unplotted = Signal()

    def __init__(self, hub: SignalHub) -> None:
        super().__init__()
        self.hub = hub
        self._groups: dict[str, QTreeWidgetItem] = {}
        self._items: dict[str, QTreeWidgetItem] = {}
        self._updating = False

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Signal", "Value", "Unit", "Plot Y1", "Plot Y2"])
        self.tree.headerItem().setToolTip(PLOT, Y1_TIP)
        self.tree.headerItem().setToolTip(RIGHT, Y2_TIP)
        self.tree.setFont(QFont("Consolas", 9))
        self.tree.header().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.tree.header().setStretchLastSection(True)
        self.tree.itemChanged.connect(self._on_item_changed)

        self.search = QLineEdit()
        self.search.setPlaceholderText("filter signals...")
        self.search.textChanged.connect(self._apply_search)
        unplot = QPushButton("Unplot all")
        unplot.clicked.connect(self.unplot_all)
        bar = QHBoxLayout()
        bar.addWidget(self.search, 1)
        bar.addWidget(unplot)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.addLayout(bar)
        layout.addWidget(self.tree)

        hub.added.connect(self._on_added)
        self._refresh_timer = QTimer(self, interval=200, timeout=self._refresh_values)
        self._refresh_timer.start()
        for key in hub.keys():
            self._on_added(key)

    @Slot(str)
    def _on_added(self, key: str) -> None:
        s = self.hub.get(key)
        if s is None or key in self._items:
            return
        group = self._groups.get(s.group)
        if group is None:
            group = QTreeWidgetItem([s.group, "", "", "", ""])
            group.setFlags(group.flags() & ~Qt.ItemIsUserCheckable)
            self._groups[s.group] = group
            self.tree.addTopLevelItem(group)
            group.setExpanded(True)
        self._updating = True
        item = QTreeWidgetItem([s.name, "", s.unit, "", ""])
        item.setData(0, ROLE_KEY, key)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(PLOT, Qt.Unchecked)
        item.setCheckState(RIGHT, Qt.Unchecked)
        group.addChild(item)
        self._items[key] = item
        self._updating = False
        self._apply_search(self.search.text())

    def _refresh_values(self) -> None:
        if not self.isVisible():
            return
        for key, item in self._items.items():
            s = self.hub.get(key)
            if s is not None and s.latest is not None:
                v = s.latest
                text = f"{v:.6g}" if isinstance(v, float) and not v.is_integer() else f"{int(v)}"
                if item.text(1) != text:
                    item.setText(1, text)
                if s.unit and not item.text(2):
                    item.setText(2, s.unit)

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._updating or column not in (PLOT, RIGHT):
            return
        key = item.data(0, ROLE_KEY)
        if not key:
            return
        y1 = item.checkState(PLOT) == Qt.Checked
        y2 = item.checkState(RIGHT) == Qt.Checked
        # One axis at a time.  Two boxes both ticked would read as the signal
        # drawn twice, so ticking one moves it there and unticks the other,
        # and unticking the ticked one takes it off the plot.
        if column == PLOT:
            if y1 and y2:
                self._tick(item, RIGHT, False)
                self.axis_toggled.emit(key, False)  # the same curve, moved left
            else:
                self.plot_toggled.emit(key, y1)
            return
        if y2 and y1:
            self._tick(item, PLOT, False)
            self.axis_toggled.emit(key, True)  # the same curve, moved right
        elif y2:
            self.plot_toggled.emit(key, True)
            self.axis_toggled.emit(key, True)
        else:
            self.plot_toggled.emit(key, False)

    def _tick(self, item: QTreeWidgetItem, column: int, on: bool) -> None:
        """Set a checkbox without it counting as somebody ticking it."""
        self._updating = True
        item.setCheckState(column, Qt.Checked if on else Qt.Unchecked)
        self._updating = False

    def set_plotted(self, key: str, on: bool) -> None:
        item = self._items.get(key)
        if item is None:
            return
        if not on:
            self._tick(item, PLOT, False)
            self._tick(item, RIGHT, False)
        elif item.checkState(RIGHT) != Qt.Checked:
            self._tick(item, PLOT, True)

    def set_right(self, key: str, on: bool) -> None:
        """Show a plotted signal on the right axis, or back on the left."""
        item = self._items.get(key)
        if item is not None:
            self._tick(item, RIGHT, on)
            self._tick(item, PLOT, not on)

    @Slot()
    def unplot_all(self) -> None:
        for item in self._items.values():
            # Each emits plot_toggled through itemChanged.
            if item.checkState(RIGHT) == Qt.Checked:
                item.setCheckState(RIGHT, Qt.Unchecked)
            elif item.checkState(PLOT) == Qt.Checked:
                item.setCheckState(PLOT, Qt.Unchecked)
        self.all_unplotted.emit()

    def _apply_search(self, text: str) -> None:
        needle = text.lower()
        for group in self._groups.values():
            any_visible = False
            for i in range(group.childCount()):
                child = group.child(i)
                show = needle in child.text(0).lower() or needle in group.text(0).lower()
                child.setHidden(not show)
                any_visible |= show
            group.setHidden(not any_visible)
