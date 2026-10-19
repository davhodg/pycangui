# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""The Database pane's dialogs for a message, a signal and the value tables.

Opened by double-clicking what is to be changed, so the pane stays a pane
for reading. Each dialog says what it would change, as the changes of
``core.dbc_edit``: only the fields that differ from what was there, so
opening one and pressing OK changes nothing. A field that is not a number
where one is wanted is said under the fields, and the dialog stays open.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pycangui.core.dbc_edit import (
    NAME,
    AddMessage,
    AddSignal,
    AddValueTable,
    Change,
    RemoveValueTable,
    SetMessage,
    SetSignal,
    SetValueTable,
    parse_values,
    values_text,
)
from pycangui.ui import fonts

INTEL, MOTOROLA = "little_endian", "big_endian"
BYTE_ORDERS = (("Intel (little-endian)", INTEL), ("Motorola (big-endian)", MOTOROLA))
UNSIGNED, SIGNED, FLOAT = "Unsigned", "Signed", "Float"
LIST_TIP = "Names separated by commas."
#: How tall a comment box is, in lines of text.
COMMENT_LINES = 3
ALWAYS = "In every frame"
MUX_TIP = (
    "A multiplexer's value says which of the message's other signals\n"
    "are in a frame: a page number, a mode, a group."
)
SENT_TIP = (
    "In every frame, or only when a multiplexer has one of the values\n"
    "given: 0, or 0, 2-4. Tick Multiplexer on a signal to offer it here."
)
TABLE_TIP = "Replace the named values with those of a value table."


class NotANumberError(ValueError):
    """A field that should hold a number, and what it should be called."""


def number(text: str, what: str, allow_empty: bool = False) -> float | None:
    """A number typed into a field: 0.125, -40, 1e3. None for an empty one,
    where that is allowed."""
    text = text.strip()
    if not text and allow_empty:
        return None
    try:
        value = float(text)
    except ValueError:
        raise NotANumberError(f"{what} is not a number.") from None
    return (
        int(value) if value.is_integer() and "." not in text and "e" not in text.lower() else value
    )


def identifier(text: str) -> int:
    """An identifier as pycangui writes one: hexadecimal, with or without 0x."""
    try:
        return int(text.strip().removeprefix("0x").removeprefix("0X"), 16)
    except ValueError:
        raise NotANumberError("The identifier is not a hexadecimal number.") from None


def names(text: str) -> list[str]:
    return [part.strip() for part in text.split(",") if part.strip()]


def framed(name: str) -> tuple[QGroupBox, QVBoxLayout, QHBoxLayout]:
    """A border with its name inside at the left: the frame, what goes under
    the name's row, and the row the name is on. The pane's, and the dialogs'."""
    frame = QGroupBox()
    frame.setAccessibleName(name)
    inside = QVBoxLayout(frame)
    inside.setContentsMargins(6, 4, 6, 6)
    top = QHBoxLayout()
    top.addWidget(QLabel(name))
    inside.addLayout(top)
    return frame, inside, top


def _values_table() -> QTableWidget:
    table = QTableWidget(0, 2)
    table.setHorizontalHeaderLabels(["Value", "Name"])
    table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
    table.verticalHeader().hide()
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    return table


def add_value_row(table: QTableWidget, value: int | None, text: str) -> None:
    """A row of named values; an empty one is chosen, to be typed into."""
    row = table.rowCount()
    table.insertRow(row)
    table.setItem(row, 0, QTableWidgetItem("" if value is None else str(value)))
    table.setItem(row, 1, QTableWidgetItem(text))
    if value is None:
        table.setCurrentCell(row, 0)
        table.editItem(table.item(row, 0))


def fill_values(table: QTableWidget, values: dict[int, str]) -> None:
    table.setRowCount(0)
    for value, text in values.items():
        add_value_row(table, int(value), str(text))


def remove_value_rows(table: QTableWidget) -> None:
    for row in sorted({i.row() for i in table.selectedIndexes()}, reverse=True):
        table.removeRow(row)


