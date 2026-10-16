# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""The Database pane's dialogs for a message and for a signal.

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
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pycangui.core.dbc_edit import AddMessage, AddSignal, Change, SetMessage, SetSignal
from pycangui.ui import fonts

INTEL, MOTOROLA = "little_endian", "big_endian"
BYTE_ORDERS = (("Intel (little-endian)", INTEL), ("Motorola (big-endian)", MOTOROLA))
UNSIGNED, SIGNED, FLOAT = "Unsigned", "Signed", "Float"
LIST_TIP = "Names separated by commas."
#: How tall a comment box is, in lines of text.
COMMENT_LINES = 3


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
    unit, who receives it, its comment and its named values.

    ``signal`` None is a new signal of the message named.
    """

    def __init__(self, parent: QWidget | None, message: str, signal=None) -> None:
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
        self.values = QTableWidget(0, 2)
        self.values.setHorizontalHeaderLabels(["Value", "Name"])
        self.values.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.values.verticalHeader().hide()
        self.values.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.values.setAccessibleName("Named values")
        for value, text in ({} if s is None else (s.choices or {})).items():
            self.add_value(int(value), str(text))
        add = QPushButton("Add")
        add.clicked.connect(lambda: self.add_value(None, ""))
        remove = QPushButton("Remove")
        remove.clicked.connect(self._remove_values)
        value_buttons = QHBoxLayout()
        value_buttons.addWidget(add)
        value_buttons.addWidget(remove)
        value_buttons.addStretch()
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
        ):
            field.setAccessibleName(label)

    def add_value(self, value: int | None, text: str) -> None:
        row = self.values.rowCount()
        self.values.insertRow(row)
        self.values.setItem(row, 0, QTableWidgetItem("" if value is None else str(value)))
        self.values.setItem(row, 1, QTableWidgetItem(text))
        if value is None:
            self.values.setCurrentCell(row, 0)
            self.values.editItem(self.values.item(row, 0))

    def _remove_values(self) -> None:
        for row in sorted({i.row() for i in self.values.selectedIndexes()}, reverse=True):
            self.values.removeRow(row)

    def _choices(self) -> dict[int, str]:
        out: dict[int, str] = {}
        for row in range(self.values.rowCount()):
            raw = (self.values.item(row, 0) or QTableWidgetItem()).text().strip()
            text = (self.values.item(row, 1) or QTableWidgetItem()).text().strip()
            if not raw and not text:
                continue  # a row added and left empty
            try:
                value = int(raw, 0)
            except ValueError:
                raise NotANumberError(
                    f"A named value, {raw or 'empty'}, is not a whole number."
                ) from None
            if not text:
                raise NotANumberError(f"The value {value} has no name.")
            out[value] = text
        return out

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
            "choices": self._choices(),
        }
        name = self.name.text().strip()
        if self.signal is None:
            out: list[Change] = [AddSignal(self.message, name, wanted["start"], wanted["length"])]
            defaults = {"is_signed": False, "is_float": False, "scale": 1, "offset": 0}
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
        }
        out = [SetSignal(self.message, s.name, f, v) for f, v in wanted.items() if had[f] != v]
        if name != s.name:
            out.append(SetSignal(self.message, s.name, "name", name))
        return out


def focus_first(dialog: QDialog) -> None:
    """The name field first, with its text chosen, ready to type over."""
    dialog.name.setFocus(Qt.TabFocusReason)
    dialog.name.selectAll()
