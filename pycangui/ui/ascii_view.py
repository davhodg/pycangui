# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Text that a device prints onto the bus.

Some devices use a CAN id as a console: ASCII in the data bytes of an ordinary
frame, a few characters at a time, printf fashion.

No protocol says which identifier that happens on.  CANopen comes closest --
it has objects for a console, and they are PDO mappable -- but an object is
not an identifier, and nothing standard settles which one a device ends up
printing on.  Devices that are not CANopen at all do the same thing on an
identifier of their own choosing.

Which is why the identifier here is yours to give.  One pane reads one id.

It used to be one pane with a tab per id and a button to pop a tab out into a
window of its own -- the right shape when a dock could only ever be one of a
kind, and the wrong one now.  Two streams side by side is the arrangement
people want, and tabs are precisely the thing that forbids it; the pop-out was
a second implementation of what every pane now gets for nothing.  Drop one of
these onto another and Qt tabs them, so the old arrangement is still there --
as a choice rather than as the only option.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Signal, Slot
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from pycangui.core.bus import Frame
from pycangui.core.channels import Channels
from pycangui.core.context import Context

#: How many lines a stream keeps.  A device printing steadily will run to
#: megabytes over an afternoon, and none of it is worth the memory.
MAX_LINES = 5000

SKIP_TIP = (
    "Bytes to ignore at the start of each frame.  Devices often put a\n"
    "length or a sequence number there; a stream of dots at the start\n"
    "of every eight characters is what that looks like."
)
NOTHING_YET = (
    "No id yet.  Type one above.\n\n"
    "There is no standard identifier for this, so it will be in the "
    "documentation for whatever you are listening to."
)


def decode(data: bytes, skip: int = 0) -> str:
    """The printable text in one frame's data.

    NUL bytes are dropped: a frame is a fixed length and the tail of it is
    padding far more often than it is data, so keeping them would put a run of
    holes at the end of every eight characters.  Carriage return goes too, so
    that a device ending its lines with CRLF does not come out double spaced.
    Newline and tab are kept, because they are the layout the device intended.

    Everything else that is not printable becomes a dot rather than
    disappearing.  A stream of dots is how you find out that the id is wrong,
    or that the first byte is a length and `skip` should be 1.
    """
    text = []
    for byte in data[skip:]:
        if byte in (0x00, 0x0D):
            continue
        if byte in (0x0A, 0x09) or 0x20 <= byte <= 0x7E:
            text.append(chr(byte))
        else:
            text.append(".")
    return "".join(text)


@dataclass
class Stream:
    """One id being read as text."""

    can_id: int = 0
    extended: bool = False
    name: str = ""
    skip: int = 0

    @property
    def chosen(self) -> bool:
        """Whether an id has been given.  A pane opens without one."""
        return self.can_id > 0

    @property
    def title(self) -> str:
        number = f"{self.can_id:08X}" if self.extended else f"{self.can_id:03X}"
        return f"{number}  {self.name}" if self.name else number

    @property
    def pane_title(self) -> str:
        """What the dock is called, until somebody renames it."""
        return f"ASCII {self.title}" if self.chosen else "ASCII Log"

    def matches(self, frame: Frame) -> bool:
        return frame.can_id == self.can_id and frame.extended == self.extended and not frame.error

    def to_dict(self) -> dict:
        return {"id": self.can_id, "extended": self.extended, "name": self.name, "skip": self.skip}

    @classmethod
    def from_dict(cls, d: dict) -> Stream:
        try:
            return cls(
                can_id=int(d.get("id", 0)),
                extended=bool(d.get("extended", False)),
                name=str(d.get("name", "")),
                skip=int(d.get("skip", 0)),
            )
        except (TypeError, ValueError, AttributeError):
            return cls()  # a hand-edited settings.json


