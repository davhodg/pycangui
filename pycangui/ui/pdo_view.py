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
from pycangui.canopen.manager import CanopenManager, mappable, mapped_bits, od_entries
from pycangui.core.classify import predefined_meaning
from pycangui.core.context import Context
from pycangui.ui import messages

COL_NAME, COL_COBID, COL_ENABLED, COL_TRANS, COL_INHIBIT, COL_TIMER, COL_BITS = range(7)

#: Eight bytes, which is what a classic CAN frame carries.
PDO_BITS = 64


def cob_id_tip(cob_id: int) -> str:
    """What CiA 301 gives this identifier to, offered as a convention.

    Deliberately a tooltip rather than the PDO's name. The predefined
    connection set is how a bus works before anybody configures it, so
    reading it backwards off a configured node is how a TPDO at 0x151 came
    to be labelled as node 81's RPDO -- true about the number, and about
    nothing else on the screen. Somebody who wants to know what an
    identifier means by convention can ask for it; nobody should have to
    read it as a claim about the node in front of them.
    """
    meaning = predefined_meaning(cob_id)
    if not meaning:
        return (
            f"0x{cob_id:03X} is not one of the identifiers CiA 301 gives a\n"
            "meaning to. Nothing is wrong with that: a PDO can be given any\n"
            "identifier its node will accept."
        )
    return (
        f"CiA 301's predefined connection set gives 0x{cob_id:03X} to\n"
        f"{meaning}.\n\n"
        "That is what the number means by convention on a bus nobody has\n"
        "configured, not what this PDO carries. The mapped objects\n"
        "underneath say that."
    )


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
                if index < 0x2000 or not mappable(var):
                    # Communication-profile objects, the container row an
                    # array or record puts in front of its subs, and
                    # anything whose length is not known in advance: a PDO
                    # has a fixed layout, so a string or a domain has no
                    # length to write into a mapping entry.
                    continue
                bits = mapped_bits(var)
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
        #: The configuration being edited, which is not the node's until
        #: Write to node. Held here rather than read back from the manager
        #: on every redraw: rebuilding the tree used to ask the manager
        #: again, which handed back what the node still says and threw away
        #: whatever had just been mapped or unmapped.
        self._configs: list[PdoConfig] = []

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
        remove = QPushButton("Unmap object")
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
        """Ask the node again, losing anything not yet written to it."""
        self._configs = [] if self.node_id is None else self.manager.pdo_configs(self.node_id)
        self._redraw()

    def _redraw(self) -> None:
        """Show the working copy, keeping edits that are not written yet.

        The tree is rebuilt rather than patched, so the selection has to be
        put back by hand: without it, unmapping one object deselects the
        PDO, and unmapping a second means finding and clicking it again.
        """
        where = self._selected_place()
        self._updating = True
        self.tree.clear()
        for config in self._configs:
            self.tree.addTopLevelItem(self._make_row(config))
        self._updating = False
        self._select_place(where)

    def _selected_place(self) -> tuple[int, int] | None:
        """Where the selection is, as (PDO row, object row or -1)."""
        items = self.tree.selectedItems()
        if not items:
            return None
        item = items[0]
        parent = item.parent()
        if parent is None:
            return (self.tree.indexOfTopLevelItem(item), -1)
        return (self.tree.indexOfTopLevelItem(parent), parent.indexOfChild(item))

    def _select_place(self, where: tuple[int, int] | None) -> None:
        """Select it again, or the PDO alone where the object has gone.

        Unmapping the last object of a PDO leaves the PDO selected, which
        is what somebody who has just emptied it is looking at.
        """
        if where is None:
            return
        row, child = where
        item = self.tree.topLevelItem(row)
        if item is None:
            return
        if 0 <= child < item.childCount():
            item = item.child(child)
        self.tree.setCurrentItem(item)

    def _make_row(self, config: PdoConfig) -> QTreeWidgetItem:
        item = QTreeWidgetItem(
            [
                f"{config.direction}{config.number}",
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
        item.setToolTip(COL_COBID, cob_id_tip(config.cob_id))
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
                item.setToolTip(COL_COBID, cob_id_tip(config.cob_id))
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
        if dialog.exec() != QDialog.Accepted or not (entry := dialog.chosen()):
            return
        config = self._config_of(item)
        if config.bits + entry.bits > PDO_BITS:
            # A box, not a line in the Event Log. This is the direct answer
            # to a button somebody has just pressed, and an answer that
            # appears in a pane they may not have open is no answer: the
            # object simply failed to arrive, for no stated reason.
            messages.warning(
                self,
                f"{config.direction}{config.number} is full",
                f"It already carries {config.bits} of {PDO_BITS} bits, and "
                f"{entry.name} needs {entry.bits} more.\n\n"
                "A PDO is eight bytes. Unmap an object first, or put this "
                "object in another PDO.",
            )
            return
        config.entries.append(entry)
        self._redraw()

    def _remove_entry(self) -> None:
        items = self.tree.selectedItems()
        if not items or items[0].parent() is None:
            messages.warning(
                self,
                "Nothing to unmap",
                "Select a mapped object -- one of the rows underneath a PDO -- "
                "rather than the PDO itself.",
            )
            return
        child = items[0]
        parent = child.parent()
        config = self._config_of(parent)
        # By position rather than by value: the same object can legitimately
        # be mapped twice, and dropping every entry equal to this one would
        # take out the copy that was not selected.
        where = parent.indexOfChild(child)
        if 0 <= where < len(config.entries):
            del config.entries[where]
        self._redraw()

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
