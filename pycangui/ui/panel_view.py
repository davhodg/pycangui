"""A panel on screen: a named group of objects, laid out as a form.

The pane is thin on purpose.  It builds a widget per field, points every one
of them at whatever source the panel is bound to, and gets out of the way --
the refusing, the scaling and the read-modify-write all belong to the widgets,
and where the values come from belongs to the source.  What is left here is
the layout, the source selector, and Read.

Which source is the one thing on screen that a panel does not carry in its
file.  A panel is a statement about a *product*, and which controller you are
pointing it at this afternoon is not.  So the file names a node as a default
and the selector at the top overrides it, including with a file -- the same
panel over a DCF is how you build a configuration at a desk.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pycangui.core.context import Context
from pycangui.panels import model
from pycangui.panels.model import Field, Panel
from pycangui.panels.source import FileSource, NodeSource, Source
from pycangui.ui import panel_widgets

#: Offered in the source selector, above whatever nodes are on the bus.
FILE_ENTRY = "Open a DCF or EDS..."

READ_TIP = "Read every object on this panel again."
EDIT_TIP = (
    "Change the labels, the order and how each object is shown.\n"
    "Objects are added from the CANopen pane: pick them in the object\n"
    "dictionary and use Add to panel."
)
SOURCE_TIP = (
    "Where the values come from and go to: a node on the bus, or a DCF or\n"
    "EDS file.  The same panel over a file is how a configuration is built\n"
    "at a desk and taken to the machine."
)


class PanelView(QWidget):
    """One panel, bound to one source at a time."""

    #: The panel was edited here and should be written back.
    changed = Signal()

    def __init__(self, name: str, panel: Panel, manager, ctx: Context) -> None:
        super().__init__()
        self.name = name
        self.panel = panel
        self.manager = manager
        self.ctx = ctx
        self.source: Source | None = None
        self._widgets: list[panel_widgets.FieldWidget] = []

        self.heading = QLabel()
        self.heading.setStyleSheet("font-weight: bold")
        self.note = QLabel()
        self.note.setWordWrap(True)

        self.sources = QComboBox()
        self.sources.setToolTip(SOURCE_TIP)
        self.sources.activated.connect(self._on_source_chosen)

        read_all = QPushButton("Read")
        read_all.setToolTip(READ_TIP)
        read_all.clicked.connect(self.refresh)
        edit = QPushButton("Edit...")
        edit.setToolTip(EDIT_TIP)
        edit.clicked.connect(self._edit)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("Values from:"))
        bar.addWidget(self.sources, 1)
        bar.addWidget(read_all)
        bar.addWidget(edit)

        self.form_holder = QWidget()
        self.form = QFormLayout(self.form_holder)
        self.form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        layout = QVBoxLayout(self)
        layout.addWidget(self.heading)
        layout.addWidget(self.note)
        layout.addLayout(bar)
        layout.addWidget(self.form_holder)
        layout.addStretch()

        self.rebuild()
        if manager is not None:
            manager.node_seen.connect(lambda *_a: self._fill_sources())

    # --- the form ------------------------------------------------------------------
    def rebuild(self) -> None:
        """Build a widget per field, and say what is wrong where anything is."""
        self.heading.setText(self.panel.title or self.name)
        problems = model.problems(self.panel)
        self.note.setText("\n".join([self.panel.description, *problems]).strip())
        self.note.setVisible(bool(self.note.text()))
        self.note.setStyleSheet("color: #c02020" if problems else "")

        while self.form.count():
            item = self.form.takeAt(0)
            if (widget := item.widget()) is not None:
                widget.deleteLater()
        self._widgets.clear()

        for item in self.panel.fields:
            display = self.source.display(item.index, item.sub) if self.source else None
            widget = panel_widgets.build(item, display or _no_display())
            widget.read_requested.connect(self._on_read_requested)
            widget.write_requested.connect(self._on_write_requested)
            widget.message.connect(self.ctx.warn)
            self.form.addRow(QLabel(widget.label_text() + ":"), widget)
            self._widgets.append(widget)
        self._apply_writable()
        self._fill_sources()

    def refresh(self) -> None:
        for widget in self._widgets:
            widget.refresh()

    # --- where the values come from ----------------------------------------------------
    def _fill_sources(self) -> None:
        """The nodes on the bus, plus a way to open a file."""
        current = self.sources.currentData()
        self.sources.blockSignals(True)
        self.sources.clear()
        nodes = sorted(self.manager.nodes()) if hasattr(self.manager, "nodes") else []
        if self.panel.node is not None and self.panel.node not in nodes:
            nodes = sorted({*nodes, self.panel.node})
        for node_id in nodes:
            self.sources.addItem(f"Node {node_id}", node_id)
        if isinstance(self.source, FileSource):
            self.sources.addItem(self.source.label, str(self.source.path))
        self.sources.addItem(FILE_ENTRY, FILE_ENTRY)
        at = self.sources.findData(current if current is not None else self.panel.node)
        self.sources.setCurrentIndex(max(at, 0))
        self.sources.blockSignals(False)

    def _on_source_chosen(self, _at: int) -> None:
        chosen = self.sources.currentData()
        if chosen == FILE_ENTRY:
            self._choose_file()
            return
        if isinstance(chosen, int):
            self.bind(NodeSource(self.manager, chosen))
        elif isinstance(chosen, str):
            self.bind(FileSource(chosen, hooks=getattr(self.manager, "_hooks", None)))

    def _choose_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Values from a file",
            str(self.ctx.eds_dir),
            "Device configuration (*.dcf *.eds);;All files (*)",
        )
        if not path:
            self._fill_sources()  # put the selector back on whatever it was
            return
        self.bind(FileSource(path, hooks=getattr(self.manager, "_hooks", None)))

    def bind(self, source: Source | None) -> None:
        """Point this panel at a node, a file, or nothing at all."""
        if self.source is not None:
            try:
                self.source.value.disconnect(self._on_value)
            except (RuntimeError, TypeError):
                pass  # never connected, or already gone
        self.source = source
        if source is not None:
            source.value.connect(self._on_value)
        # The display comes from the source, so the labels and the units may
        # be different ones now: rebuilt rather than patched.
        self.rebuild()
        if source is not None:
            self.refresh()

    def _apply_writable(self) -> None:
        writable = bool(self.source is not None and self.source.writable)
        for widget in self._widgets:
            widget.set_writable(writable)

    # --- and back again ------------------------------------------------------------------
    def _on_read_requested(self, index: int, sub: int) -> None:
        if self.source is None:
            self.ctx.warn(f"{self.panel.title or self.name}: no node or file selected")
            return
        self.source.request(index, sub)

    def _on_write_requested(self, index: int, sub: int, raw: Any) -> None:
        if self.source is None:
            self.ctx.warn(f"{self.panel.title or self.name}: no node or file selected")
            return
        self.source.write(index, sub, raw)

    def _on_value(self, index: int, sub: int, raw: Any, error: Any) -> None:
        # Handed to every widget rather than routed: a map wants many
        # sub-indices of one object, and only it knows which.
        for widget in self._widgets:
            widget.set_value(index, sub, raw, error)

    # --- changing the panel itself ----------------------------------------------------------
    def _edit(self) -> None:
        dialog = PanelEditor(self, self.panel)
        if dialog.exec() != QDialog.Accepted:
            return
        self.panel = dialog.result_panel()
        model.save(self.name, self.panel)
        self.rebuild()
        if self.source is not None:
            self.refresh()
        self.changed.emit()

    def add_field(self, item: Field) -> None:
        """Put another object on this panel, from wherever it was picked."""
        self.panel = self.panel.with_field(item)
        model.save(self.name, self.panel)
        self.rebuild()
        if self.source is not None:
            self.refresh()
        self.changed.emit()


def _no_display():
    from pycangui.canopen.display import Display

    return Display()


class PanelEditor(QDialog):
    """The labels, the order and how each object is shown.

    Not where objects are added: they are picked in the object dictionary,
    which is where they can be searched for and where their names already are.
    Typing an index into a dialog is the thing this feature exists to avoid.
    """

    COLUMNS = ("Object", "Label", "Shown as")

    def __init__(self, parent: QWidget | None, panel: Panel) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit panel")
        self.resize(560, 420)
        self._fields = list(panel.fields)

        self.title = QLineEdit(panel.title)
        self.description = QLineEdit(panel.description)
        self.node = QLineEdit("" if panel.node is None else str(panel.node))
        self.node.setToolTip(
            "The node this panel usually opens against.  Only a default -- the\n"
            "selector on the panel itself decides where the values come from."
        )

        details = QFormLayout()
        details.addRow("Title:", self.title)
        details.addRow("Note:", self.description)
        details.addRow("Usual node:", self.node)

        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.verticalHeader().setVisible(False)

        up = QPushButton("Move up")
        up.clicked.connect(lambda: self._move(-1))
        down = QPushButton("Move down")
        down.clicked.connect(lambda: self._move(1))
        remove = QPushButton("Remove")
        remove.clicked.connect(self._remove)
        buttons = QHBoxLayout()
        for button in (up, down, remove):
            buttons.addWidget(button)
        buttons.addStretch()

        closer = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        closer.accepted.connect(self._accept)
        closer.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(details)
        layout.addWidget(self.table)
        layout.addLayout(buttons)
        layout.addWidget(
            QLabel(
                "Anything this dialog does not cover -- named bits, map axes -- "
                "is in the panel's file, which is JSON and meant to be edited."
            )
        )
        layout.addWidget(closer)
        self._fill()

    def _fill(self) -> None:
        self.table.setRowCount(len(self._fields))
        for row, item in enumerate(self._fields):
            where = QTableWidgetItem(f"0x{item.index:04X}:{item.sub:02X}")
            where.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.table.setItem(row, 0, where)
            self.table.setItem(row, 1, QTableWidgetItem(item.label))
            kinds = QComboBox()
            kinds.addItems(model.KINDS)
            kinds.setCurrentText(item.kind)
            self.table.setCellWidget(row, 2, kinds)

    def _current(self) -> int:
        return self.table.currentRow()

    def _move(self, by: int) -> None:
        row = self._current()
        if row < 0 or not 0 <= row + by < len(self._fields):
            return
        self._collect()
        self._fields[row], self._fields[row + by] = self._fields[row + by], self._fields[row]
        self._fill()
        self.table.setCurrentCell(row + by, 0)

    def _remove(self) -> None:
        row = self._current()
        if row < 0:
            return
        self._collect()
        del self._fields[row]
        self._fill()

    def _collect(self) -> None:
        """Take what is typed in the table back into the fields."""
        from dataclasses import replace

        for row, item in enumerate(self._fields):
            label = self.table.item(row, 1)
            kinds = self.table.cellWidget(row, 2)
            self._fields[row] = replace(
                item,
                label=label.text().strip() if label else item.label,
                kind=kinds.currentText() if kinds else item.kind,
            )

    def _accept(self) -> None:
        self._collect()
        if not self.title.text().strip():
            QMessageBox.warning(self, "The panel needs a title", "It is what names its window.")
            return
        self.accept()

    def result_panel(self) -> Panel:
        node = self.node.text().strip()
        return Panel(
            title=self.title.text().strip(),
            description=self.description.text().strip(),
            fields=list(self._fields),
            node=int(node, 0) if node.isdigit() or node.lower().startswith("0x") else None,
        )
