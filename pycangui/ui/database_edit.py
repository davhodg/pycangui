# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""The Database pane's dialogs: a message, a signal, and the database's value
tables, nodes, signal groups, attributes and attribute definitions.

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
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pycangui.core.dbc_edit import (
    KEPT_BY_THE_FORMAT,
    NAME,
    TYPES,
    AddAttributeDefinition,
    AddMessage,
    AddNode,
    AddSignal,
    AddSignalGroup,
    AddValueTable,
    AttributeRow,
    AttributeSpec,
    Change,
    RemoveAttributeDefinition,
    RemoveNode,
    RemoveSignalGroup,
    RemoveValueTable,
    SetAttribute,
    SetAttributeDefinition,
    SetMessage,
    SetNode,
    SetSignal,
    SetSignalGroup,
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
CHOOSE_TIP = "Tick the database's nodes in or out of the list."
ATTRIBUTES_TIP = (
    "Left empty, an attribute has its default, which is beside it.\n"
    "The attributes there are are in Attribute definitions..."
)
RENAME_TIP = "Double-click a name to change it."
#: What an attribute's definition can be of, as its dialog says it.
KIND_NAMES = (
    ("Database", "database"),
    ("Node", "node"),
    ("Message", "message"),
    ("Signal", "signal"),
)
#: How tall an attribute table is, in rows, before it scrolls.
ATTRIBUTE_ROWS = 6


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


def node_chooser(field: QLineEdit, nodes: list[str] | tuple[str, ...]) -> QPushButton:
    """Choose beside a list of node names: the database's nodes, ticked in or
    out of it. What is typed stays as typed, a name not a node included."""
    button = QPushButton("Choose")
    button.setToolTip(CHOOSE_TIP)
    button.setAccessibleName(f"Choose {field.accessibleName() or 'nodes'}")
    menu = QMenu(button)
    actions = []
    for name in nodes:
        action = menu.addAction(name)
        action.setCheckable(True)
        actions.append(action)

    def ticked() -> None:
        listed = names(field.text())
        for action in actions:
            action.setChecked(action.text() in listed)

    def toggled(action) -> None:
        listed = [n for n in names(field.text()) if n != action.text()]
        if action.isChecked():
            listed.append(action.text())
        field.setText(", ".join(listed))

    menu.aboutToShow.connect(ticked)
    menu.triggered.connect(toggled)
    button.setMenu(menu)
    button.setVisible(bool(nodes))
    return button


def _with_chooser(field: QLineEdit, nodes) -> QHBoxLayout:
    row = QHBoxLayout()
    row.addWidget(field, 1)
    row.addWidget(node_chooser(field, nodes))
    return row


def _number_text(value) -> str:
    if value is None:
        return ""
    return f"{value:.12g}" if isinstance(value, float) else str(value)


class AttributeTable(QTableWidget):
    """A target's attributes: name, value and default. A value left empty, or
    an enumeration on *Default*, is the default."""

    def __init__(self, rows: list[AttributeRow], parent: QWidget | None = None) -> None:
        super().__init__(0, 3, parent)
        self.setHorizontalHeaderLabels(["Attribute", "Value", "Default"])
        header = self.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.verticalHeader().hide()
        self.setAccessibleName("Attributes")
        self.setToolTip(ATTRIBUTES_TIP)
        self.rows = list(rows)
        for row in self.rows:
            self._add(row)
        line = self.verticalHeader().defaultSectionSize()
        self.setMaximumHeight(line * (ATTRIBUTE_ROWS + 1) + 8)
        # Wide enough for an attribute's name, a choice and its default side by
        # side: a long name elided, or a choice cut to its first letter, says nothing.
        self.resizeColumnsToContents()
        wanted = sum(self.columnWidth(c) for c in range(3)) + 120
        self.setMinimumWidth(min(wanted, 640))

    def _add(self, row: AttributeRow) -> None:
        r = self.rowCount()
        self.insertRow(r)
        name = QTableWidgetItem(row.name)
        name.setFlags(name.flags() & ~Qt.ItemIsEditable)
        kind = row.type_name.lower()
        if row.type_name in ("INT", "HEX", "FLOAT") and row.minimum is not None:
            kind += f", {_number_text(row.minimum)} to {_number_text(row.maximum)}"
        name.setToolTip(row.fixed or kind)
        self.setItem(r, 0, name)
        default = QTableWidgetItem(_number_text(row.default))
        default.setFlags(default.flags() & ~Qt.ItemIsEditable)
        self.setItem(r, 2, default)
        if row.type_name == "ENUM":
            box = QComboBox()
            box.addItem("Default", None)
            for choice in row.choices:
                box.addItem(choice, choice)
            box.setCurrentIndex(max(0, box.findData(row.value)) if row.value is not None else 0)
            box.setEnabled(not row.fixed)
            box.setAccessibleName(row.name)
            self.setCellWidget(r, 1, box)
            self.setItem(r, 1, QTableWidgetItem())
        else:
            value = QTableWidgetItem(_number_text(row.value))
            if row.fixed:
                value.setFlags(value.flags() & ~(Qt.ItemIsEditable | Qt.ItemIsEnabled))
                value.setToolTip(row.fixed)
            self.setItem(r, 1, value)

    def _value(self, r: int, row: AttributeRow):
        if row.type_name == "ENUM":
            return self.cellWidget(r, 1).currentData()
        text = (self.item(r, 1) or QTableWidgetItem()).text().strip()
        if not text:
            return None
        if row.type_name == "STRING":
            return text
        try:
            return float(text) if row.type_name == "FLOAT" else int(text, 0)
        except ValueError:
            kind = "a number" if row.type_name == "FLOAT" else "a whole number"
            raise NotANumberError(f"The attribute {row.name}, {text}, is not {kind}.") from None

    def changes(self, target: tuple) -> list[Change]:
        """An attribute change for each value that differs from what it was."""
        out: list[Change] = []
        for r, row in enumerate(self.rows):
            if row.fixed:
                continue
            value = self._value(r, row)
            if value != row.value:
                out.append(SetAttribute(target, row.name, value))
        return out


def _attribute_row(form: QFormLayout, rows: list[AttributeRow] | None) -> AttributeTable | None:
    """The attributes' row of a dialog, where the database defines any."""
    if not rows:
        return None
    table = AttributeTable(rows)
    form.addRow("Attributes:", table)
    return table


class _NamedList(QListWidget):
    """A list of names to change by double-clicking, as the nodes, the groups
    and the value tables are."""

    def __init__(self, label: str) -> None:
        super().__init__()
        self.setAccessibleName(label)
        self.setToolTip(RENAME_TIP)

    def add_name(self, name: str) -> QListWidgetItem:
        item = QListWidgetItem(name)
        item.setFlags(item.flags() | Qt.ItemIsEditable)
        self.addItem(item)
        return item

    def names(self) -> list[str]:
        return [self.item(r).text().strip() for r in range(self.count())]

    def fresh(self, stem: str) -> str:
        taken, n = set(self.names()), 1
        while f"{stem}{n}" in taken:
            n += 1
        return f"{stem}{n}"


def _check_names(names: list[str], what: str) -> None:
    seen: set[str] = set()
    for name in names:
        if not NAME.match(name):
            raise NotANumberError(
                f"{name or f'A {what} with no name'} is not a name the format allows: "
                "letters, digits and _, not starting with a digit."
            )
        if name in seen:
            raise NotANumberError(f"Two {what}s are called {name}.")
        seen.add(name)


def _list_buttons(add, remove) -> QHBoxLayout:
    row = QHBoxLayout()
    for text, slot in (("Add", add), ("Remove", remove)):
        button = QPushButton(text)
        button.clicked.connect(slot)
        row.addWidget(button)
    row.addStretch()
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

    def __init__(
        self,
        parent: QWidget | None,
        msg=None,
        nodes: list[str] | tuple[str, ...] = (),
        attributes: list[AttributeRow] | None = None,
    ) -> None:
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
        self.senders.setAccessibleName("Senders")
        self.form.addRow("Senders:", _with_chooser(self.senders, nodes))
        self.form.addRow("Comment:", self.comment)
        self.attributes = _attribute_row(self.form, attributes)
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
            if self.attributes is not None:
                out += self.attributes.changes(("message", name))
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
        if self.attributes is not None:
            out += self.attributes.changes(("message", msg.name))
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
        nodes: list[str] | tuple[str, ...] = (),
        attributes: list[AttributeRow] | None = None,
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
        self.receivers.setAccessibleName("Receivers")
        self.form.addRow("Receivers:", _with_chooser(self.receivers, nodes))
        self.form.addRow("Comment:", self.comment)
        self.form.addRow("Named values:", values)
        self.form.addRow("Multiplexer:", self.multiplexer)
        self.form.addRow("Sent:", sent_row)
        self.attributes = _attribute_row(self.form, attributes)
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
            if self.attributes is not None:
                out += self.attributes.changes(("signal", self.message, name))
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
        if self.attributes is not None:
            out += self.attributes.changes(("signal", self.message, s.name))
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


class _PagedDialog(_EditDialog):
    """A list of names on the left, framed, and the chosen one's page on the
    right: a node's comment and attributes, a group's signals. A page is made
    for each entry and kept, so nothing typed is lost moving between them."""

    def __init__(self, parent, title: str, list_label: str, page_label: str) -> None:
        super().__init__(parent, title)
        self.list = _NamedList(list_label)
        self.pages = QStackedWidget()
        #: The name each entry had, or None for one added here; in list order.
        self.had: list[str | None] = []
        left, inside, _top = framed(list_label)
        inside.addWidget(self.list)
        inside.addLayout(_list_buttons(self.add_entry, self.remove_entry))
        right, inside, _top = framed(page_label)
        inside.addWidget(self.pages)
        both = QHBoxLayout()
        both.addWidget(left, 1)
        both.addWidget(right, 2)
        self.form.addRow(both)
        self.list.currentRowChanged.connect(self._show)
        self._removed: list[str] = []

    def _show(self, row: int) -> None:
        if row >= 0:
            self.pages.setCurrentIndex(row)
        self.pages.setEnabled(row >= 0)

    def _append(self, name: str, had: str | None, page: QWidget) -> QListWidgetItem:
        self.had.append(had)
        self.pages.addWidget(page)
        return self.list.add_name(name)

    def add_entry(self) -> None:
        item = self._append(self.list.fresh(self.stem), None, self.new_page())
        self.list.setCurrentItem(item)
        self.list.editItem(item)

    def remove_entry(self) -> None:
        row = self.list.currentRow()
        if row < 0:
            return
        if self.had[row] is not None:
            self._removed.append(self.had[row])
        del self.had[row]
        self.pages.removeWidget(self.pages.widget(row))
        self.list.takeItem(row)
        self._show(self.list.currentRow())

    stem = "Entry"

    def new_page(self) -> QWidget:  # pragma: no cover - each dialog has its own
        raise NotImplementedError


class NodesDialog(_PagedDialog):
    """The database's nodes: each one's name, comment and attributes."""

    stem = "Node"

    def __init__(
        self,
        parent: QWidget | None,
        nodes: list[tuple[str, str]],
        attributes: dict[str, list[AttributeRow]] | None = None,
        blank: list[AttributeRow] | None = None,
    ) -> None:
        super().__init__(parent, "Nodes", "Nodes", "Its comment and attributes")
        self._attributes = attributes or {}
        self._blank = blank or []
        self._comments: dict[str, str] = {}
        for name, comment in nodes:
            self._comments[name] = comment or ""
            self._append(name, name, self._page(comment or "", self._attributes.get(name)))
        if self.list.count():
            self.list.setCurrentRow(0)
        else:
            self._show(-1)

    def _page(self, comment: str, rows: list[AttributeRow] | None) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setContentsMargins(0, 0, 0, 0)
        page.comment = _comment_box(comment)
        page.comment.setAccessibleName("Comment")
        form.addRow("Comment:", page.comment)
        page.attributes = _attribute_row(form, rows)
        return page

    def new_page(self) -> QWidget:
        return self._page("", self._blank)

    def _work_out(self) -> list[Change]:
        names = self.list.names()
        _check_names(names, "node")
        out: list[Change] = [RemoveNode(name) for name in self._removed]
        renames: list[Change] = []
        for row, (had, name) in enumerate(zip(self.had, names, strict=True)):
            page = self.pages.widget(row)
            comment = page.comment.toPlainText() or None
            if had is None:
                out.append(AddNode(name, comment))
                if page.attributes is not None:
                    out += page.attributes.changes(("node", name))
                continue
            if comment != (self._comments.get(had) or None):
                out.append(SetNode(had, "comment", comment))
            if page.attributes is not None:
                out += page.attributes.changes(("node", had))
            if name != had:
                renames.append(SetNode(had, "name", name))
        return out + renames  # renamed last: the rest name each as it was


class SignalGroupsDialog(_PagedDialog):
    """A message's signal groups: each one's name, how often it is repeated,
    and which of the message's signals are in it."""

    stem = "Group"

    def __init__(
        self,
        parent: QWidget | None,
        message: str,
        signals: list[str],
        groups: list[tuple[str, int, list[str]]],
    ) -> None:
        super().__init__(parent, f"Signal groups of {message}", "Groups", "Its signals")
        self.message = message
        self.signals = list(signals)
        self._had_groups = {name: (reps, list(chosen)) for name, reps, chosen in groups}
        for name, reps, chosen in groups:
            self._append(name, name, self._page(reps, chosen))
        if self.list.count():
            self.list.setCurrentRow(0)
        else:
            self._show(-1)

    def _page(self, repetitions: int, chosen: list[str]) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setContentsMargins(0, 0, 0, 0)
        page.repetitions = QSpinBox()
        page.repetitions.setRange(1, 65535)
        page.repetitions.setValue(repetitions)
        page.repetitions.setAccessibleName("Repetitions")
        page.ticks = QListWidget()
        page.ticks.setAccessibleName("Signals in the group")
        for name in self.signals:
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if name in chosen else Qt.Unchecked)
            page.ticks.addItem(item)
        form.addRow("Repetitions:", page.repetitions)
        form.addRow("Signals:", page.ticks)
        return page

    def new_page(self) -> QWidget:
        return self._page(1, [])

    @staticmethod
    def chosen(page: QWidget) -> list[str]:
        ticks = page.ticks
        return [
            ticks.item(r).text()
            for r in range(ticks.count())
            if ticks.item(r).checkState() == Qt.Checked
        ]

    def _work_out(self) -> list[Change]:
        names = self.list.names()
        _check_names(names, "signal group")
        out: list[Change] = [RemoveSignalGroup(self.message, name) for name in self._removed]
        renames: list[Change] = []
        for row, (had, name) in enumerate(zip(self.had, names, strict=True)):
            page = self.pages.widget(row)
            reps, chosen = page.repetitions.value(), self.chosen(page)
            if had is None:
                out.append(AddSignalGroup(self.message, name, reps, tuple(chosen)))
                continue
            had_reps, had_chosen = self._had_groups[had]
            if reps != had_reps:
                out.append(SetSignalGroup(self.message, had, "repetitions", reps))
            if chosen != had_chosen:
                out.append(SetSignalGroup(self.message, had, "signals", tuple(chosen)))
            if name != had:
                renames.append(SetSignalGroup(self.message, had, "name", name))
        return out + renames