def value_rows(table: QTableWidget) -> list[tuple[str, str]]:
    """What is typed into a table of named values, as text."""
    return [
        (
            (table.item(row, 0) or QTableWidgetItem()).text(),
            (table.item(row, 1) or QTableWidgetItem()).text(),
        )
        for row in range(table.rowCount())
    ]


def read_values(table: QTableWidget, where: str = "") -> dict[int, str]:
    """The named values typed into a table; NotANumberError says what is wrong."""
    return parse_rows(value_rows(table), where)


def parse_rows(rows: list[tuple[str, str]], where: str = "") -> dict[int, str]:
    out: dict[int, str] = {}
    for raw, text in rows:
        raw, text = raw.strip(), text.strip()
        if not raw and not text:
            continue  # a row added and left empty
        try:
            value = int(raw, 0)
        except ValueError:
            raise NotANumberError(
                f"{where}A named value, {raw or 'empty'}, is not a whole number."
            ) from None
        if not text:
            raise NotANumberError(f"{where}The value {value} has no name.")
        out[value] = text
    return out


def _value_buttons(table: QTableWidget) -> QHBoxLayout:
    add = QPushButton("Add")
    add.clicked.connect(lambda: add_value_row(table, None, ""))
    remove = QPushButton("Remove")
    remove.clicked.connect(lambda: remove_value_rows(table))
    row = QHBoxLayout()
    row.addWidget(add)
    row.addWidget(remove)
    return row


def _comment_box(text: str) -> QPlainTextEdit:
    box = QPlainTextEdit(text)
    box.setTabChangesFocus(True)  # Tab moves on, as in every other field
    box.setFixedHeight(fonts.text_height() * COMMENT_LINES + 12)
    return box


class _EditDialog(QDialog):
    """What both have: a form, a line for what is wrong with it, OK and Cancel."""

    def __init__(self, parent: QWidget | None, title: str) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.form = QFormLayout()
        self.problem = QLabel()
        self.problem.setWordWrap(True)
        self.problem.hide()
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(self.form)
        layout.addWidget(self.problem)
        layout.addWidget(self.buttons)
        self._changes: list[Change] = []

    def show_problem(self, text: str) -> None:
        """Said under the fields, and the dialog stays open to put it right."""
        self.problem.setText(text)
        self.problem.setAccessibleName(text)
        self.problem.show()

    def _accept(self) -> None:
        try:
            self._changes = self._work_out()
        except NotANumberError as exc:
            self.show_problem(str(exc))
            return
        self.accept()

    def changes(self) -> list[Change]:
        """What OK asked for: only what differs from what was there."""
        return list(self._changes)

    def _work_out(self) -> list[Change]:  # pragma: no cover - each dialog has its own
        raise NotImplementedError