class AsciiView(QWidget):
    """One identifier, read as text."""

    #: The id, the name or the skip changed.  The window writes it down and
    #: renames the pane; this widget knows about neither.
    changed = Signal(object)

    def __init__(self, channels: Channels, ctx: Context, stream: Stream | None = None) -> None:
        super().__init__()
        self.ctx = ctx
        self.stream = stream or Stream()
        self._loading = False

        self.id_edit = QLineEdit()
        self.id_edit.setFont(QFont("Consolas", 9))
        self.id_edit.setFixedWidth(80)
        self.id_edit.setPlaceholderText("id (hex)")
        self.id_edit.editingFinished.connect(self._on_changed)
        self.ext = QCheckBox("29-bit")
        self.ext.setToolTip("The same number with 29-bit addressing is a different id")
        self.ext.toggled.connect(lambda _on: self._on_changed())
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("name (optional)")
        self.name_edit.setFixedWidth(140)
        self.name_edit.editingFinished.connect(self._on_changed)
        self.skip = QSpinBox()
        self.skip.setRange(0, 63)
        self.skip.setToolTip(SKIP_TIP)
        self.skip.valueChanged.connect(lambda _v: self._on_changed())

        clear = QPushButton("Clear")
        clear.setToolTip("Throw away the text so far.  The id goes on being read.")
        clear.clicked.connect(lambda: self.text.clear())

        bar = QHBoxLayout()
        bar.addWidget(QLabel("Id"))
        bar.addWidget(self.id_edit)
        bar.addWidget(self.ext)
        bar.addWidget(self.name_edit)
        bar.addWidget(QLabel("Skip"))
        bar.addWidget(self.skip)
        bar.addStretch()
        bar.addWidget(clear)

        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setFont(QFont("Consolas", 9))
        self.text.setMaximumBlockCount(MAX_LINES)
        self.text.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.text.setPlaceholderText(NOTHING_YET)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.addLayout(bar)
        layout.addWidget(self.text, 1)

        self._show_stream()
        channels.frames.connect(self.on_frames)

    # --- the id it is reading ---------------------------------------------------------
    def _show_stream(self) -> None:
        self._loading = True
        self.id_edit.setText(f"{self.stream.can_id:X}" if self.stream.chosen else "")
        self.ext.setChecked(self.stream.extended)
        self.name_edit.setText(self.stream.name)
        self.skip.setValue(self.stream.skip)
        self._loading = False

    def _on_changed(self) -> None:
        if self._loading:
            return
        typed = self.id_edit.text().strip().removeprefix("0x").removeprefix("0X")
        can_id = 0
        if typed:
            try:
                can_id = int(typed, 16)
            except ValueError:
                self.ctx.warn(f"ASCII Log: {typed!r} is not a hex id")
                self._show_stream()  # put back what it was actually reading
                return
        was, self.stream = (
            self.stream,
            Stream(
                can_id=can_id,
                extended=self.ext.isChecked(),
                name=self.name_edit.text().strip(),
                skip=self.skip.value(),
            ),
        )
        if self.stream != was:
            self.changed.emit(self.stream)

    # --- what arrives -------------------------------------------------------------------
    @Slot(list)
    def on_frames(self, frames: list[Frame]) -> None:
        if not self.stream.chosen:
            return
        for frame in frames:
            if self.stream.matches(frame):
                self.feed(frame.data)

    def feed(self, data: bytes) -> None:
        """Add a frame's worth of characters.

        Inserted at the end rather than appended as a paragraph: the text
        arrives a few characters at a time and the newlines are in it, so
        appending would put every eight characters on a line of its own.
        """
        text = decode(data, self.stream.skip)
        if not text:
            return
        bar = self.text.verticalScrollBar()
        at_end = bar.value() == bar.maximum()
        self.text.moveCursor(QTextCursor.End)
        self.text.insertPlainText(text)
        # Only follow if it was already at the bottom, so scrolling back to
        # read something is not undone by the next frame.
        if at_end:
            bar.setValue(bar.maximum())
