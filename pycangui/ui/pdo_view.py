# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""PDO configuration pane: the communication and mapping parameters of a
node's TPDOs and RPDOs, editable and writable back to the node.

Each PDO is a top-level row (COB-ID, enabled, transmission type, inhibit time,
event timer); its mapped objects are child rows. Mapping entries are added by
picking objects out of the node's object dictionary, so the mapping can be
rebuilt without hand-encoding 0xIIIISSLL words.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pycangui.canopen import PdoConfig, PdoEntry
from pycangui.canopen.manager import CanopenManager, od_entries
from pycangui.core.context import Context

COL_NAME, COL_COBID, COL_ENABLED, COL_TRANS, COL_INHIBIT, COL_TIMER, COL_BITS = range(7)
HEADERS = (
    "PDO / mapped object",
    "COB-ID",
    "Enabled",
    "Transmission",
    "Inhibit us",
    "Event ms",
    "Bits",
)
ROLE_CONFIG = Qt.UserRole  # PdoConfig on top-level rows
ROLE_ENTRY = Qt.UserRole + 1  # PdoEntry on child rows


class ObjectPicker(QDialog):
    """Pick an object from the node's dictionary to map into a PDO."""

    def __init__(
        self, manager: CanopenManager, node_id: int, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Map an object")
        self.resize(520, 460)
        self.entries: list[PdoEntry] = []
        self.search = QLineEdit()
        self.search.setPlaceholderText("filter...")
        self.search.textChanged.connect(self._filter)
        self.list = QListWidget()
        self.list.setFont(QFont("Consolas", 9))
        self.list.itemDoubleClicked.connect(lambda _i: self.accept())
        node = manager.node(node_id)
        if node is not None:
            for index, sub, var, name in od_entries(node.object_dictionary):
                if var is None or index < 0x2000:
                    continue  # skip communication-profile objects and container rows
                bits = var.bit_length or 8
                self.entries.append(PdoEntry(index, sub or 0, bits, name))
                self.list.addItem(f"{index:04X}:{(sub or 0):02X}  {name}  ({bits} bits)")
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.search)
        layout.addWidget(self.list)
        layout.addWidget(buttons)

    def _filter(self, text: str) -> None:
        needle = text.lower()
        for i in range(self.list.count()):
            self.list.item(i).setHidden(needle not in self.list.item(i).text().lower())

    def chosen(self) -> PdoEntry | None:
        row = self.list.currentRow()
        return self.entries[row] if 0 <= row < len(self.entries) else None


