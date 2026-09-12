# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""A custom pane on screen: a named group of objects, laid out as a form.

The pane is thin on purpose.  It builds a widget per field, points every one
of them at whatever source the pane is bound to, and gets out of the way --
the refusing, the scaling and the read-modify-write all belong to the widgets,
and where the values come from belongs to the source.  What is left here is
the layout, the source selector, and Read.

Which source is the one thing on screen that a pane does not carry in its
file.  A custom pane is a statement about a *product*, and which controller you are
pointing it at this afternoon is not.  So the file names a node as a default
and the selector at the top overrides it, including with a file -- the same
pane over a DCF is how you build a configuration at a desk.
"""

from __future__ import annotations

import time
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pycangui.core.context import Context
from pycangui.custom_panes import model
from pycangui.custom_panes.model import CustomPane, Field
from pycangui.custom_panes.polling import DEFAULT_HZ, MAX_HZ, MIN_HZ, Poller, rate_text
from pycangui.custom_panes.source import FileSource, NodeSource, Source
from pycangui.ui import field_widgets, folders
from pycangui.ui.persist import remember

#: How many unanswered reads of one object to keep track of.  Past this,
#: the oldest are ones whose answers are never coming.
MAX_WAITING = 8

#: Offered in the source selector, above whatever nodes are on the bus.
FILE_ENTRY = "Open a DCF or EDS..."

READ_TIP = "Read every object on this pane again."
EDIT_TIP = (
    "Add objects to this pane, and change their labels, their order and\n"
    "how each one is shown.  Objects can also be picked in the CANopen\n"
    "pane's object dictionary, with Add to pane."
)
SOURCE_TIP = (
    "Where the values come from and go to: a node on the bus, or a DCF or\n"
    "EDS file.  The same pane over a file is how a configuration is built\n"
    "at a desk and taken to the machine."
)
POLL_TIP = (
    "Read every object on this pane over and over, so the values follow\n"
    "the controller.  The figure beside the box is the rate actually being\n"
    "achieved, which for SDO reads is often well below the one asked for.\n"
    "Numeric values go to Signals and Plot while this is on, so a polled\n"
    "object plots and exports like any other signal."
)
RATE_TIP = (
    "How often to read the whole pane, at most.  A round only starts when\n"
    "the last one has finished, so asking for more than the bus can do gets\n"
    "you as fast as it can rather than a backlog of stale values."
)


class CustomPaneView(QWidget):
    """One pane, bound to one source at a time."""

    #: The pane was edited here and should be written back.
    changed = Signal()

    def __init__(
        self, name: str, pane: CustomPane, manager, ctx: Context, signals=None, now=None
    ) -> None:
        super().__init__()
        self.name = name
        self.pane = pane
        self.manager = manager
        self.ctx = ctx
        #: Where polled values go, so that one plots and exports like any other
        #: signal rather than being a number that only exists on this form.
        self.signals = signals
        self._now = now or time.monotonic
        self.source: Source | None = None
        self._widgets: list[field_widgets.FieldWidget] = []
        #: Per object, oldest first: True where polling asked for the read
        #: and False where a person did.  See ``_request``.
        self._asked: dict[tuple[int, int], list[bool]] = {}
        self.poller = Poller(self)
        self.poller.read.connect(self._on_poll_read)
        # rate is connected once the label it writes to exists, below.

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

        self.poll = QCheckBox("Poll")
        self.poll.setToolTip(POLL_TIP)
        self.poll.toggled.connect(self._on_poll_toggled)
        self.poll_hz = QDoubleSpinBox()
        self.poll_hz.setRange(MIN_HZ, MAX_HZ)
        self.poll_hz.setDecimals(2)
        self.poll_hz.setSingleStep(0.5)
        self.poll_hz.setSuffix(" Hz")
        self.poll_hz.setToolTip(RATE_TIP)
        remember(self.ctx, f"pane.{self.name}.poll_hz", self.poll_hz, DEFAULT_HZ)
        self.poll_hz.valueChanged.connect(self.poller.set_rate)
        self.poller.set_rate(self.poll_hz.value())
        #: The rate actually being managed.  Shown rather than the requested
        #: one, because a value read at 12 Hz that looks like it was read at
        #: 100 is the sort of thing people build conclusions on.
        self.poll_rate = QLabel("")
        self.poll_rate.setToolTip("The rate this pane is actually being read at.")
        self.poller.rate.connect(self._on_rate)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("Values from:"))
        bar.addWidget(self.sources, 1)
        bar.addWidget(read_all)
        bar.addWidget(self.poll)
        bar.addWidget(self.poll_hz)
        bar.addWidget(self.poll_rate)
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
        self.heading.setText(self.pane.title or self.name)
        problems = model.problems(self.pane)
        self.note.setText("\n".join([self.pane.description, *problems]).strip())
        self.note.setVisible(bool(self.note.text()))
        self.note.setStyleSheet("color: #c02020" if problems else "")

        while self.form.count():
            item = self.form.takeAt(0)
            if (widget := item.widget()) is not None:
                widget.deleteLater()
        self._widgets.clear()

        for item in self.pane.fields:
            display = self.source.display(item.index, item.sub) if self.source else None
            widget = field_widgets.build(item, display or _no_display())
            widget.read_requested.connect(self._on_read_requested)
            widget.write_requested.connect(self._on_write_requested)
            widget.message.connect(self.ctx.warn)
            self.form.addRow(QLabel(widget.label_text() + ":"), widget)
            self._widgets.append(widget)
        self._apply_writable()
        self.poller.set_objects(f.where for f in self.pane.fields)
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
        if self.pane.node is not None and self.pane.node not in nodes:
            nodes = sorted({*nodes, self.pane.node})
        for node_id in nodes:
            self.sources.addItem(f"Node {node_id}", node_id)
        if isinstance(self.source, FileSource):
            self.sources.addItem(self.source.label, str(self.source.path))
        self.sources.addItem(FILE_ENTRY, FILE_ENTRY)
        at = self.sources.findData(current if current is not None else self.pane.node)
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
        path = folders.open_file(
            self,
            self.ctx,
            folders.EDS,
            "Values from a file",
            "Device configuration (*.dcf *.eds);;All files (*)",
            self.ctx.eds_dir,
        )
        if not path:
            self._fill_sources()  # put the selector back on whatever it was
            return
        self.bind(FileSource(path, hooks=getattr(self.manager, "_hooks", None)))

    def bind(self, source: Source | None) -> None:
        """Point this pane at a node, a file, or nothing at all."""
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
        self._apply_live()

    def _apply_live(self) -> None:
        """A file does not change under you, so polling one is not offered."""
        live = bool(self.source is not None and getattr(self.source, "live", False))
        for widget in (self.poll, self.poll_hz):
            widget.setEnabled(live)
        if not live and self.poller.running:
            self.poll.setChecked(False)
        self.poll.setToolTip(
            POLL_TIP if live else "Only a node changes while you watch it; a file does not."
        )

    # --- polling ------------------------------------------------------------------------
    def _on_poll_toggled(self, on: bool) -> None:
        if on and self.source is None:
            self.ctx.warn(f"{self.pane.title or self.name}: no node or file selected")
            self.poll.setChecked(False)
            return
        self.poller.start(self.poll_hz.value()) if on else self.poller.stop()

    def _on_rate(self, requested: float, achieved: float) -> None:
        self.poll_rate.setText(rate_text(requested, achieved, self.poller.running))

    # --- and back again ------------------------------------------------------------------
    def _on_poll_read(self, index: int, sub: int) -> None:
        self._request(index, sub, polling=True)

    def _on_read_requested(self, index: int, sub: int) -> None:
        self._request(index, sub, polling=False)

    def _request(self, index: int, sub: int, polling: bool) -> None:
        """Ask the source for a value, and remember who wanted it.

        Who wanted it is the whole point.  A source answers in the order it
        was asked, so the oldest waiting request for an object is the one this
        answer belongs to -- and a round of polling must be finished by *its
        own* answers, not by one that happened to arrive while it was waiting.
        """
        if self.source is None:
            self.ctx.warn(f"{self.pane.title or self.name}: no node or file selected")
            return
        waiting = self._asked.setdefault((index, sub), [])
        waiting.append(polling)
        # A source that answers some reads and not others would otherwise grow
        # this list for as long as the polling runs.  The oldest are the ones
        # whose answers are never coming.
        del waiting[:-MAX_WAITING]
        self.source.request(index, sub)

    def _on_write_requested(self, index: int, sub: int, raw: Any) -> None:
        if self.source is None:
            self.ctx.warn(f"{self.pane.title or self.name}: no node or file selected")
            return
        self.source.write(index, sub, raw)

    def _on_value(self, index: int, sub: int, raw: Any, error: Any) -> None:
        # Handed to every widget rather than routed: a map wants many
        # sub-indices of one object, and only it knows which.
        for widget in self._widgets:
            widget.set_value(index, sub, raw, error)
        # Only if this answer is one polling asked for.  Reading a pane by
        # hand while it polls used to finish whichever round was in flight,
        # and the rate then read faster than the bus was really managing --
        # which is the one thing the rate is there not to do.
        waiting = self._asked.get((index, sub))
        if waiting and waiting.pop(0):
            self.poller.answered(index, sub)
        if not error:
            self._to_signals(index, sub, raw)

    def _to_signals(self, index: int, sub: int, raw: Any) -> None:
        """A polled number is a signal, so it plots and exports like one.

        Only while polling: a value read once by hand is a reading, and putting
        it in the plot as a series of one point would fill the signal list with
        things nobody is watching.
        """
        if self.signals is None or not self.poller.running:
            return
        if not isinstance(raw, int | float) or isinstance(raw, bool):
            return  # a string or a block of bytes is not a trace
        widget = next((w for w in self._widgets if w.field.where == (index, sub)), None)
        if widget is None:
            return
        display = widget.display
        value = display.physical(raw) if display.scaled else raw
        group = self.source.label if self.source is not None else self.name
        self.signals.push(group, widget.label_text(), self._now(), float(value), display.unit)
        self.signals.updated.emit()

    # --- changing the pane itself ----------------------------------------------------------
    def _edit(self) -> None:
        dialog = CustomPaneEditor(self, self.pane, self.source)
        if dialog.exec() != QDialog.Accepted:
            return
        self.pane = dialog.result_pane()
        model.save(self.name, self.pane)
        self.rebuild()
        if self.source is not None:
            self.refresh()
        self.changed.emit()

    def add_field(self, item: Field) -> None:
        """Put another object on this pane, from wherever it was picked."""
        self.pane = self.pane.with_field(item)
        model.save(self.name, self.pane)
        self.rebuild()
        if self.source is not None:
            self.refresh()
        self.changed.emit()


def _no_display():
    from pycangui.canopen.display import Display

    return Display()


class CustomPaneEditor(QDialog):
    """The objects on a pane, what they are called, and how each is shown.

    Objects are usually picked in the CANopen pane's object dictionary, which
    is where they can be searched for and where their names already are.  Add
    is here as well because that route needs a node on the bus with an EDS
    loaded, and the two cases it does not cover are ordinary ones: building a
    pane at a desk against a DCF, and adding an object whose index you
    already have in front of you.
    """

    COLUMNS = ("Object", "Label", "Shown as")

    def __init__(self, parent: QWidget | None, pane: CustomPane, source=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit pane")
        self.resize(560, 460)
        self.source = source
        self._fields = list(pane.fields)

        self.title = QLineEdit(pane.title)
        self.description = QLineEdit(pane.description)
        self.node = QLineEdit("" if pane.node is None else str(pane.node))
        self.node.setToolTip(
            "The node this pane usually opens against.  Only a default -- the\n"
            "selector on the pane itself decides where the values come from."
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

        add = QPushButton("Add...")
        add.setToolTip(
            "Add objects to this pane.  Pick them from whatever it is bound\n"
            "to -- a node's dictionary or an EDS -- or type an index."
        )
        add.clicked.connect(self._add)
        up = QPushButton("Move up")
        up.clicked.connect(lambda: self._move(-1))
        down = QPushButton("Move down")
        down.clicked.connect(lambda: self._move(1))
        remove = QPushButton("Remove")
        remove.clicked.connect(self._remove)
        buttons = QHBoxLayout()
        for button in (add, up, down, remove):
            buttons.addWidget(button)
        buttons.addStretch()

        closer = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        closer.accepted.connect(self._accept)
        closer.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(details)
        layout.addWidget(self.table)
        layout.addLayout(buttons)
        note = QLabel(
            "Anything this dialog does not cover -- named bits, map axes -- "
            "is in the pane's file, which is JSON and meant to be edited."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
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

    def _add(self) -> None:
        chosen = AddObjects(self, self.source).chosen_fields()
        if not chosen:
            return
        self._collect()
        self._fields.extend(chosen)
        self._fill()
        self.table.setCurrentCell(len(self._fields) - 1, 1)

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
            QMessageBox.warning(self, "The pane needs a title", "It is what names its window.")
            return
        self.accept()

    def result_pane(self) -> CustomPane:
        node = self.node.text().strip()
        return CustomPane(
            title=self.title.text().strip(),
            description=self.description.text().strip(),
            fields=list(self._fields),
            node=int(node, 0) if node.isdigit() or node.lower().startswith("0x") else None,
        )


class AddObjects(QDialog):
    """Pick objects out of whatever the pane is bound to, or type an index.

    Both, rather than either.  A list to search is how somebody who does not
    know the index finds it, and typing one is how somebody who does gets on
    with it -- and there is no list at all when the pane is bound to a node
    that has no EDS, which must not be a dead end.
    """

    def __init__(self, parent: QWidget | None, source) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add objects")
        self.resize(520, 480)
        self._entries: list[tuple[int, int, str, str]] = []
        if source is not None:
            self._entries = list(source.objects())

        self.search = QLineEdit()
        self.search.setPlaceholderText("filter by index or name...")
        self.search.setToolTip("Several words must all match, as in the object dictionary.")
        self.search.textChanged.connect(self._filter)

        self.list = QListWidget()
        self.list.setFont(QFont("Consolas", 9))
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.itemDoubleClicked.connect(lambda _i: self.accept())
        for index, sub_index, name, access in self._entries:
            self.list.addItem(f"{index:04X}:{sub_index:02X}  {name}  ({access or '?'})")

        self.index = QLineEdit()
        self.index.setPlaceholderText("2001")
        self.index.setToolTip("In hex, as an index is always written.  0x2001 works too.")
        self.sub = QLineEdit()
        self.sub.setPlaceholderText("0")
        self.sub.setToolTip("Sub-index, in hex.  Blank means 0.")
        typed = QHBoxLayout()
        typed.addWidget(QLabel("or index:"))
        typed.addWidget(self.index, 1)
        typed.addWidget(QLabel("sub:"))
        typed.addWidget(self.sub)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        if self._entries:
            layout.addWidget(self.search)
            layout.addWidget(self.list, 1)
        else:
            missing = QLabel(
                "Nothing to pick from: this pane is not bound to a node with an "
                "EDS loaded, or to a file.  Type an index instead."
            )
            missing.setWordWrap(True)
            layout.addWidget(missing)
            self.list.hide()
            self.search.hide()
        layout.addLayout(typed)
        layout.addWidget(buttons)

    def _filter(self, text: str) -> None:
        needles = text.lower().split()
        for row in range(self.list.count()):
            item = self.list.item(row)
            item.setHidden(not all(n in item.text().lower() for n in needles))

    def chosen_fields(self) -> list[Field]:
        """What was picked, as fields.  Empty if the dialog was cancelled."""
        if self.exec() != QDialog.Accepted:
            return []
        out = [self._as_field(*self._entries[self.list.row(i)]) for i in self.list.selectedItems()]
        if (typed := self._typed()) is not None:
            out.append(typed)
        return out

    def _typed(self) -> Field | None:
        text = self.index.text().strip()
        if not text:
            return None
        try:
            index = int(text, 16 if not text.lower().startswith("0x") else 0)
            sub_text = self.sub.text().strip() or "0"
            sub_index = int(sub_text, 16 if not sub_text.lower().startswith("0x") else 0)
        except ValueError:
            QMessageBox.warning(
                self.parent(), "Not an index", f"{text!r} is not a hex object index."
            )
            return None
        # Whatever the source says about it, where it knows: a typed index that
        # happens to be in the dictionary should arrive named, like a picked one.
        for entry in self._entries:
            if (entry[0], entry[1]) == (index, sub_index):
                return self._as_field(*entry)
        return Field(index=index, sub=sub_index, kind="number")

    @staticmethod
    def _as_field(index: int, sub_index: int, name: str, access: str) -> Field:
        """A writable object is one to type into, a read-only one a reading."""
        return Field(
            index=index,
            sub=sub_index,
            kind="number" if "w" in (access or "").lower() else "value",
            label=name,
        )