class MessageDialog(_EditDialog):
    """A message's name, identifier, length, cycle time, senders and comment.

    ``msg`` None is a new message.
    """

    def __init__(self, parent: QWidget | None, msg=None) -> None:
        super().__init__(parent, "New message" if msg is None else f"Message {msg.name}")
        self.msg = msg
        self.name = QLineEdit("" if msg is None else msg.name)
        self.ident = QLineEdit("" if msg is None else f"{msg.frame_id:X}")
        self.ident.setToolTip("Hexadecimal, as everywhere in pycangui.")
        self.extended = QCheckBox("29-bit")
        self.extended.setChecked(bool(msg is not None and msg.is_extended_frame))
        ident_row = QHBoxLayout()
        ident_row.addWidget(self.ident, 1)
        ident_row.addWidget(self.extended)
        self.length = QSpinBox()
        self.length.setRange(0, 64)
        self.length.setSuffix(" bytes")
        self.length.setValue(8 if msg is None else msg.length)
        self.cycle = QSpinBox()
        self.cycle.setRange(0, 3_600_000)
        self.cycle.setSuffix(" ms")
        self.cycle.setSpecialValueText("none")
        self.cycle.setValue(0 if msg is None else (msg.cycle_time or 0))
        self.senders = QLineEdit("" if msg is None else ", ".join(msg.senders or []))
        self.senders.setToolTip(LIST_TIP)
        self.comment = _comment_box("" if msg is None else (msg.comment or ""))
        self.form.addRow("Name:", self.name)
        self.form.addRow("Identifier:", ident_row)
        self.form.addRow("Length:", self.length)
        self.form.addRow("Cycle time:", self.cycle)
        self.form.addRow("Senders:", self.senders)
        self.form.addRow("Comment:", self.comment)
        for label, field in (
            ("Name", self.name),
            ("Identifier", self.ident),
            ("Length", self.length),
            ("Cycle time", self.cycle),
            ("Senders", self.senders),
            ("Comment", self.comment),
        ):
            field.setAccessibleName(label)

    def _work_out(self) -> list[Change]:
        name = self.name.text().strip()
        frame_id = identifier(self.ident.text())
        wanted = {
            "frame_id": frame_id,
            "is_extended_frame": self.extended.isChecked(),
            "length": self.length.value(),
            "cycle_time": self.cycle.value() or None,
            "senders": names(self.senders.text()),
            "comment": self.comment.toPlainText() or None,
        }
        if self.msg is None:
            out: list[Change] = [
                AddMessage(name, frame_id, wanted["is_extended_frame"], wanted["length"])
            ]
            for field in ("cycle_time", "senders", "comment"):
                if wanted[field]:
                    out.append(SetMessage(name, field, wanted[field]))
            return out
        msg = self.msg
        had = {
            "frame_id": msg.frame_id,
            "is_extended_frame": msg.is_extended_frame,
            "length": msg.length,
            "cycle_time": msg.cycle_time,
            "senders": list(msg.senders or []),
            "comment": msg.comment or None,
        }
        out = [SetMessage(msg.name, f, v) for f, v in wanted.items() if had[f] != v]
        if name != msg.name:
            out.append(SetMessage(msg.name, "name", name))  # last: the rest name it as it was
        return out


