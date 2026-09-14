# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""The seven ways a pane can show an object, and no eighth.

A manufacturer's tool has two dozen hand-built configuration screens, and
every one of them turns out to be made of the same small set of parts: a
range-checked number, a code in hex, a named choice, a word of flags, a field
packed into some bits of a larger object, an editable XY map, and a value that
is only read.  Implement those and a user can assemble the screens rather than
somebody compiling them in.

Two rules run through all of them.

**A widget never invents a value.**  ``flags`` and ``bits`` write part of an
object, and part of an object cannot be written -- three bits of a 32 bit word
go out with the other twenty-nine.  So they read first and refuse to write
until they have, rather than sending a word made mostly of zeroes.

**What is refused is refused here.**  A number outside the limits the EDS
declared never reaches the bus: a node is free to clamp silently, and a
parameter that did not take is very much worse than one that was not sent.
"""

from __future__ import annotations

import math
from typing import Any

import pyqtgraph as pg
from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pycangui.canopen.display import (
    Display,
    as_number,
    format_number,
    limits_text,
    out_of_range,
)
from pycangui.canopen.display import text as value_text
from pycangui.custom_panes.model import Field, display_for

#: A value that was refused, and one that has not been read yet.
REFUSED = QColor(200, 40, 40)
UNREAD = QColor(140, 140, 140)

#: A box holding a value that has been typed and not written.  Translucent, so
#: it reads on a light theme and a dark one alike.
PENDING = "QLineEdit { background-color: rgba(230, 150, 0, 70); }"
KEYS_HINT = "Enter writes what is typed; Esc puts back the value last read."
STEP_HINT = "Ctrl+Up doubles it and Ctrl+Down halves it, without writing."

#: An array's sub 0 is how many entries it has, which is where a map starts.
COUNT_SUB = 0
#: Nothing sane has more, and a corrupt count should not ask for a thousand.
MAX_POINTS = 128


class FieldWidget(QWidget):
    """One object on a pane: shows what it is, and asks for what it should be.

    Everything is by (index, sub) rather than implied, because a map is one
    field over many sub-indices and the rest of the machinery should not have
    to know which sort it is holding.
    """

    #: Please fetch this.  The pane forwards it to whatever source it is bound
    #: to -- a live node, a DCF, an EDS's defaults.
    read_requested = Signal(int, int)
    #: index, sub, raw value to write.
    write_requested = Signal(int, int, object)
    #: Something the user should be told: a refusal, mostly.
    message = Signal(str)

    #: Whether Ctrl+Up and Ctrl+Down double and halve what is typed.
    can_step = False

    def __init__(self, item: Field, display: Display) -> None:
        super().__init__()
        self.field = item
        self.display = display_for(item, display)
        self.writable = True
        self._raw: Any = None
        #: The box typed into, for the sorts that have one.
        self._typed: QLineEdit | None = None
        #: Something is typed in it that has not been written.
        self.pending = False

    # --- what the pane asks of it ---------------------------------------------------
    def refresh(self) -> None:
        """Ask for whatever this field needs in order to show itself."""
        self.read_requested.emit(self.field.index, self.field.sub)

    def set_value(self, index: int, sub: int, raw: Any, error: Any = None) -> None:
        if (index, sub) != self.field.where:
            return
        self._raw = None if error else raw
        self._show(raw, error)

    def set_writable(self, on: bool) -> None:
        self.writable = on
        self._apply_writable()

    # --- what each sort fills in --------------------------------------------------------
    def _show(self, raw: Any, error: Any) -> None:
        """Put a value, or the reason there is none, on screen."""

    def _apply_writable(self) -> None:
        """Stop offering to write when the source cannot be written to."""

    # --- shared helpers -------------------------------------------------------------------
    def _tooltip(self) -> str:
        where = f"0x{self.field.index:04X}:{self.field.sub:02X}"
        lines = [where]
        if self.display.description:
            lines.append(self.display.description)
        if limits := limits_text(self.display):
            lines.append(f"Limits: {limits}")
        if self._raw is not None:
            lines.append(f"Raw: {self._raw}")
        if self._typed is not None and self.writable:
            lines.append(KEYS_HINT)
            if self.can_step:
                lines.append(STEP_HINT)
        return "\n".join(lines)

    def _refuse(self, why: str) -> None:
        self.message.emit(f"{self.label_text()}: {why}")

    # --- a box that is typed into -----------------------------------------------------------
    def _typed_into(self, edit: QLineEdit) -> None:
        """Enter writes, Esc puts back, and nothing else does either.

        Leaving the box used to write it too, which is how a value still being
        thought about reached a controller because somebody clicked on the
        plot.  What is typed now stays typed -- tinted, and safe from polling --
        until it is sent or taken back.
        """
        self._typed = edit
        edit.installEventFilter(self)
        edit.textEdited.connect(self._on_typed)

    def eventFilter(self, watched, event) -> bool:
        if watched is self._typed and event.type() in (QEvent.KeyPress, QEvent.ShortcutOverride):
            action = self._key_action(event)
            if action is not None:
                # Accepting the override is what stops a window shortcut on the
                # same keys taking them before the box sees them.
                event.accept()
                if event.type() == QEvent.KeyPress:
                    action()
                return True
        return super().eventFilter(watched, event)

    def _key_action(self, event):
        key = event.key()
        if key in (Qt.Key_Return, Qt.Key_Enter):  # Ctrl+Enter as well as Enter
            return self._on_entered
        if key == Qt.Key_Escape:
            return self._put_back
        if self.can_step and event.modifiers() & Qt.ControlModifier:
            if key == Qt.Key_Up:
                return lambda: self._step(2.0)
            if key == Qt.Key_Down:
                return lambda: self._step(0.5)
        return None

    def _on_typed(self, _text: str) -> None:
        # Typing back exactly what was read is not a change.
        self._set_pending(self._typed.text() != self._box_text(self._raw))

    def _set_pending(self, on: bool) -> None:
        self.pending = on
        if self._typed is not None:
            self._typed.setStyleSheet(PENDING if on else "")

    def _put_back(self) -> None:
        """The value last read, over whatever was typed."""
        self._set_pending(False)
        if self._typed is not None:
            self._typed.setText(self._box_text(self._raw))

    def _busy(self) -> bool:
        """Being typed in, or holding something typed and not sent.

        Either way an arriving value must not replace it: a polled value
        landing in the box would take the typed one with it, and the next
        thing pressed would be Enter -- which would write whatever had
        replaced it.
        """
        return self._typed is not None and (self._typed.hasFocus() or self.pending)

    def _box_text(self, raw: Any) -> str:
        """What the box shows for this raw value."""
        return "" if raw is None else str(raw)

    def _on_entered(self) -> None:
        """Write what is typed."""

    def _step(self, by: float) -> None:
        """Multiply what is typed, without writing it."""

    def label_text(self) -> str:
        return self.display.name or f"0x{self.field.index:04X}:{self.field.sub:02X}"


class ValueWidget(FieldWidget):
    """Read only.  What the object says, in the terms it is understood in."""

    def __init__(self, item: Field, display: Display) -> None:
        super().__init__(item, display)
        self.value = QLabel("--")
        self.value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.value)

    def _show(self, raw: Any, error: Any) -> None:
        self.value.setText(str(error) if error else value_text(self.display, raw))
        self.value.setStyleSheet(f"color: {REFUSED.name()}" if error else "")
        self.value.setToolTip(self._tooltip())


class _EntryWidget(FieldWidget):
    """A box you type into, and the shared business of typing into one."""

    def __init__(self, item: Field, display: Display) -> None:
        super().__init__(item, display)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("--")
        self._typed_into(self.edit)
        self.unit = QLabel(self.display.unit)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.edit)
        if self.display.unit:
            layout.addWidget(self.unit)
        self.edit.setToolTip(self._tooltip())

    def _apply_writable(self) -> None:
        self.edit.setReadOnly(not self.writable)
        self.edit.setToolTip(self._tooltip())

    def _show(self, raw: Any, error: Any) -> None:
        self.edit.setToolTip(self._tooltip())
        if self._busy():
            return  # see _busy
        if error:
            self.edit.clear()
            self.edit.setPlaceholderText(str(error))
            return
        self.edit.setPlaceholderText("--")
        self.edit.setText(self._as_text(raw))

    def _as_text(self, raw: Any) -> str:
        return "" if raw is None else str(raw)

    def _box_text(self, raw: Any) -> str:
        return self._as_text(raw)

    def _on_entered(self) -> None:
        if not self.writable:
            return
        typed = self.edit.text().strip()
        if not typed:
            return
        raw = self._as_raw(typed)
        if raw is None:
            self._refuse(f"{typed!r} is not a number")
            self._put_back()
            return
        if why := out_of_range(self.display, raw):
            # Never sent.  A node may clamp silently, and a parameter that did
            # not take is worse than one that was not sent.
            self._refuse(f"{typed} is {why}")
            self._put_back()
            return
        self._set_pending(False)
        self.write_requested.emit(self.field.index, self.field.sub, self._to_wire(raw))

    def _as_raw(self, typed: str) -> float | None:
        return as_number(typed)

    @staticmethod
    def _to_wire(raw: float) -> Any:
        return int(raw) if float(raw).is_integer() else raw


class NumberWidget(_EntryWidget):
    """A number in its own units, refused if the EDS says it is out of range."""

    can_step = True

    def _step(self, by: float) -> None:
        """Double or halve what is in the box, and leave it there to be sent.

        It sounds trivial and is not: it is how a gain is walked in on a bench,
        a factor of two at a time, each one tried before the next.  A value the
        object holds as a whole number halves towards zero, so 1 goes to 0
        rather than to a 0.5 the object cannot store.  Limits are checked when
        it is written, like anything else typed.
        """
        if not self.writable:
            return
        typed = self.edit.text().strip()
        if typed:
            before = self._as_raw(typed)
            if before is None:
                self._refuse(f"{typed!r} is not a number")
                return
        elif self._raw is not None and not isinstance(self._raw, bool | str | bytes):
            before = self._raw
        else:
            return
        before = round(float(before), 6)  # 123.4 / 0.1 is 1233.9999999999998
        if self.display.offset:
            after = self.display.raw(self.display.physical(before) * by)
        else:
            after = before * by
        after = round(after, 6)
        whole = isinstance(self._raw, int) or (self._raw is None and before.is_integer())
        if whole:
            after = math.trunc(after)
        self.edit.setText(self._as_text(after))
        self._set_pending(self.edit.text() != self._box_text(self._raw))

    def _as_text(self, raw: Any) -> str:
        if raw is None:
            return ""
        if self.display.scaled:
            return format_number(self.display.physical(raw), self.display.decimals)
        return format_number(raw, self.display.decimals)

    def _as_raw(self, typed: str) -> float | None:
        """Typed in the units it is shown in, stored in the units it goes in."""
        physical = as_number(typed)
        if physical is None:
            return None
        return self.display.raw(physical) if self.display.scaled else physical


class HexWidget(_EntryWidget):
    """The same, in hex.  For codes, masks and identifiers rather than quantities."""

    def _as_text(self, raw: Any) -> str:
        return "" if raw is None else f"0x{int(raw):X}"

    def _as_raw(self, typed: str) -> float | None:
        # Bare digits are hex here, since that is what the box is showing.  A
        # 0x prefix is accepted too rather than being called a typo.
        text = typed.strip()
        try:
            return float(int(text, 16 if not text.lower().startswith("0x") else 0))
        except ValueError:
            return None


class EnumWidget(FieldWidget):
    """A dropdown of the values that have names."""

    def __init__(self, item: Field, display: Display) -> None:
        super().__init__(item, display)
        self.box = QComboBox()
        for raw, name in sorted(self.display.choices.items()):
            self.box.addItem(f"{name} ({raw})", raw)
        self.box.activated.connect(self._on_chosen)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.box)

    def _apply_writable(self) -> None:
        self.box.setEnabled(self.writable)

    def _show(self, raw: Any, error: Any) -> None:
        self.box.setToolTip(str(error) if error else self._tooltip())
        if self.box.hasFocus():
            return  # being chosen from; see the note in _EntryWidget
        if error or raw is None:
            self.box.setCurrentIndex(-1)
            return
        at = self.box.findData(int(raw))
        if at < 0:
            # A value the file did not name.  Shown rather than hidden: a
            # dropdown that silently displays the wrong entry is worse than one
            # that admits it does not know this value.
            self.box.addItem(f"{int(raw)} (not named)", int(raw))
            at = self.box.count() - 1
        self.box.setCurrentIndex(at)

    def _on_chosen(self, _at: int) -> None:
        if self.writable and (data := self.box.currentData()) is not None:
            self.write_requested.emit(self.field.index, self.field.sub, int(data))


class FlagsWidget(FieldWidget):
    """One named tick per bit.  Only the named ones: a word with three
    meaningful bits should not put thirty-two boxes on a form."""

    def __init__(self, item: Field, display: Display) -> None:
        super().__init__(item, display)
        self.boxes: dict[int, QCheckBox] = {}
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        for at, (bit, name) in enumerate(sorted(item.bits.items())):
            box = QCheckBox(name)
            box.setToolTip(f"Bit {bit}")
            box.clicked.connect(lambda checked, b=bit: self._on_toggled(b, checked))
            grid.addWidget(box, at // 4, at % 4)
            self.boxes[bit] = box

    def _apply_writable(self) -> None:
        for box in self.boxes.values():
            box.setEnabled(self.writable)

    def _show(self, raw: Any, error: Any) -> None:
        known = not error and raw is not None
        for bit, box in self.boxes.items():
            box.blockSignals(True)
            box.setChecked(bool(known and int(raw) >> bit & 1))
            box.blockSignals(False)
            box.setToolTip(f"Bit {bit}" if known else str(error or "not read yet"))
        self.setToolTip(self._tooltip())

    def _on_toggled(self, bit: int, checked: bool) -> None:
        if not self.writable:
            return
        if self._raw is None:
            # The other bits are unknown, and writing the word would clear
            # them.  Put the tick back and say so rather than guessing.
            self._refuse("not read yet, so the other bits are unknown")
            self.boxes[bit].setChecked(not checked)
            self.read_requested.emit(self.field.index, self.field.sub)
            return
        word = int(self._raw) | (1 << bit) if checked else int(self._raw) & ~(1 << bit)
        self.write_requested.emit(self.field.index, self.field.sub, word)


class BitsWidget(FieldWidget):
    """A field packed into some of the bits of a larger object.

    Shown as a dropdown when the values are named and as a number when they are
    not, because that is the only difference between the two once the bits have
    been picked out.
    """

    def __init__(self, item: Field, display: Display) -> None:
        super().__init__(item, display)
        self.box: QComboBox | None = None
        self.edit: QLineEdit | None = None
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        if self.display.choices:
            self.box = QComboBox()
            for raw, name in sorted(self.display.choices.items()):
                self.box.addItem(f"{name} ({raw})", raw)
            self.box.activated.connect(lambda _at: self._send(self.box.currentData()))
            layout.addWidget(self.box)
        else:
            self.edit = QLineEdit()
            self.edit.setPlaceholderText("--")
            self._typed_into(self.edit)
            layout.addWidget(self.edit)
        bits = (
            f"bit {item.first}"
            if item.width == 1
            else f"bits {item.first}..{item.first + item.width - 1}"
        )
        layout.addWidget(QLabel(f"[{bits}]"))

    def _apply_writable(self) -> None:
        for widget in (self.box, self.edit):
            if widget is not None:
                widget.setEnabled(self.writable)

    def _show(self, raw: Any, error: Any) -> None:
        self.setToolTip(str(error) if error else self._tooltip())
        if (self.box is not None and self.box.hasFocus()) or self._busy():
            return  # being edited; see _busy
        known = not error and raw is not None
        part = self.field.extract(int(raw)) if known else None
        if self.box is not None:
            at = -1 if part is None else self.box.findData(part)
            if part is not None and at < 0:
                self.box.addItem(f"{part} (not named)", part)
                at = self.box.count() - 1
            self.box.setCurrentIndex(at)
        elif self.edit is not None:
            self.edit.setText("" if part is None else str(part))

    def _box_text(self, raw: Any) -> str:
        return "" if raw is None else str(self.field.extract(int(raw)))

    def _on_entered(self) -> None:
        if not self.writable:
            return
        typed = self.edit.text().strip()
        if not typed:
            return
        value = as_number(typed)
        if value is None:
            self._refuse(f"{typed!r} is not a number")
            self._put_back()
            return
        self._send(int(value))

    def _send(self, value: Any) -> None:
        if not self.writable or value is None:
            return
        if self._raw is None:
            # What was typed stays, so Enter works once the read has answered.
            self._refuse("not read yet, so the rest of the word is unknown")
            self.read_requested.emit(self.field.index, self.field.sub)
            return
        if int(value) >= (1 << self.field.width):
            self._refuse(f"{value} does not fit in {self.field.width} bits")
            if self.edit is not None:
                self._put_back()
            else:
                self._show(self._raw, None)
            return
        self._set_pending(False)
        self.write_requested.emit(
            self.field.index, self.field.sub, self.field.insert(int(self._raw), int(value))
        )


class MapWidget(FieldWidget):
    """An array object as an editable table beside its graph.

    This is what a power limit, a thermistor curve, a cutback and a steering
    profile all are, and the pair is the point: the table is how a number is
    entered exactly and the graph is how a mistake is seen.  They show the same
    array, so editing either moves the other.

    The X values come from a second array where the pane names one, and from
    the sub-index number where it does not -- which is what an array of
    breakpoints usually means.
    """

    def __init__(self, item: Field, display: Display) -> None:
        super().__init__(item, display)
        self._points = 0
        self._y: dict[int, float] = {}
        self._x: dict[int, float] = {}

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(
            [item.x_label or "X", item.y_label or self.display.name or "Y"]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.itemChanged.connect(self._on_edited)

        pg.setConfigOptions(antialias=False, background="w", foreground="k")
        self.plot = pg.PlotWidget()
        self.plot.showGrid(x=True, y=True, alpha=0.3)
        self.plot.setLabel("bottom", item.x_label or "X")
        self.plot.setLabel("left", item.y_label or self.display.unit or "Y")
        self.curve = self.plot.plot([], [], pen=pg.mkPen("#1f77b4", width=2), symbol="o")

        self.reread = QPushButton("Read")
        self.reread.setToolTip("Read every point of the map again.")
        self.reread.clicked.connect(self.refresh)

        split = QSplitter(Qt.Horizontal)
        left = QWidget()
        column = QVBoxLayout(left)
        column.setContentsMargins(0, 0, 0, 0)
        column.addWidget(self.table)
        column.addWidget(self.reread)
        split.addWidget(left)
        split.addWidget(self.plot)
        split.setStretchFactor(1, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(split)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumHeight(180)
        self._updating = False

    # --- reading it -------------------------------------------------------------------
    def refresh(self) -> None:
        """Sub 0 first: an array says how many entries it has before it says what they are."""
        self._points = 0
        self.read_requested.emit(self.field.index, COUNT_SUB)

    def set_value(self, index: int, sub: int, raw: Any, error: Any = None) -> None:
        if index == self.field.x_index and self.field.x_index is not None:
            if not error and raw is not None and sub:
                self._x[sub] = float(raw)
                self._redraw()
            return
        if index != self.field.index or error or raw is None:
            return
        if sub == COUNT_SUB:
            self._points = min(int(raw), MAX_POINTS)
            self._start_rows()
            for point in range(1, self._points + 1):
                self.read_requested.emit(self.field.index, point)
                if self.field.x_index is not None:
                    self.read_requested.emit(self.field.x_index, point)
            return
        self._y[sub] = float(raw)
        self._redraw()

    def _start_rows(self) -> None:
        self._updating = True
        self._y.clear()
        self._x.clear()
        self.table.setRowCount(self._points)
        for row in range(self._points):
            for column in (0, 1):
                if self.table.item(row, column) is None:
                    self.table.setItem(row, column, QTableWidgetItem(""))
            self.table.item(row, 0).setFlags(
                Qt.ItemIsEnabled | Qt.ItemIsSelectable
                if self.field.x_index is None
                else Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable
            )
            self.table.item(row, 0).setText(str(row + 1) if self.field.x_index is None else "")
        self._updating = False

    def _redraw(self) -> None:
        self._updating = True
        for point in range(1, self._points + 1):
            row = point - 1
            if row >= self.table.rowCount():
                continue
            if point in self._y:
                self.table.item(row, 1).setText(
                    format_number(self.display.physical(self._y[point]), self.display.decimals)
                    if self.display.scaled
                    else format_number(self._y[point], self.display.decimals)
                )
            if self.field.x_index is not None and point in self._x:
                self.table.item(row, 0).setText(format_number(self._x[point], None))
        self._updating = False
        xs = [self._x.get(p, float(p)) for p in sorted(self._y)]
        ys = [
            self.display.physical(self._y[p]) if self.display.scaled else self._y[p]
            for p in sorted(self._y)
        ]
        self.curve.setData(xs, ys)

    # --- changing it -------------------------------------------------------------------
    def _apply_writable(self) -> None:
        self.table.setEditTriggers(
            QTableWidget.NoEditTriggers if not self.writable else QTableWidget.DoubleClicked
        )

    def _on_edited(self, item: QTableWidgetItem) -> None:
        if self._updating or not self.writable:
            return
        point = item.row() + 1
        typed = item.text().strip()
        value = as_number(typed)
        if value is None:
            self._refuse(f"{typed!r} is not a number")
            self._redraw()
            return
        if item.column() == 0 and self.field.x_index is not None:
            self.write_requested.emit(self.field.x_index, point, _wire(value))
            return
        raw = self.display.raw(value) if self.display.scaled else value
        if why := out_of_range(self.display, raw):
            self._refuse(f"point {point}: {typed} is {why}")
            self._redraw()
            return
        self.write_requested.emit(self.field.index, point, _wire(raw))


def _wire(value: float) -> Any:
    return int(value) if float(value).is_integer() else value


BY_KIND = {
    "value": ValueWidget,
    "number": NumberWidget,
    "hex": HexWidget,
    "enum": EnumWidget,
    "flags": FlagsWidget,
    "bits": BitsWidget,
    "map": MapWidget,
}


def build(item: Field, display: Display) -> FieldWidget:
    """The widget for one field.  An unknown kind is shown, not guessed at."""
    return BY_KIND.get(item.kind, ValueWidget)(item, display)
