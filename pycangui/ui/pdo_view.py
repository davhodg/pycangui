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

from canopen.objectdictionary import ODVariable
from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pycangui.canopen import PdoConfig, PdoEntry, pdo_file
from pycangui.canopen.manager import CanopenManager, mappable, mapped_bits, od_entries
from pycangui.core.classify import predefined_meaning
from pycangui.core.context import Context
from pycangui.ui import fonts, messages
from pycangui.ui.column_widths import ColumnWidths
from pycangui.ui.edit_columns import EditColumns

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
#: A mapped size that is not the size of the object's type: amber, as a warning is.
MISMATCH_COLOUR = QColor(200, 120, 0)
ROLE_CONFIG = Qt.UserRole  # PdoConfig on top-level rows
ROLE_ENTRY = Qt.UserRole + 1  # PdoEntry on child rows


class ObjectPicker(QDialog):
    """Pick an object from the node's dictionary to map into a PDO."""

    def __init__(
        self,
        manager: CanopenManager,
        node_id: int | None,
        parent: QWidget | None = None,
        dictionary=None,
    ) -> None:
        """From a node's dictionary, or from ``dictionary`` -- a file's."""
        super().__init__(parent)
        self.setWindowTitle("Map an object")
        self.resize(520, 460)
        self.entries: list[PdoEntry] = []
        self.search = QLineEdit()
        self.search.setPlaceholderText("filter...")
        self.search.textChanged.connect(self._filter)
        self.list = QListWidget()
        self.list.setFont(fonts.mono())
        self.list.itemDoubleClicked.connect(lambda _i: self.accept())
        if dictionary is None and node_id is not None:
            node = manager.node(node_id)
            dictionary = None if node is None else node.object_dictionary
        if dictionary is not None:
            for index, sub, var, name in od_entries(dictionary):
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
    #: Save file was pressed, for a file's PDOs: saving is the pane's to do,
    #: since it is the same Save as the one under the node list.
    save_requested = Signal()

    def __init__(self, manager: CanopenManager, ctx: Context) -> None:
        super().__init__()
        self.manager = manager
        self.ctx = ctx
        self.node_id: int | None = None
        #: A DCF or EDS open with no node, shown in place of one.
        self._file = None
        self._updating = False
        #: The configuration being edited, which is not the node's until
        #: Write to node. Held here rather than read back from the manager
        #: on every redraw: rebuilding the tree used to ask the manager
        #: again, which handed back what the node still says and threw away
        #: whatever had just been mapped or unmapped.
        self._configs: list[PdoConfig] = []

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(HEADERS)
        self.tree.setFont(fonts.mono())
        self.widths = ColumnWidths(self.tree, ctx.settings, "canopen.pdo_config")
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        # The PDO's settings are typed into; its name and size are not.
        self.tree.setItemDelegate(
            EditColumns((COL_COBID, COL_TRANS, COL_INHIBIT, COL_TIMER), self.tree)
        )
        self.tree.itemChanged.connect(self._on_item_changed)

        self.read_btn = read = QPushButton()
        read.clicked.connect(self._read)
        add = QPushButton("Map object...")
        add.setToolTip(
            "Add an object from the dictionary to this PDO: after the selected\n"
            "mapped object, or at the end with the PDO itself selected."
        )
        add.clicked.connect(self._add_entry)
        remove = QPushButton("Unmap object")
        remove.setToolTip("Take the selected object out of this PDO")
        remove.clicked.connect(self._remove_entry)
        # The order is the layout of the frame: the first object mapped is
        # the first bits sent.
        up = QPushButton("Move up")
        up.setToolTip("Move the selected object earlier in the PDO: lower bits of the frame")
        up.clicked.connect(lambda: self._move_entry(-1))
        down = QPushButton("Move down")
        down.setToolTip("Move the selected object later in the PDO: higher bits of the frame")
        down.clicked.connect(lambda: self._move_entry(1))
        self.write_btn = write = QPushButton()
        write.clicked.connect(self._write)
        self.hint = QLabel()
        bar = QHBoxLayout()
        for b in (read, add, remove, up, down, write):
            bar.addWidget(b)
        bar.addStretch()
        bar.addWidget(self.hint)
        self._name_buttons()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.addLayout(bar)
        layout.addWidget(self.tree)

        manager.pdo_config.connect(self._on_config_changed)

    # --- population -------------------------------------------------------------
    @Slot(int)
    def set_node(self, node_id: int | None) -> None:
        self.node_id = node_id
        self._file = None
        self._name_buttons()
        self.refresh()

    def set_file(self, source) -> None:
        """Show a DCF or EDS's PDOs, with no node: read from its objects, and
        put back into them, where they wait to be saved like any other edit."""
        self.node_id = None
        self._file = source
        self._name_buttons()
        self.refresh()

    def _name_buttons(self) -> None:
        """Say where reading comes from and writing goes: the node, or the file."""
        if self._file is None:
            self.read_btn.setText("Read from node")
            self.read_btn.setToolTip(
                "Read what the node is actually configured to send and receive,\n"
                "rather than what its EDS says it was built with."
            )
            self.write_btn.setText("Write to node")
            self.write_btn.setToolTip(
                "Write the selected PDO's communication and mapping parameters"
            )
            self.write_btn.setEnabled(True)
            self.hint.setText("Edit a cell, then Write to node")
        else:
            self.read_btn.setText("Read from file")
            self.read_btn.setToolTip("Show the PDOs as the file's objects have them now.")
            # In a file, each change goes into its objects as it is made, like
            # a value typed into the object dictionary, so there is nothing to
            # put anywhere first: what writes is saving the file. A second step
            # before Save was a step to forget, and closing the file then lost
            # the change without a word.
            self.write_btn.setText("Save file")
            self.write_btn.setToolTip(
                "Write the file to disk, with every change in it -- the PDOs here\n"
                "and the values in the object dictionary. The same as Save under\n"
                "the node list."
            )
            self.offer_save()
            self.hint.setText("Changes go into the file as they are made")

    @Slot(int)
    def _on_config_changed(self, node_id: int) -> None:
        if node_id == self.node_id:
            self.refresh()

    def refresh(self) -> None:
        """Ask the node again, losing anything not yet written to it."""
        if self._file is not None:
            self._configs = pdo_file.configs(self._file)
        elif self.node_id is not None:
            self._configs = self.manager.pdo_configs(self.node_id)
        else:
            self._configs = []
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
        if config.cob_id_text:
            item.setToolTip(
                COL_COBID,
                f"The file gives this as {config.cob_id_text}: the node ID is added\n"
                "by the node, and there is none here. Left as it is unless changed.",
            )
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
            type_bits = self._type_bits(entry)
            if type_bits is not None and type_bits != entry.bits:
                # Mapped at a size the object's type in the EDS does not have:
                # one of the two is wrong about it, or part of it is mapped.
                child.setText(COL_BITS, f"{entry.bits} (type {type_bits})")
                child.setForeground(COL_BITS, MISMATCH_COLOUR)
                child.setToolTip(
                    COL_BITS,
                    f"Mapped as {entry.bits} bits, and the EDS types this object as "
                    f"{type_bits}.\nDecoded, and sent, as the {entry.bits} bits mapped: either "
                    "the node maps part of the\nobject, or the EDS has its type wrong.",
                )
            item.addChild(child)
        item.setExpanded(True)
        return item

    def _type_bits(self, entry: PdoEntry) -> int | None:
        """The size the EDS's data type gives a mapped object, or None if unknown."""
        if self._file is not None:
            dictionary = self._file.object_dictionary
        else:
            node = None if self.node_id is None else self.manager.node(self.node_id)
            dictionary = getattr(node, "object_dictionary", None)
        try:
            obj = dictionary[entry.index]
            var = obj if isinstance(obj, ODVariable) else obj[entry.subindex]
            return mapped_bits(var)
        except (KeyError, TypeError, IndexError):
            return None

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
            else:
                return
        except ValueError:
            messages.warning(
                self, "Not changed", f"{item.text(column)!r} is not a number this can hold."
            )
            self.refresh()
            return
        finally:
            self._updating = False
        self._into_file(config)

    def _into_file(self, config: PdoConfig) -> bool:
        """In a file, put the change into its objects now. False if it would not go.

        Refused, the file is left as it was and the tree goes back to it, so
        what is on the screen is never something the file does not hold.
        """
        if self._file is None:
            return True
        if (why_not := pdo_file.apply(self._file, config)) != "":
            messages.warning(self, f"{config.direction}{config.number} not changed", why_not)
            self.refresh()
            return False
        self.refresh()
        self.offer_save()
        return True

    def _add_entry(self) -> None:
        item = self._selected_row()
        if item is None or (self.node_id is None and self._file is None):
            self.ctx.warn("PDO: select a PDO first")
            return
        dictionary = None if self._file is None else self._file.object_dictionary
        dialog = ObjectPicker(self.manager, self.node_id, self, dictionary)
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
        # After the object selected, which is where somebody looking at a
        # mapping expects the new one; at the end with the PDO selected.
        picked = self.tree.selectedItems()
        child = picked[0] if picked and picked[0].parent() is item else None
        at = item.indexOfChild(child) + 1 if child is not None else len(config.entries)
        row = self.tree.indexOfTopLevelItem(item)  # the item goes with the redraw
        config.entries.insert(at, entry)
        self._redraw()
        if self._into_file(config):
            self._select_place((row, at))

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
        self._into_file(config)

    def _move_entry(self, step: int) -> None:
        """Move the selected mapped object one place earlier or later."""
        items = self.tree.selectedItems()
        if not items or items[0].parent() is None:
            messages.warning(
                self,
                "Nothing to move",
                "Select a mapped object -- one of the rows underneath a PDO.",
            )
            return
        child = items[0]
        parent = child.parent()
        config = self._config_of(parent)
        row = self.tree.indexOfTopLevelItem(parent)
        where = parent.indexOfChild(child)
        to = where + step
        if not 0 <= to < len(config.entries):
            return  # already first, or last
        entries = config.entries
        entries[where], entries[to] = entries[to], entries[where]
        self._redraw()
        if self._into_file(config):
            self._select_place((row, to))

    def _read(self) -> None:
        if self._file is not None:
            self.refresh()
        elif self.node_id is not None:
            self.manager.read_pdo_config(self.node_id)

    def offer_save(self) -> None:
        """Save file only while the file has something to save."""
        if self._file is not None:
            self.write_btn.setEnabled(self._file.unsaved)

    def _write(self) -> None:
        if self._file is not None:
            self.save_requested.emit()
            return
        item = self._selected_row()
        if item is None:
            messages.information(self, "Nothing to write", "Select the PDO to write first.")
            return
        self.manager.write_pdo_config(self._config_of(item))


def _optional_int(text: str) -> int | None:
    text = text.strip()
    return int(text, 0) if text else None