class SignalDialog(_EditDialog):
    """A signal: where it is, what kind of number, its scaling and range, its
    unit, who receives it, its comment, its named values and its multiplexing.

    ``signal`` None is a new signal of the message named. ``multiplexers`` are
    the message's signals it could be sent by; ``tables`` the database's value
    tables, to take named values from.
    """

    def __init__(
        self,
        parent: QWidget | None,
        message: str,
        signal=None,
        multiplexers: list[str] | tuple[str, ...] = (),
        tables: dict[str, dict[int, str]] | None = None,
    ) -> None:
        title = f"New signal in {message}" if signal is None else f"Signal {message}.{signal.name}"
        super().__init__(parent, title)
        self.message = message
        self.signal = signal
        s = signal
        self.name = QLineEdit("" if s is None else s.name)
        self.start = QSpinBox()
        self.start.setRange(0, 511)
        self.start.setValue(0 if s is None else s.start)
        self.start.setToolTip(
            "The first bit, as the format counts: an Intel signal's least\n"
            "significant, a Motorola one's most significant."
        )
        self.bits = QSpinBox()
        self.bits.setRange(1, 64)
        self.bits.setSuffix(" bits")
        self.bits.setValue(8 if s is None else s.length)
        self.order = QComboBox()
        for text, value in BYTE_ORDERS:
            self.order.addItem(text, value)
        self.order.setCurrentIndex(self.order.findData(INTEL if s is None else s.byte_order))
        self.kind = QComboBox()
        self.kind.addItems([UNSIGNED, SIGNED, FLOAT])
        if s is not None:
            self.kind.setCurrentText(FLOAT if s.is_float else SIGNED if s.is_signed else UNSIGNED)
        self.scale = QLineEdit("1" if s is None else f"{s.scale:.12g}")
        self.offset = QLineEdit("0" if s is None else f"{s.offset:.12g}")
        self.minimum = QLineEdit("" if s is None or s.minimum is None else f"{s.minimum:.12g}")
        self.maximum = QLineEdit("" if s is None or s.maximum is None else f"{s.maximum:.12g}")
        for box in (self.minimum, self.maximum):
            box.setPlaceholderText("none")
        self.unit = QLineEdit("" if s is None else (s.unit or ""))
        self.receivers = QLineEdit("" if s is None else ", ".join(s.receivers or []))
        self.receivers.setToolTip(LIST_TIP)
        self.comment = _comment_box("" if s is None else (s.comment or ""))
        self.values = _values_table()
        self.values.setAccessibleName("Named values")
        fill_values(self.values, {} if s is None else (s.choices or {}))
        value_buttons = _value_buttons(self.values)
        #: The database's value tables, each offered to copy from.
        self.tables = dict(tables or {})
        self.from_table = QPushButton("From value table")
        self.from_table.setToolTip(TABLE_TIP)
        table_menu = QMenu(self.from_table)
        for name in self.tables:
            table_menu.addAction(name, lambda n=name: self.use_table(n))
        self.from_table.setMenu(table_menu)
        self.from_table.setVisible(bool(self.tables))
        value_buttons.addWidget(self.from_table)
        value_buttons.addStretch()
        self.multiplexer = QCheckBox("Chooses which of the others are sent")
        self.multiplexer.setToolTip(MUX_TIP)
        self.multiplexer.setChecked(bool(s is not None and s.is_multiplexer))
        self.sent = QComboBox()
        self.sent.setToolTip(SENT_TIP)
        self.sent.addItem(ALWAYS, "")
        by = "" if s is None else (s.multiplexer_signal or "")
        for name in dict.fromkeys([*multiplexers, *([by] if by else [])]):
            self.sent.addItem(f"When {name} is", name)
        self.sent.setCurrentIndex(max(0, self.sent.findData(by)))
        self.sent_values = QLineEdit(
            values_text(s.multiplexer_ids) if s is not None and s.multiplexer_ids else ""
        )
        self.sent_values.setPlaceholderText("0, 2-4")
        self.sent_values.setToolTip(SENT_TIP)
        self.sent.currentIndexChanged.connect(self._sent_changed)
        self._sent_changed()
        sent_row = QHBoxLayout()
        sent_row.addWidget(self.sent, 1)
        sent_row.addWidget(self.sent_values, 1)
        values = QVBoxLayout()
        values.addWidget(self.values)
        values.addLayout(value_buttons)
        range_row = QHBoxLayout()
        range_row.addWidget(self.minimum)
        range_row.addWidget(QLabel("to"))
        range_row.addWidget(self.maximum)
        self.form.addRow("Name:", self.name)
        self.form.addRow("Start bit:", self.start)
        self.form.addRow("Length:", self.bits)
        self.form.addRow("Byte order:", self.order)
        self.form.addRow("Type:", self.kind)
        self.form.addRow("Scale:", self.scale)
        self.form.addRow("Offset:", self.offset)
        self.form.addRow("Range:", range_row)
        self.form.addRow("Unit:", self.unit)
        self.form.addRow("Receivers:", self.receivers)
        self.form.addRow("Comment:", self.comment)
        self.form.addRow("Named values:", values)
        self.form.addRow("Multiplexer:", self.multiplexer)
        self.form.addRow("Sent:", sent_row)
        for label, field in (
            ("Name", self.name),
            ("Start bit", self.start),
            ("Length", self.bits),
            ("Byte order", self.order),
            ("Type", self.kind),
            ("Scale", self.scale),
            ("Offset", self.offset),
            ("Minimum", self.minimum),
            ("Maximum", self.maximum),
            ("Unit", self.unit),
            ("Receivers", self.receivers),
            ("Comment", self.comment),
            ("Multiplexer", self.multiplexer),
            ("Sent", self.sent),
            ("Multiplexer values", self.sent_values),
        ):
            field.setAccessibleName(label)

    def add_value(self, value: int | None, text: str) -> None:
        add_value_row(self.values, value, text)

    def use_table(self, name: str) -> None:
        """The named values of a value table, in place of those there."""
        fill_values(self.values, self.tables.get(name, {}))

    def _sent_changed(self) -> None:
        self.sent_values.setEnabled(bool(self.sent.currentData()))

    def _multiplexed_by(self) -> tuple[str | None, tuple[int, ...]]:
        mux = self.sent.currentData() or None
        if mux is None:
            return None, ()
        try:
            values = parse_values(self.sent_values.text())
        except ValueError:
            values = []
        if not values:
            raise NotANumberError(
                f"Which values of {mux} is it sent for? Whole numbers, as 0 or 0, 2-4."
            )
        return mux, tuple(values)

    def _work_out(self) -> list[Change]:
        kind = self.kind.currentText()
        wanted = {
            "start": self.start.value(),
            "length": self.bits.value(),
            "byte_order": self.order.currentData(),
            "is_signed": kind != UNSIGNED,
            "is_float": kind == FLOAT,
            "scale": number(self.scale.text(), "The scale"),
            "offset": number(self.offset.text(), "The offset"),
            "minimum": number(self.minimum.text(), "The minimum", allow_empty=True),
            "maximum": number(self.maximum.text(), "The maximum", allow_empty=True),
            "unit": self.unit.text().strip() or None,
            "receivers": names(self.receivers.text()),
            "comment": self.comment.toPlainText() or None,
            "choices": read_values(self.values),
            "is_multiplexer": self.multiplexer.isChecked(),
            "multiplexed_by": self._multiplexed_by(),
        }
        name = self.name.text().strip()
        if self.signal is None:
            out: list[Change] = [AddSignal(self.message, name, wanted["start"], wanted["length"])]
            defaults = {
                "is_signed": False,
                "is_float": False,
                "scale": 1,
                "offset": 0,
                "is_multiplexer": False,
                "multiplexed_by": (None, ()),
            }
            for field, value in wanted.items():
                if field in ("start", "length"):
                    continue
                if value not in (None, [], {}) and defaults.get(field, object()) != value:
                    out.append(SetSignal(self.message, name, field, value))
            return out
        s = self.signal
        had = {
            "start": s.start,
            "length": s.length,
            "byte_order": s.byte_order,
            "is_signed": s.is_signed,
            "is_float": s.is_float,
            "scale": s.scale,
            "offset": s.offset,
            "minimum": s.minimum,
            "maximum": s.maximum,
            "unit": s.unit or None,
            "receivers": list(s.receivers or []),
            "comment": s.comment or None,
            "choices": {int(k): str(v) for k, v in (s.choices or {}).items()},
            "is_multiplexer": bool(s.is_multiplexer),
            "multiplexed_by": (
                (s.multiplexer_signal, tuple(sorted(s.multiplexer_ids or ())))
                if s.multiplexer_signal
                else (None, ())
            ),
        }
        out = [SetSignal(self.message, s.name, f, v) for f, v in wanted.items() if had[f] != v]
        if name != s.name:
            out.append(SetSignal(self.message, s.name, "name", name))
        return out