class AttributesDialog(_EditDialog):
    """The database's own attributes: the ones of no node, message or signal."""

    def __init__(self, parent: QWidget | None, rows: list[AttributeRow]) -> None:
        super().__init__(parent, "Database attributes")
        self.table = AttributeTable(rows)
        self.table.setMaximumHeight(16777215)
        self.form.addRow(self.table)
        if not rows:
            self.form.addRow(QLabel("The database defines no attributes of its own."))

    def _work_out(self) -> list[Change]:
        return self.table.changes(())


#: The columns of the definitions table.
D_NAME, D_KIND, D_TYPE, D_MIN, D_MAX, D_CHOICES, D_DEFAULT = range(7)


class DefinitionsDialog(_EditDialog):
    """The attributes there are: each one's name, what it is of, its type, its
    range or its choices, and its default.

    What one is of, and its type, cannot be changed while it is set anywhere:
    those boxes are greyed, and say so.
    """

    def __init__(
        self, parent: QWidget | None, specs: list[AttributeSpec], uses: dict[str, int]
    ) -> None:
        super().__init__(parent, "Attribute definitions")
        self._had = {spec.name: spec for spec in specs}
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["Name", "Of", "Type", "Minimum", "Maximum", "Choices", "Default"]
        )
        self.table.horizontalHeader().setSectionResizeMode(D_CHOICES, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(D_NAME, QHeaderView.ResizeToContents)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAccessibleName("Attribute definitions")
        self.table.setToolTip("Choices are for a choice of names: separated by commas.")
        for spec in specs:
            self._add(spec, uses.get(spec.name, 0))
        self.form.addRow(self.table)
        self.form.addRow(_list_buttons(self.add_definition, self.remove_definition))
        self._removed: list[str] = []
        self.resize(760, 360)

    def _add(self, spec: AttributeSpec | None, uses: int = 0) -> int:
        r = self.table.rowCount()
        self.table.insertRow(r)
        spec = spec or AttributeSpec(self._fresh(), "message", "INT", 0, 0)
        name = QTableWidgetItem(spec.name)
        name.setData(Qt.UserRole, spec.name if spec.name in self._had else None)
        if spec.name in KEPT_BY_THE_FORMAT:
            name.setFlags(name.flags() & ~Qt.ItemIsEditable)
            name.setToolTip("Kept by the format itself: not renamed or removed here.")
        self.table.setItem(r, D_NAME, name)
        kind = QComboBox()
        for text, value in KIND_NAMES:
            kind.addItem(text, value)
        kind.setCurrentIndex(max(0, kind.findData(spec.kind)))
        kind_type = QComboBox()
        kind_type.addItems(TYPES)
        kind_type.setCurrentText(spec.type_name)
        for box, label in ((kind, "Of"), (kind_type, "Type")):
            box.setAccessibleName(label)
            if uses:
                box.setEnabled(False)
                box.setToolTip(f"Set on {uses}: take those back to the default first.")
        self.table.setCellWidget(r, D_KIND, kind)
        self.table.setCellWidget(r, D_TYPE, kind_type)
        for column, text in (
            (D_MIN, _number_text(spec.minimum)),
            (D_MAX, _number_text(spec.maximum)),
            (D_CHOICES, ", ".join(spec.choices)),
            (D_DEFAULT, _number_text(spec.default)),
        ):
            self.table.setItem(r, column, QTableWidgetItem(text))
        return r

    def _fresh(self) -> str:
        taken = {
            (self.table.item(r, D_NAME) or QTableWidgetItem()).text()
            for r in range(self.table.rowCount())
        }
        n = 1
        while f"Attribute{n}" in taken:
            n += 1
        return f"Attribute{n}"

    def add_definition(self) -> None:
        r = self._add(None)
        self.table.setCurrentCell(r, D_NAME)
        self.table.editItem(self.table.item(r, D_NAME))

    def remove_definition(self) -> None:
        r = self.table.currentRow()
        if r < 0:
            return
        had = self.table.item(r, D_NAME).data(Qt.UserRole)
        if had in KEPT_BY_THE_FORMAT:
            self.show_problem(f"{had} is kept by the format itself: not removed here.")
            return
        if had is not None:
            self._removed.append(had)
        self.table.removeRow(r)

    def _spec(self, r: int) -> AttributeSpec:
        def text(column: int) -> str:
            return (self.table.item(r, column) or QTableWidgetItem()).text().strip()

        name = text(D_NAME)
        kind = self.table.cellWidget(r, D_KIND).currentData()
        type_name = self.table.cellWidget(r, D_TYPE).currentText()
        whole = type_name in ("INT", "HEX")

        def numeric(column: int, what: str):
            raw = text(column)
            if not raw or type_name not in ("INT", "HEX", "FLOAT"):
                return None
            try:
                return int(raw, 0) if whole else float(raw)
            except ValueError:
                raise NotANumberError(f"{name}: the {what}, {raw}, is not a number.") from None

        choices = tuple(names(text(D_CHOICES))) if type_name == "ENUM" else ()
        default: object = text(D_DEFAULT) or None
        if default is not None and type_name in ("INT", "HEX", "FLOAT"):
            default = numeric(D_DEFAULT, "default")
        if default is not None and type_name == "ENUM" and default not in choices:
            raise NotANumberError(f"{name}: the default, {default}, is not one of its choices.")
        if type_name == "ENUM" and not choices:
            raise NotANumberError(f"{name} is a choice of names, and has none.")
        return AttributeSpec(
            name=name,
            kind=kind,
            type_name=type_name,
            minimum=numeric(D_MIN, "minimum"),
            maximum=numeric(D_MAX, "maximum"),
            choices=choices,
            default=default,
        )

    def _work_out(self) -> list[Change]:
        specs = [self._spec(r) for r in range(self.table.rowCount())]
        _check_names([spec.name for spec in specs], "attribute")
        out: list[Change] = [RemoveAttributeDefinition(name) for name in self._removed]
        added: list[Change] = []
        for r, spec in enumerate(specs):
            had = self.table.item(r, D_NAME).data(Qt.UserRole)
            if had is None:
                added.append(AddAttributeDefinition(spec))
            elif not _same_spec(spec, self._had[had]):
                out.append(SetAttributeDefinition(had, spec))
        return out + added


def _same_spec(a: AttributeSpec, b: AttributeSpec) -> bool:
    """Whether a definition was left as it was: a number shown and read back
    is the same number, and a range of none and of 0 to 0 the same range."""

    def norm(spec: AttributeSpec) -> tuple:
        numeric = spec.type_name in ("INT", "HEX", "FLOAT")
        low = (spec.minimum or 0) if numeric else None
        high = (spec.maximum or 0) if numeric else None
        default = None if spec.default == "" else spec.default  # an empty text is none
        return (spec.name, spec.kind, spec.type_name, low, high, tuple(spec.choices), default)

    return norm(a) == norm(b)


def focus_first(dialog: QDialog) -> None:
    """The name field first, with its text chosen, ready to type over."""
    dialog.name.setFocus(Qt.TabFocusReason)
    dialog.name.selectAll()
