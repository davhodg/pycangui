# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Setting up a message's counter and checksum.

Two groups, each switched on by its own checkbox, because a message may have
either, both or neither and the common case is neither.  A preview of the
next few frames sits at the bottom: a checksum is invisible from the sending
end -- the frames go out looking fine whether or not the arithmetic is what
the device expects -- so the one thing this dialog can usefully do is show
the bytes before anybody puts them on a bus.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from pycangui.core import tx_fields as tx
from pycangui.ui import messages

TITLE = "Counter and checksum"

WHY = (
    "A receiver that checks a rolling counter or a checksum rejects every "
    "frame of a message that never changes.  Set them here and each frame is "
    "computed as it is sent."
)

TIMING_NOTE = (
    "A message with either of these is sent by pycangui's own timer rather "
    "than by the adapter, because every frame has to differ.  Expect a little "
    "more jitter in the period than the adapter would give you."
)

PARTS = [("Whole byte", tx.WHOLE), ("Low nibble", tx.LOW), ("High nibble", tx.HIGH)]
PREVIEW_FRAMES = 4


class PlacementBox(QWidget):
    """Which byte, and which part of it."""

    def __init__(self, parent: QWidget, allow_two: bool, changed) -> None:
        super().__init__(parent)
        self.byte = QSpinBox()
        self.byte.setRange(0, 63)
        self.byte.setPrefix("byte ")
        self.part = QComboBox()
        for label, value in PARTS:
            self.part.addItem(label, value)
        self.endian = QComboBox()
        self.endian.addItem("big endian", tx.BIG)
        self.endian.addItem("little endian", tx.LITTLE)
        self.endian.setVisible(allow_two)
        self._allow_two = allow_two

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.byte)
        row.addWidget(self.part)
        row.addWidget(self.endian)
        row.addStretch()

        self.byte.valueChanged.connect(changed)
        self.part.currentIndexChanged.connect(changed)
        self.endian.currentIndexChanged.connect(changed)

    def set_two_bytes(self, two: bool) -> None:
        """A 16-bit checksum is two whole bytes; nibbles stop making sense."""
        self.part.setEnabled(not two)
        self.endian.setVisible(two)
        if two:
            self.part.setCurrentIndex(0)

    def value(self) -> tx.Placement:
        two = self._allow_two and self.endian.isVisible()
        return tx.Placement(
            byte=self.byte.value(),
            part=tx.WHOLE if two else self.part.currentData(),
            width=2 if two else 1,
            endian=self.endian.currentData(),
        )

    def set_value(self, at: tx.Placement) -> None:
        self.byte.setValue(at.byte)
        self.part.setCurrentIndex(max(0, [p for _l, p in PARTS].index(at.part)))
        self.endian.setCurrentIndex(0 if at.endian == tx.BIG else 1)