class ValueTablesDialog(_EditDialog):
    """The database's value tables: named values kept once, by name.

    Each is a list of numbers and their names, as a signal's named values
    are; a signal takes a copy of one from its own dialog. The tables are
    on the left, to add, rename and remove; the chosen one's values are on
    the right.
    """

    def __init__(self, parent: QWidget | None, tables: dict[str, dict[int, str]]) -> None:
        super().__init__(parent, "Value tables")
        #: [name it had, or None for a new one; its values as typed, rows of text].
        self._entries: list[list] = [
            [name, [(str(v), str(t)) for v, t in values.items()]] for name, values in tables.items()
        ]
        self._had = {name: dict(values) for name, values in tables.items()}
        self._shown: int = -1
        self.list = QListWidget()
        self.list.setAccessibleName("Value tables")
        for name, _rows in self._entries:
            self._add_item(name)
        self.list.currentRowChanged.connect(self._show)
        add = QPushButton("Add")
        add.clicked.connect(self.add_table)
        remove = QPushButton("Remove")
        remove.clicked.connect(self.remove_table)
        list_buttons = QHBoxLayout()
        list_buttons.addWidget(add)
        list_buttons.addWidget(remove)
        list_buttons.addStretch()
        left, inside, _top = framed("Tables")
        self.list.setToolTip("Double-click a name to change it.")
        inside.addWidget(self.list)
        inside.addLayout(list_buttons)
        self.values = _values_table()
        self.values.setAccessibleName("Named values")
        value_buttons = _value_buttons(self.values)
        value_buttons.addStretch()
        right, inside, _top = framed("Named values")
        inside.addWidget(self.values)
        inside.addLayout(value_buttons)
        both = QHBoxLayout()
        both.addWidget(left, 1)
        both.addWidget(right, 2)
        self.form.addRow(both)
        if self._entries:
            self.list.setCurrentRow(0)
        else:
            self.values.setEnabled(False)

    def _add_item(self, name: str) -> QListWidgetItem:
        item = QListWidgetItem(name)
        item.setFlags(item.flags() | Qt.ItemIsEditable)
        self.list.addItem(item)
        return item

    def _keep_shown(self) -> None:
        """What is typed into the values, kept with the table it is of."""
        if 0 <= self._shown < len(self._entries):
            self._entries[self._shown][1] = value_rows(self.values)

    def _show(self, row: int) -> None:
        self._keep_shown()
        self._shown = row
        self.values.setEnabled(row >= 0)
        self.values.setRowCount(0)
        if row < 0:
            return
        for value, text in self._entries[row][1]:
            r = self.values.rowCount()
            self.values.insertRow(r)
            self.values.setItem(r, 0, QTableWidgetItem(value))
            self.values.setItem(r, 1, QTableWidgetItem(text))

    def add_table(self) -> None:
        taken = {self.list.item(r).text() for r in range(self.list.count())}
        n = 1
        while f"Table{n}" in taken:
            n += 1
        self._entries.append([None, []])
        item = self._add_item(f"Table{n}")
        self.list.setCurrentItem(item)
        self.list.editItem(item)

    def add_value(self, value: int | None, text: str) -> None:
        """A named value in the table chosen."""
        add_value_row(self.values, value, text)

    def remove_table(self) -> None:
        row = self.list.currentRow()
        if row < 0:
            return
        self._shown = -1  # nothing to keep of a table going
        del self._entries[row]
        self.list.takeItem(row)
        self._show(self.list.currentRow())

    def tables(self) -> dict[str, dict[int, str]]:
        """The tables as they are now in the dialog, checked: {name: values}."""
        self._keep_shown()
        out: dict[str, dict[int, str]] = {}
        for row, (_had, rows) in enumerate(self._entries):
            name = self.list.item(row).text().strip()
            if not NAME.match(name):
                raise NotANumberError(
                    f"{name or 'A table with no name'} is not a name the format allows: "
                    "letters, digits and _, not starting with a digit."
                )
            if name in out:
                raise NotANumberError(f"Two value tables are called {name}.")
            out[name] = parse_rows(rows, f"{name}: ")
        return out

    def _work_out(self) -> list[Change]:
        now = self.tables()
        names = [self.list.item(r).text().strip() for r in range(self.list.count())]
        kept = {had for had, _rows in self._entries if had is not None}
        out: list[Change] = [RemoveValueTable(name) for name in self._had if name not in kept]
        renames: list[Change] = []
        for (had, _rows), name in zip(self._entries, names, strict=True):
            if had is None:
                out.append(AddValueTable(name, now[name]))
                continue
            if list(now[name].items()) != list(self._had[had].items()):  # order too
                out.append(SetValueTable(had, "values", now[name]))
            if name != had:
                renames.append(SetValueTable(had, "name", name))
        return out + renames  # renamed last: the rest name each as it was


def focus_first(dialog: QDialog) -> None:
    """The name field first, with its text chosen, ready to type over."""
    dialog.name.setFocus(Qt.TabFocusReason)
    dialog.name.selectAll()