class PdoConfigView(QWidget):
    """Editor for one node's PDO configuration."""

    changed = Signal()

    def __init__(self, manager: CanopenManager, ctx: Context) -> None:
        super().__init__()
        self.manager = manager
        self.ctx = ctx
        self.node_id: int | None = None
        self._updating = False

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(HEADERS)
        self.tree.setFont(QFont("Consolas", 9))
        self.tree.header().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.tree.header().setStretchLastSection(True)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.itemChanged.connect(self._on_item_changed)

        read = QPushButton("Read from node")
        read.setToolTip(
            "Read what the node is actually configured to send and receive,\n"
            "rather than what its EDS says it was built with."
        )
        read.clicked.connect(self._read)
        add = QPushButton("Map object...")
        add.setToolTip("Add an object from the dictionary to this PDO's contents")
        add.clicked.connect(self._add_entry)
        remove = QPushButton("Unmap")
        remove.setToolTip("Take the selected object out of this PDO")
        remove.clicked.connect(self._remove_entry)
        write = QPushButton("Write to node")
        write.setToolTip("Write the selected PDO's communication and mapping parameters")
        write.clicked.connect(self._write)
        bar = QHBoxLayout()
        for b in (read, add, remove, write):
            bar.addWidget(b)
        bar.addStretch()
        bar.addWidget(QLabel("Edit a cell, then Write to node"))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.addLayout(bar)
        layout.addWidget(self.tree)

        manager.pdo_config.connect(self._on_config_changed)

    # --- population -------------------------------------------------------------
    @Slot(int)
    def set_node(self, node_id: int | None) -> None:
        self.node_id = node_id
        self.refresh()

    @Slot(int)
    def _on_config_changed(self, node_id: int) -> None:
        if node_id == self.node_id:
            self.refresh()

    def refresh(self) -> None:
        self._updating = True
        self.tree.clear()
        if self.node_id is not None:
            for config in self.manager.pdo_configs(self.node_id):
                self.tree.addTopLevelItem(self._make_row(config))
        self._updating = False

    def _make_row(self, config: PdoConfig) -> QTreeWidgetItem:
        item = QTreeWidgetItem(
            [
                f"{config.direction}{config.number}  {config.name}",
                f"{config.cob_id:03X}",
                "",
                "" if config.transmission_type is None else str(config.transmission_type),
                "" if config.inhibit_time_us is None else str(config.inhibit_time_us),
                "" if config.event_timer_ms is None else str(config.event_timer_ms),
                str(config.bits),
            ]
        )
        item.setData(0, ROLE_CONFIG, config)
        item.setFlags(item.flags() | Qt.ItemIsEditable | Qt.ItemIsUserCheckable)
        item.setCheckState(COL_ENABLED, Qt.Checked if config.enabled else Qt.Unchecked)
        item.setToolTip(COL_TRANS, config.transmission_text())
        for entry in config.entries:
            child = QTreeWidgetItem(
                [
                    f"    {entry.index:04X}:{entry.subindex:02X}  {entry.name}",
                    "",
                    "",
                    "",
                    "",
                    "",
                    str(entry.bits),
                ]
            )
            child.setData(0, ROLE_ENTRY, entry)
            item.addChild(child)
        item.setExpanded(True)
        return item

    # --- selection helpers ---------------------------------------------------------
    def _selected_row(self) -> QTreeWidgetItem | None:
        items = self.tree.selectedItems()
        if not items:
            return None
        item = items[0]
        return item if item.parent() is None else item.parent()

    def _config_of(self, item: QTreeWidgetItem) -> PdoConfig:
        return item.data(0, ROLE_CONFIG)

    # --- editing ---------------------------------------------------------------------
    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._updating or item.parent() is not None:
            return
        config = self._config_of(item)
        self._updating = True
        try:
            if column == COL_COBID:
                config.cob_id = int(item.text(COL_COBID), 16)
            elif column == COL_ENABLED:
                config.enabled = item.checkState(COL_ENABLED) == Qt.Checked
            elif column == COL_TRANS:
                config.transmission_type = _optional_int(item.text(COL_TRANS))
                item.setToolTip(COL_TRANS, config.transmission_text())
            elif column == COL_INHIBIT:
                config.inhibit_time_us = _optional_int(item.text(COL_INHIBIT))
            elif column == COL_TIMER:
                config.event_timer_ms = _optional_int(item.text(COL_TIMER))
        except ValueError:
            self.ctx.warn(f"PDO: {item.text(column)!r} is not a valid number")
            self.refresh()
        self._updating = False

    def _add_entry(self) -> None:
        item = self._selected_row()
        if item is None or self.node_id is None:
            self.ctx.warn("PDO: select a PDO first")
            return
        dialog = ObjectPicker(self.manager, self.node_id, self)
        if dialog.exec() == QDialog.Accepted and (entry := dialog.chosen()):
            config = self._config_of(item)
            if config.bits + entry.bits > 64:
                self.ctx.log("PDO: a PDO holds at most 64 bits")
                return
            config.entries.append(entry)
            self.refresh()

    def _remove_entry(self) -> None:
        items = self.tree.selectedItems()
        if not items or items[0].parent() is None:
            self.ctx.log("PDO: select a mapped object to unmap")
            return
        child = items[0]
        config = self._config_of(child.parent())
        entry = child.data(0, ROLE_ENTRY)
        config.entries = [e for e in config.entries if e != entry]
        self.refresh()

    def _read(self) -> None:
        if self.node_id is None:
            return
        self.manager.read_pdo_config(self.node_id)

    def _write(self) -> None:
        item = self._selected_row()
        if item is None:
            self.ctx.log("PDO: select a PDO to write")
            return
        self.manager.write_pdo_config(self._config_of(item))


def _optional_int(text: str) -> int | None:
    text = text.strip()
    return int(text, 0) if text else None
