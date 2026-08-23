"""Signals pane: every live signal in the hub, grouped by source, with its
latest value and a checkbox that adds it to the Plot pane."""

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


class SignalsView(QWidget):
    plot_toggled = Signal(str, bool)  # key, on

    def __init__(self, hub: SignalHub) -> None:
        super().__init__()
        self.hub = hub
        self._groups: dict[str, QTreeWidgetItem] = {}
        self._items: dict[str, QTreeWidgetItem] = {}
        self._updating = False

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Signal", "Value", "Unit", "Plot"])
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
            group = QTreeWidgetItem([s.group, "", "", ""])
            group.setFlags(group.flags() & ~Qt.ItemIsUserCheckable)
            self._groups[s.group] = group
            self.tree.addTopLevelItem(group)
            group.setExpanded(True)
        self._updating = True
        item = QTreeWidgetItem([s.name, "", s.unit, ""])
        item.setData(0, ROLE_KEY, key)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(3, Qt.Unchecked)
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
        if self._updating or column != 3:
            return
        key = item.data(0, ROLE_KEY)
        if key:
            self.plot_toggled.emit(key, item.checkState(3) == Qt.Checked)

    def set_plotted(self, key: str, on: bool) -> None:
        item = self._items.get(key)
        if item is not None:
            self._updating = True
            item.setCheckState(3, Qt.Checked if on else Qt.Unchecked)
            self._updating = False

    @Slot()
    def unplot_all(self) -> None:
        for item in self._items.values():
            if item.checkState(3) == Qt.Checked:
                item.setCheckState(3, Qt.Unchecked)  # emits plot_toggled via itemChanged

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
