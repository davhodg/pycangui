"""Text that a device prints onto the bus.

Some devices use a CAN id as a console: ASCII in the data bytes of an ordinary
frame, a few characters at a time, printf fashion.

CANopen has an object for exactly this -- CiA 301 gives every node 0x1026,
"OS prompt", whose StdOut and StdErr sub-indices are single bytes and are PDO
mappable, so a device maps StdOut into a TPDO and its output comes out on that
PDO's COB-ID.  What the standard does *not* do is say which COB-ID that ends
up being: it is whichever PDO the maker chose, so there is no id to decode by
default.  Plenty of devices that are not CANopen at all do the same thing with
an id of their own choosing.

So this decodes whatever ids it is pointed at, several at once, each with its
own tab -- and any one of them can be given a window of its own, which is the
useful arrangement when the point of the exercise is to watch a device talk
while doing something else with pycangui.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from pycangui.core.bus import Frame
from pycangui.core.channels import Channels
from pycangui.core.context import Context
from pycangui.ui.detached import DetachedPane

#: How many lines a stream keeps.  A device printing steadily will run to
#: megabytes over an afternoon, and none of it is worth the memory.
MAX_LINES = 5000


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

    can_id: int
    extended: bool = False
    name: str = ""
    skip: int = 0

    @property
    def key(self) -> str:
        """Identity for the tab.  11-bit 0x123 and 29-bit 0x123 are not the same id."""
        return f"{self.can_id:X}{'x' if self.extended else ''}"

    @property
    def title(self) -> str:
        number = f"{self.can_id:08X}" if self.extended else f"{self.can_id:03X}"
        return f"{number}  {self.name}" if self.name else number

    def matches(self, frame: Frame) -> bool:
        return frame.can_id == self.can_id and frame.extended == self.extended and not frame.error

    def to_dict(self) -> dict:
        return {"id": self.can_id, "extended": self.extended, "name": self.name, "skip": self.skip}

    @classmethod
    def from_dict(cls, d: dict) -> Stream:
        return cls(
            can_id=int(d.get("id", 0)),
            extended=bool(d.get("extended", False)),
            name=str(d.get("name", "")),
            skip=int(d.get("skip", 0)),
        )


class StreamText(QPlainTextEdit):
    """The text of one id, appended to as frames arrive."""

    def __init__(self, stream: Stream) -> None:
        super().__init__()
        self.stream = stream
        self.setReadOnly(True)
        self.setFont(QFont("Consolas", 9))
        self.setMaximumBlockCount(MAX_LINES)
        self.setLineWrapMode(QPlainTextEdit.NoWrap)

    def feed(self, data: bytes) -> None:
        """Add a frame's worth of characters.

        Inserted at the end rather than appended as a paragraph: the text
        arrives a few characters at a time and the newlines are in it, so
        appending would put every eight characters on a line of their own.
        """
        text = decode(data, self.stream.skip)
        if not text:
            return
        at_end = self.verticalScrollBar().value() == self.verticalScrollBar().maximum()
        self.moveCursor(QTextCursor.End)
        self.insertPlainText(text)
        # Only follow if it was already at the bottom, so scrolling back to
        # read something is not undone by the next frame.
        if at_end:
            self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())


class AsciiView(QWidget):
    def __init__(self, channels: Channels, ctx: Context) -> None:
        super().__init__()
        self.ctx = ctx
        self._streams: list[Stream] = []
        self._texts: dict[str, StreamText] = {}
        self._windows: dict[str, DetachedPane] = {}

        self.id_edit = QLineEdit()
        self.id_edit.setFont(QFont("Consolas", 9))
        self.id_edit.setFixedWidth(80)
        self.id_edit.setPlaceholderText("id (hex)")
        self.id_edit.returnPressed.connect(self._add_from_controls)
        self.ext = QCheckBox("29-bit")
        self.ext.setToolTip("The same number with 29-bit addressing is a different id")
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("name (optional)")
        self.name_edit.setFixedWidth(140)
        self.name_edit.returnPressed.connect(self._add_from_controls)
        self.skip = QSpinBox()
        self.skip.setRange(0, 63)
        self.skip.setToolTip(
            "Bytes to ignore at the start of each frame.  Devices often put a\n"
            "length or a sequence number there; a stream of dots at the start\n"
            "of every eight characters is what that looks like."
        )
        add = QPushButton("Add")
        add.setToolTip("Watch this id and read its data as text")
        add.clicked.connect(self._add_from_controls)
        clear = QPushButton("Clear")
        clear.clicked.connect(self._clear_current)
        self.pop_out = QPushButton("Own window")
        self.pop_out.setToolTip(
            "Put this stream in a window of its own, with a taskbar entry, so\n"
            "it can be watched on another display while pycangui is used for\n"
            "something else.  Closing the window brings it back as a tab."
        )
        self.pop_out.clicked.connect(self._pop_out_current)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("Id"))
        bar.addWidget(self.id_edit)
        bar.addWidget(self.ext)
        bar.addWidget(self.name_edit)
        bar.addWidget(QLabel("Skip"))
        bar.addWidget(self.skip)
        bar.addWidget(add)
        bar.addStretch()
        bar.addWidget(clear)
        bar.addWidget(self.pop_out)

        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setDocumentMode(True)
        self.tabs.tabCloseRequested.connect(self._close_tab)
        self.empty = QLabel(
            "No ids yet.  Type one above and press Add.\n\n"
            "A CANopen device that maps object 0x1026 (OS prompt) into a TPDO\n"
            "sends its output on that PDO's COB-ID."
        )
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setEnabled(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.addLayout(bar)
        layout.addWidget(self.tabs, 1)
        layout.addWidget(self.empty, 1)

        channels.frames.connect(self._on_frames)
        self._load()
        self._sync_controls()

    # --- streams ----------------------------------------------------------------------
    def streams(self) -> list[Stream]:
        return list(self._streams)

    def add_stream(self, stream: Stream) -> bool:
        """Watch one id.  False if it was already being watched."""
        if any(s.key == stream.key for s in self._streams):
            return False
        self._streams.append(stream)
        text = StreamText(stream)
        self._texts[stream.key] = text
        index = self.tabs.addTab(text, stream.title)
        self.tabs.setTabToolTip(index, self._describe(stream))
        self.tabs.setCurrentIndex(index)
        self._save()
        self._sync_controls()
        return True

    def remove_stream(self, key: str) -> None:
        window = self._windows.pop(key, None)
        if window is not None:
            window.blockSignals(True)  # it is going for good, not coming back
            window.close()
        text = self._texts.pop(key, None)
        if text is not None:
            index = self.tabs.indexOf(text)
            if index >= 0:
                self.tabs.removeTab(index)
            text.deleteLater()
        self._streams = [s for s in self._streams if s.key != key]
        self._save()
        self._sync_controls()

    @staticmethod
    def _describe(stream: Stream) -> str:
        bits = "29-bit" if stream.extended else "11-bit"
        skipped = f", skipping {stream.skip} byte(s)" if stream.skip else ""
        return f"{stream.title} ({bits}{skipped})"

    def _add_from_controls(self) -> None:
        text = self.id_edit.text().strip().replace("0x", "")
        if not text:
            return
        try:
            can_id = int(text, 16)
        except ValueError:
            self.ctx.warn(f"ASCII: {text!r} is not a hex id")
            return
        stream = Stream(
            can_id=can_id,
            extended=self.ext.isChecked(),
            name=self.name_edit.text().strip(),
            skip=self.skip.value(),
        )
        if not self.add_stream(stream):
            self.ctx.warn(f"ASCII: {stream.title} is already being read")
            return
        self.id_edit.clear()
        self.name_edit.clear()

    # --- what arrives -------------------------------------------------------------------
    @Slot(list)
    def _on_frames(self, frames: list[Frame]) -> None:
        if not self._streams:
            return
        for frame in frames:
            for stream in self._streams:
                if stream.matches(frame):
                    self._texts[stream.key].feed(frame.data)

    # --- the tabs -----------------------------------------------------------------------
    def _current_key(self) -> str:
        widget = self.tabs.currentWidget()
        return widget.stream.key if isinstance(widget, StreamText) else ""

    def _close_tab(self, index: int) -> None:
        widget = self.tabs.widget(index)
        if isinstance(widget, StreamText):
            self.remove_stream(widget.stream.key)

    def _clear_current(self) -> None:
        widget = self.tabs.currentWidget()
        if isinstance(widget, StreamText):
            widget.clear()

    def _pop_out_current(self) -> None:
        """Give the current stream a window with no parent, and a taskbar entry."""
        key = self._current_key()
        text = self._texts.get(key)
        if not key or text is None or key in self._windows:
            return
        index = self.tabs.indexOf(text)
        if index >= 0:
            self.tabs.removeTab(index)  # removeTab does not delete the widget
        stream = next(s for s in self._streams if s.key == key)
        window = DetachedPane(key, f"{stream.title} (ASCII)", text)
        window.closed.connect(self._take_back)
        self._windows[key] = window
        window.show()
        self._sync_controls()

    @Slot(str)
    def _take_back(self, key: str) -> None:
        """Closing the window puts the stream back as a tab, still reading."""
        window = self._windows.pop(key, None)
        if window is None:
            return
        widget = window.release()
        if isinstance(widget, StreamText):
            stream = widget.stream
            index = self.tabs.addTab(widget, stream.title)
            self.tabs.setTabToolTip(index, self._describe(stream))
            widget.show()  # release() reparented it, which hides it
            self.tabs.setCurrentIndex(index)
        self._sync_controls()

    def _sync_controls(self) -> None:
        has_tab = isinstance(self.tabs.currentWidget(), StreamText)
        self.pop_out.setEnabled(has_tab and self._current_key() not in self._windows)
        self.tabs.setVisible(self.tabs.count() > 0)
        self.empty.setVisible(self.tabs.count() == 0 and not self._windows)

    # --- between runs ---------------------------------------------------------------------
    def _save(self) -> None:
        self.ctx.settings.set("ascii.streams", [s.to_dict() for s in self._streams])

    def _load(self) -> None:
        for saved in self.ctx.settings.get("ascii.streams", []):
            try:
                self.add_stream(Stream.from_dict(saved))
            except (TypeError, ValueError):
                continue  # a hand-edited settings.json

    def shutdown(self) -> None:
        """Close any windows that are out, so none are left behind."""
        for window in list(self._windows.values()):
            window.blockSignals(True)
            window.close()
        self._windows.clear()
