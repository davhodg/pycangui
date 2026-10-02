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
    QMenu,
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
Y1_TIP = "Plot the signal against the left Y axis.\nRight-click to plot or unplot several at once."
MENU_TIP = "Right-click to plot or unplot every signal, or every signal of one message."
Y2_TIP = (
    "Plot the signal against a second Y axis, on the right of the plot, with a\n"
    "scale of its own. A signal is on one axis at a time."
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
        # Plotting and unplotting several at once is on the right-click,
        # over the list or its two Plot headings, rather than a button here.
        self.tree.setToolTip(MENU_TIP)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_menu)
        header = self.tree.header()
        header.setContextMenuPolicy(Qt.CustomContextMenu)
        header.customContextMenuRequested.connect(self._header_menu)
        bar = QHBoxLayout()
        bar.addWidget(self.search, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.addLayout(bar)
        layout.addWidget(self.tree)

        hub.added.connect(self._on_added)
        hub.removed.connect(self._on_removed)
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

    @Slot(list)
    def _on_removed(self, keys: list) -> None:
        """Take forgotten signals off the list, and a source left empty with them."""
        for key in keys:
            item = self._items.pop(key, None)
            if item is None:
                continue
            group = item.parent()
            group.removeChild(item)
            if group.childCount() == 0:
                name = next((g for g, it in self._groups.items() if it is group), None)
                self._groups.pop(name, None)
                self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(group))

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
        # One axis at a time. Two boxes both ticked would read as the signal
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

    # --- several at once, on the right-click ------------------------------------------
    def _tree_menu(self, at) -> None:
        item = self.tree.itemAt(at)
        column = self.tree.columnAt(at.x())
        self.menu_for(item, column).exec(self.tree.viewport().mapToGlobal(at))

    def _header_menu(self, at) -> None:
        header = self.tree.header()
        self.menu_for(None, header.logicalIndexAt(at)).exec(header.mapToGlobal(at))

    def menu_for(self, item: QTreeWidgetItem | None, column: int) -> QMenu:
        """What a right-click offers. Over the Plot Y2 column it plots on the
        right axis, anywhere else on the left; over a message, that message's
        signals have entries of their own."""
        axis = RIGHT if column == RIGHT else PLOT
        name = "Y2" if axis == RIGHT else "Y1"
        group = None if item is None else (item.parent() or item)
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        if group is not None:
            title = group.text(0)
            menu.addAction(f"Plot all of {title} on {name}", lambda: self.plot_all(axis, group))
            menu.addAction(f"Unplot all of {title}", lambda: self.unplot_all(group))
            menu.addSeparator()
        every = menu.addAction(f"Plot all on {name}", lambda: self.plot_all(axis))
        every.setToolTip("Every signal listed: with a filter typed, only the ones it shows.")
        menu.addAction("Unplot all", self.unplot_all)
        return menu

    def _signals(self, group: QTreeWidgetItem | None, shown_only: bool) -> list[QTreeWidgetItem]:
        groups = [group] if group is not None else list(self._groups.values())
        return [
            g.child(i)
            for g in groups
            for i in range(g.childCount())
            if not (shown_only and g.child(i).isHidden())
        ]

    def plot_all(self, axis: int = PLOT, group: QTreeWidgetItem | None = None) -> None:
        """Plot every signal listed, or every one of a message's, on one axis.

        Listed, so a filter narrows it: "plot all" with *speed* typed plots
        the speeds, which is usually what was meant.
        """
        for item in self._signals(group, shown_only=True):
            if item.checkState(axis) != Qt.Checked:
                item.setCheckState(axis, Qt.Checked)  # itemChanged plots or moves it

    @Slot()
    def unplot_all(self, group: QTreeWidgetItem | None = None) -> None:
        """Take every signal off the plot, or every one of a message's."""
        for item in self._signals(group, shown_only=False):
            # Each emits plot_toggled through itemChanged.
            if item.checkState(RIGHT) == Qt.Checked:
                item.setCheckState(RIGHT, Qt.Unchecked)
            elif item.checkState(PLOT) == Qt.Checked:
                item.setCheckState(PLOT, Qt.Unchecked)
        if group is None:
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