class TxFieldsDialog(QDialog):
    def __init__(
        self,
        parent: QWidget,
        payload: bytes,
        counter: tx.Counter | None = None,
        checksum: tx.Checksum | None = None,
        label: str = "",
        signals: list[str] | None = None,
        encode=None,
        values: dict | None = None,
        bits: dict[str, int] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{TITLE} -- {label}" if label else TITLE)
        self.resize(560, 620)
        self._payload = payload or b"\x00" * 8
        #: The database signals of this message, where it has any.  Naming a
        #: field beats counting to it, and is simpler underneath as well: the
        #: database does the bit packing, so no byte or nibble has to be
        #: worked out, and a signal that straddles a byte is not a problem
        #: anybody has to think about.
        self._signals = signals or []
        #: How to turn signal values into bytes, so the preview still works
        #: when the fields are named rather than placed.  The preview is most
        #: of what this dialog is for, and losing it on the database path
        #: would be losing it exactly where the arithmetic is least visible.
        self._encode = encode
        self._values = values or {}
        self._bits = bits or {}

        why = QLabel(WHY)
        why.setWordWrap(True)

        # --- the counter ---
        self.use_counter = QCheckBox("Add a counter")
        self.counter_at = PlacementBox(self, allow_two=False, changed=self._refresh)
        self.start = QSpinBox()
        self.start.setRange(0, 65535)
        self.step = QSpinBox()
        self.step.setRange(1, 255)
        self.step.setValue(1)
        self.wrap = QSpinBox()
        self.wrap.setRange(0, 65536)
        self.wrap.setSpecialValueText("as many as the field holds")
        self.wrap.setToolTip(
            "Count 0, 1, 2 ... up to one less than this, then back to the start.\n"
            "Left alone it wraps at whatever the byte or nibble holds, which is\n"
            "what most messages do."
        )
        self.counter_where = self._where_box()
        counter_form = QFormLayout()
        counter_form.addRow("Put it in:", self.counter_where)
        self._counter_at_row = counter_form.rowCount()
        counter_form.addRow("", self.counter_at)
        counter_form.addRow("Start at:", self.start)
        counter_form.addRow("Step by:", self.step)
        counter_form.addRow("Wrap at:", self.wrap)
        self.counter_box = QGroupBox()
        self.counter_box.setLayout(counter_form)

        # --- the checksum ---
        self.use_checksum = QCheckBox("Add a checksum")
        self.algorithm = QComboBox()
        for key, name in tx.ALGORITHM_NAMES.items():
            self.algorithm.addItem(name, key)
        self.checksum_at = PlacementBox(self, allow_two=True, changed=self._refresh)
        self.whole = QCheckBox("Over the whole message except the checksum itself")
        self.whole.setChecked(True)
        self.whole.setToolTip(
            "The usual rule.  Including the checksum's own bytes means hashing\n"
            "a field that is about to be overwritten, so the number never\n"
            "matches at the other end."
        )
        self.first = QSpinBox()
        self.first.setRange(0, 63)
        self.last = QSpinBox()
        self.last.setRange(0, 63)
        self.last.setValue(6)
        over = QHBoxLayout()
        over.addWidget(QLabel("bytes"))
        over.addWidget(self.first)
        over.addWidget(QLabel("to"))
        over.addWidget(self.last)
        over.addStretch()
        self.checksum_where = self._where_box()
        checksum_form = QFormLayout()
        checksum_form.addRow("Algorithm:", self.algorithm)
        checksum_form.addRow("Put it in:", self.checksum_where)
        self._checksum_at_row = checksum_form.rowCount()
        checksum_form.addRow("", self.checksum_at)
        checksum_form.addRow("", self.whole)
        checksum_form.addRow("Computed over:", over)
        self._checksum_form = checksum_form
        self._counter_form = counter_form
        self.checksum_box = QGroupBox()
        self.checksum_box.setLayout(checksum_form)

        hook_note = QLabel(
            "Not in the list?  A maker's own arithmetic goes in the "
            "<b>transmit.checksum</b> hook, and is written wherever you put it here."
        )
        hook_note.setWordWrap(True)
        hook_note.setTextFormat(Qt.RichText)

        # --- what it will send ---
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFont(QFont("Consolas", 9))
        self.preview.setFixedHeight(90)
        preview_box = QGroupBox("The next few frames")
        inside = QVBoxLayout(preview_box)
        inside.addWidget(self.preview)

        timing = QLabel(TIMING_NOTE)
        timing.setWordWrap(True)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._ok)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(why)
        layout.addWidget(self.use_counter)
        layout.addWidget(self.counter_box)
        layout.addWidget(self.use_checksum)
        layout.addWidget(self.checksum_box)
        layout.addWidget(hook_note)
        layout.addWidget(preview_box)
        layout.addWidget(timing)
        layout.addWidget(buttons)

        for widget in (self.use_counter, self.use_checksum, self.whole):
            widget.toggled.connect(self._refresh)
        for widget in (self.start, self.step, self.wrap, self.first, self.last):
            widget.valueChanged.connect(self._refresh)
        self.algorithm.currentIndexChanged.connect(self._algorithm_changed)

        self._load(counter, checksum)
        self._algorithm_changed()

    # --- naming a field rather than counting to it --------------------------------
    def _where_box(self) -> QComboBox:
        """At a position, or in one of the message's signals.

        Only offered where the message has signals: a raw row is bytes and
        nothing else, and an empty list of names would be a choice that
        cannot be made.
        """
        box = QComboBox()
        box.addItem("a byte position", "")
        for name in self._signals:
            box.addItem(f"signal {name}", name)
        box.setVisible(bool(self._signals))
        box.currentIndexChanged.connect(self._refresh)
        return box

    def _signal_of(self, box: QComboBox) -> str:
        return box.currentData() if self._signals else ""

    def _select_signal(self, box: QComboBox, name: str) -> None:
        index = box.findData(name)
        box.setCurrentIndex(index if index >= 0 else 0)

    # --- filling in and reading back --------------------------------------------
    def _load(self, counter: tx.Counter | None, checksum: tx.Checksum | None) -> None:
        self.use_counter.setChecked(counter is not None)
        if counter is not None:
            self._select_signal(self.counter_where, counter.signal)
            if counter.at is not None:
                self.counter_at.set_value(counter.at)
            self.start.setValue(counter.start)
            self.step.setValue(counter.step)
            self.wrap.setValue(counter.wrap)
        else:
            self.counter_at.byte.setValue(0)
            self.counter_at.part.setCurrentIndex(1)  # a nibble, the common case

        self.use_checksum.setChecked(checksum is not None)
        if checksum is not None:
            index = self.algorithm.findData(checksum.algorithm)
            self.algorithm.setCurrentIndex(max(0, index))
            self._select_signal(self.checksum_where, checksum.signal)
            if checksum.at is not None:
                self.checksum_at.set_value(checksum.at)
            self.whole.setChecked(checksum.first is None and checksum.last is None)
            if checksum.first is not None:
                self.first.setValue(checksum.first)
            if checksum.last is not None:
                self.last.setValue(checksum.last)
        else:
            self.checksum_at.byte.setValue(max(0, len(self._payload) - 1))

    def _algorithm_changed(self) -> None:
        self.checksum_at.set_two_bytes(tx.width_of(self.algorithm.currentData()) == 2)
        self._refresh()

    def _ok(self) -> None:
        """Accept, unless the two fields would be written over each other.

        Refused here rather than warned about later: the dialog is the only
        place the arrangement can be seen, and a pair that overlaps sends
        wrong frames that look right from this end.
        """
        if (clash := tx.conflict(self.counter(), self.checksum())) is not None:
            messages.warning(
                self,
                "They cannot both go there",
                f"{clash[0].upper()}{clash[1:]}.\n\nMove one of them and the "
                "preview will show both.",
            )
            return
        self.accept()

    def counter(self) -> tx.Counter | None:
        if not self.use_counter.isChecked():
            return None
        signal = self._signal_of(self.counter_where)
        return tx.Counter(
            at=None if signal else self.counter_at.value(),
            signal=signal,
            start=self.start.value(),
            step=self.step.value(),
            wrap=self.wrap.value(),
        )

    def checksum(self) -> tx.Checksum | None:
        if not self.use_checksum.isChecked():
            return None
        whole = self.whole.isChecked()
        signal = self._signal_of(self.checksum_where)
        return tx.Checksum(
            at=None if signal else self.checksum_at.value(),
            signal=signal,
            algorithm=self.algorithm.currentData(),
            first=None if whole else self.first.value(),
            last=None if whole else self.last.value(),
        )

    # --- the preview, which is the point of the dialog ----------------------------
    def _refresh(self) -> None:
        self.counter_box.setEnabled(self.use_counter.isChecked())
        self.checksum_box.setEnabled(self.use_checksum.isChecked())
        # A named signal has no byte position to choose, and leaving the
        # spinner on screen would invite somebody to set it and then wonder
        # why nothing moved.
        self.counter_at.setVisible(not self._signal_of(self.counter_where))
        by_signal = bool(self._signal_of(self.checksum_where))
        self.checksum_at.setVisible(not by_signal)
        # Nor a byte range: a signal's own bits are zeroed instead of whole
        # bytes being left out, because a signal can share a byte with data
        # that has to survive.
        self.whole.setVisible(not by_signal)
        self.first.setEnabled(not by_signal and not self.whole.isChecked())
        self.last.setEnabled(not by_signal and not self.whole.isChecked())
        self.preview.setPlainText(self._frames())

    def _frames(self) -> str:
        try:
            counter, checksum = self.counter(), self.checksum()
        except tx.FieldError as exc:
            return str(exc)
        if counter is None and checksum is None:
            return "  ".join(f"{b:02X}" for b in self._payload) + "    (every frame the same)"
        by_signal = bool((counter and counter.signal) or (checksum and checksum.signal))
        if by_signal and self._encode is None:
            return "The database packs these; the row itself will show the bytes."
        lines = []
        for n in range(PREVIEW_FRAMES):
            try:
                if by_signal:
                    out = tx.apply_signals(
                        self._encode,
                        self._values,
                        counter,
                        checksum,
                        sent=n,
                        counter_bits=self._bits.get(counter.signal) if counter else None,
                    )
                else:
                    out = tx.apply(self._payload, counter, checksum, sent=n)
            except tx.FieldError as exc:
                return str(exc)
            except Exception as exc:  # the database refused the values
                return f"{type(exc).__name__}: {exc}"
            lines.append(f"{n + 1}:  " + " ".join(f"{b:02X}" for b in out))
        return "\n".join(lines)
