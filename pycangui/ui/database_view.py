# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""The Database pane: what is in the CAN databases that are loaded.

A database was something pycangui used and never showed. A message could be
seen once it arrived, or once it had been added to CAN Transmit, and its
signals' scaling not at all -- so the question "what does this file say
byte 3 is?" was answered in another program.

It shows the databases pycangui has loaded and no others. A file open here
alone would be a second state to keep apart from the first: its messages
could not be plotted, sent or given a live value, and two lists of what is
loaded is one too many. *Load...* is File > Load DBC..., from here.

Reading only. Nothing in this pane changes a database or a file.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPalette, QPen, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QToolTip,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pycangui.core.dbc_edit import (
    ERROR,
    DatabaseEdit,
    EditError,
    RemoveMessage,
    RemoveSignal,
)
from pycangui.core.named_values import shown
from pycangui.ui import folders, fonts, messages
from pycangui.ui.column_widths import ColumnWidths
from pycangui.ui.database_edit import (
    AttributesDialog,
    DefinitionsDialog,
    MessageDialog,
    NodesDialog,
    SignalDialog,
    SignalGroupsDialog,
    ValueTablesDialog,
    focus_first,
    framed,
)

if TYPE_CHECKING:
    from cantools.database import Message
    from cantools.database import Signal as DbSignal

    from pycangui.core.context import Context
    from pycangui.core.dbc import DbcDecoder
    from pycangui.core.signals import SignalHub

#: The list of messages: its columns.
ID, NAME, LENGTH, CYCLE = range(4)
#: The list of the chosen message's signals: its columns.
SIGNAL, BITS, SCALE, RANGE, NOW = range(5)
#: On a row of the message list: the message, and for a signal's row its name.
ROLE_MESSAGE = Qt.UserRole
ROLE_SIGNAL = Qt.UserRole + 1
ROLE_PATH = Qt.UserRole + 2

#: How often the Now column is brought up to date while the pane is showing.
NOW_MS = 250
#: One for each signal of a message in turn, and round again past the end.
#: Mid tones, so that dark text reads on every one of them in either theme.
#: The colour is never the only thing that says which signal a bit belongs
#: to: its name is written across the bits, and each has a tooltip.
COLOURS = (
    "#8fd3bf",
    "#c9c3f2",
    "#f2c1ae",
    "#a9cdf0",
    "#e8d28a",
    "#f0b3cb",
    "#b9dd94",
    "#d3d1c7",
)
INK = QColor("#1b1b1b")
#: The lines between the bits of a signal: there, and not in the way of its name.
INSIDE = QColor(27, 27, 27, 50)
#: A bit two signals both claim: said with an outline, since a third colour
#: would be one more thing to tell apart.
OVERLAP = QColor("#c0392b")

FIND_TIP = (
    "Show only the messages with this in their name or identifier, or with\n"
    "a signal that has it in its name. A message found by one of its signals\n"
    "is opened to show it."
)
FILE_TIP = "Show the messages of one database only, of the ones that are loaded."
LOAD_TIP = "Load another database: the same as File > Load DBC..."
TRANSMIT_TIP = "Add this message to CAN Transmit, with a box for each of its signals."
TRACE_TIP = "Show only this message in the CAN Trace, by its name in the filter box."
PLOT_TIP = (
    "Plot this signal in Signals and Plot. For a signal that has arrived:\n"
    "one that has not has nothing to plot yet."
)
SHADOWED_TIP = (
    "Not the one in use. {other} was loaded before this database and has a\n"
    "message of this name or on this identifier, and the first one loaded is\n"
    "the one frames are decoded with."
)
MUX_TIP = (
    "Which value of the multiplexer to draw the message for: the signals that\n"
    "come with it, and those always there. The others are greyed in the list.\n"
    "Choosing one of them chooses a value it comes with."
)
EDITING_TIP = (
    "Changes made here, by double-clicking a message or a signal, or adding\n"
    "and removing them with a right-click. Nothing is decoded or sent\n"
    "differently, and the file is not touched, until Save as... writes a new\n"
    "file and it is loaded in place of this one."
)
NOT_DBC = (
    "{name} is a {kind} file. Only DBC files can be changed here: pycangui reads\n"
    "the others, and writes DBC only."
)
ONE_AT_A_TIME = (
    "{name} has changes that are not saved. Save them as a new file, or discard\n"
    "them, before changing another database."
)
CHANGED_TIP = "Changed, and not saved yet."
NOTHING_LOADED = "No database is loaded. Load... to choose one."
NOTHING_CHOSEN = "Choose a message on the left."


def id_text(msg: Message) -> str:
    """An identifier as the rest of pycangui writes one: eight digits if 29-bit."""
    return f"{msg.frame_id:08X}" if msg.is_extended_frame else f"{msg.frame_id:03X}"


def bits_of(signal: DbSignal) -> list[tuple[int, int]]:
    """Where a signal's bits are, as (byte, bit in the byte), first bit first.

    An Intel signal starts at its least significant bit and runs up: bit 8
    is bit 0 of byte 1. A Motorola one starts at its most significant bit
    and runs down the byte, then on from bit 7 of the next -- which is why
    one drawn on a grid does not look like a run of numbers.
    """
    out = []
    at = signal.start
    for _ in range(signal.length):
        out.append((at // 8, at % 8))
        if signal.byte_order == "little_endian":
            at += 1
        elif at % 8 == 0:
            at += 15
        else:
            at -= 1
    return out


def _plain(number: float) -> str:
    """A number of the database's, written out: 4294967.295, not 4.29497e+06."""
    return f"{number:.10g}"


def scale_text(signal: DbSignal) -> str:
    """Factor and offset, as little as says it: nothing to say for 1 and 0."""
    scale, offset = signal.scale, signal.offset
    if scale == 1 and offset == 0:
        return "named" if signal.choices else ""
    return _plain(scale) if offset == 0 else f"{_plain(scale)}, {offset:+.10g}"


def range_text(signal: DbSignal) -> str:
    unit = f" {signal.unit}" if signal.unit else ""
    if signal.minimum is None and signal.maximum is None:
        if signal.choices:
            return f"{len(signal.choices)} values"
        return unit.strip()
    low = "" if signal.minimum is None else _plain(signal.minimum)
    high = "" if signal.maximum is None else _plain(signal.maximum)
    return f"{low} to {high}{unit}"


def value_text(value: float | None, signal: DbSignal) -> str:
    """A live value as Signals and Plot writes it, with its unit after it."""
    if value is None:
        return ""
    choices = {number: str(name) for number, name in (signal.choices or {}).items()}
    if (text := shown(value, choices)) is not None:
        return text
    number = f"{int(value)}" if float(value).is_integer() else f"{value:.6g}"
    return f"{number} {signal.unit}" if signal.unit else number


def multiplexer(msg: Message | None) -> DbSignal | None:
    """The signal that says which of the others are in a frame: the one at the
    top, where a multiplexed signal is itself a multiplexer for others."""
    if msg is None:
        return None
    return next((s for s in msg.signals if s.is_multiplexer and not s.multiplexer_ids), None)


def multiplexer_values(msg: Message, top: DbSignal) -> list[int]:
    """The values of the multiplexer that bring signals of their own."""
    found: set[int] = set()
    for signal in msg.signals:
        if signal.multiplexer_signal == top.name:
            found.update(signal.multiplexer_ids or ())
    return sorted(found)


def present(msg: Message, value: int | None) -> set[str]:
    """The signals in a frame whose multiplexer has this value: those that are
    always there, and those that come with it. A multiplexer under the top one
    is shown with all of its own, which this does not choose between."""
    top = multiplexer(msg)
    by_name = {s.name: s for s in msg.signals}

    def there(signal) -> bool:
        if not signal.multiplexer_ids:
            return True
        if top is not None and signal.multiplexer_signal == top.name:
            return value in signal.multiplexer_ids
        parent = by_name.get(signal.multiplexer_signal)
        return parent is None or there(parent)

    return {s.name for s in msg.signals if there(s)}


def about(signal: DbSignal) -> str:
    """One line on a signal: what kind of number, which way round, and its comment."""
    kind = "float" if signal.is_float else ("signed" if signal.is_signed else "unsigned")
    order = "Intel" if signal.byte_order == "little_endian" else "Motorola"
    parts = [f"{signal.name}: {kind}, {order} byte order"]
    if signal.is_multiplexer:
        parts.append("chooses which of the other signals are in the frame")
    elif signal.multiplexer_ids:
        which = ", ".join(str(number) for number in signal.multiplexer_ids)
        parts.append(f"there when {signal.multiplexer_signal} is {which}")
    text = ", ".join(parts) + "."
    if signal.comment:
        text += f" {signal.comment}"
    if signal.choices:
        names = ", ".join(f"{name} ({number})" for number, name in signal.choices.items())
        text += f" Values: {names}."
    return text


class Layout(QWidget):
    """A message's bytes, a row each, with every bit marked for its signal.

    Bit 7 at the left, as a byte is written. What it is for is what a list
    of start bits does not show: a gap nobody uses, two signals on the same
    bit, and where a Motorola signal really is.
    """

    chosen = Signal(str)  # the name of the signal a bit that was clicked belongs to
    edit_wanted = Signal(str)  # the same, double-clicked: to be changed

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setAccessibleName("Bit layout")
        self._length = 0
        #: (byte, bit) -> the names of the signals that have it.
        self._owners: dict[tuple[int, int], list[str]] = {}
        self._colours: dict[str, QColor] = {}
        self._selected = ""

    def show_message(
        self, msg: Message | None, colours: dict[str, QColor], shown: set[str] | None = None
    ) -> None:
        """Draw a message's signals: all of them, or the ones in ``shown`` --
        for a multiplexed message, those in a frame with one multiplexer value."""
        self._length = 0 if msg is None else msg.length
        self._owners = {}
        self._colours = colours
        self._selected = ""
        for signal in [] if msg is None else msg.signals:
            if shown is not None and signal.name not in shown:
                continue
            for place in bits_of(signal):
                self._owners.setdefault(place, []).append(signal.name)
        self.setAccessibleDescription(self.described())
        self.updateGeometry()
        self.update()

    def select(self, name: str) -> None:
        self._selected = name
        self.update()

    def owners(self, byte: int, bit: int) -> list[str]:
        return list(self._owners.get((byte, bit), []))

    def overlaps(self) -> list[tuple[int, int]]:
        """The bits more than one of the signals drawn has. A multiplexed
        message drawn for one multiplexer value has none that are not a fault."""
        return sorted(place for place, names in self._owners.items() if len(names) > 1)

    def described(self) -> str:
        """The picture in words, for something that reads the screen aloud."""
        used = len(self._owners)
        return f"{self._length} bytes, {used} of {self._length * 8} bits used by signals."

    # --- geometry ---------------------------------------------------------------------
    def row_height(self) -> int:
        return fonts.text_height() + 6

    def _row(self) -> int:
        return self.row_height()

    def _label(self) -> int:
        return QFontMetrics(self.font()).horizontalAdvance("Byte 63") + 10

    def sizeHint(self) -> QSize:
        return QSize(self._label() + 8 * 28, max(1, self._length) * self._row() + 2)

    def minimumSizeHint(self) -> QSize:
        return QSize(self._label() + 8 * 14, self._row())

    def _cell(self, byte: int, bit: int) -> QRect:
        label = self._label()
        width = (self.width() - label - 1) / 8
        left = label + round((7 - bit) * width)
        right = label + round((8 - bit) * width)
        return QRect(left, byte * self._row(), right - left, self._row())

    def _place_at(self, pos) -> tuple[int, int] | None:
        byte = pos.y() // self._row()
        if pos.x() < self._label() or not 0 <= byte < self._length:
            return None
        width = (self.width() - self._label() - 1) / 8
        return byte, 7 - min(7, int((pos.x() - self._label()) / width))

    # --- events -----------------------------------------------------------------------
    def mousePressEvent(self, event) -> None:
        place = self._place_at(event.position().toPoint())
        if place is not None and (names := self.owners(*place)):
            # A bit two signals have gives each in turn.
            after = names.index(self._selected) + 1 if self._selected in names else 0
            self.chosen.emit(names[after % len(names)])

    def mouseDoubleClickEvent(self, event) -> None:
        place = self._place_at(event.position().toPoint())
        if place is not None and (names := self.owners(*place)):
            self.edit_wanted.emit(self._selected if self._selected in names else names[0])

    def mouseMoveEvent(self, event) -> None:
        place = self._place_at(event.position().toPoint())
        if place is None:
            QToolTip.hideText()
            return
        byte, bit = place
        names = self.owners(byte, bit)
        what = " and ".join(names) if names else "not used"
        QToolTip.showText(
            event.globalPosition().toPoint(), f"Byte {byte}, bit {bit} ({byte * 8 + bit}): {what}"
        )

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        palette = self.palette()
        line = palette.color(QPalette.Mid)
        text = palette.color(QPalette.WindowText)
        faint = palette.color(QPalette.PlaceholderText)
        for byte in range(self._length):
            top = byte * self._row()
            painter.setPen(text)
            painter.drawText(
                QRect(0, top, self._label() - 6, self._row()),
                Qt.AlignVCenter | Qt.AlignRight,
                f"Byte {byte}",
            )
            for bit in range(8):
                cell = self._cell(byte, bit)
                names = self.owners(byte, bit)
                if names:
                    painter.fillRect(cell, self._colours.get(names[0], QColor(COLOURS[-1])))
                # Faint inside a signal, where the name is written across them.
                painter.setPen(INSIDE if names else line)
                painter.drawRect(cell)
                if not names:
                    painter.setPen(faint)
                    painter.drawText(cell, Qt.AlignCenter, str(byte * 8 + bit))
            self._name_runs(painter, byte)
        for byte, bit in self._owners:
            names = self._owners[(byte, bit)]
            if len(names) > 1:
                painter.setPen(QPen(OVERLAP, 2, Qt.DashLine))
                painter.drawRect(self._cell(byte, bit).adjusted(1, 1, -1, -1))

    def _name_runs(self, painter: QPainter, byte: int) -> None:
        """A signal's name across each run of its bits in this byte."""
        metrics = QFontMetrics(self.font())
        bit = 7
        while bit >= 0:
            names = self.owners(byte, bit)
            end = bit
            while end > 0 and self.owners(byte, end - 1) == names:
                end -= 1
            if names:
                run = self._cell(byte, bit).united(self._cell(byte, end))
                if self._selected in names:
                    # Round the run, not round each bit: one mark for one signal.
                    painter.setPen(QPen(self.palette().color(QPalette.WindowText), 2))
                    painter.drawRect(run.adjusted(1, 1, -1, -1))
                across = run.adjusted(3, 0, -3, 0)
                name = " + ".join(names)
                painter.setPen(INK)
                painter.drawText(
                    across,
                    Qt.AlignCenter,
                    metrics.elidedText(name, Qt.ElideRight, across.width()),
                )
            bit = end - 1


def _multiplexers(message, signal: str) -> list[str]:
    """The signals of a message that ``signal`` could be sent by: each
    multiplexer but itself and those it chooses, however far down."""
    by_name = {s.name: s for s in message.signals}

    def under(s) -> bool:
        while s is not None and s.multiplexer_signal:
            if s.multiplexer_signal == signal:
                return True
            s = by_name.get(s.multiplexer_signal)
        return False

    return [
        s.name for s in message.signals if s.is_multiplexer and s.name != signal and not under(s)
    ]


def _swatch(colour: QColor, size: int) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(colour)
    return pixmap


class DatabaseView(QWidget):
    """Messages on the left; the chosen one's details, layout and signals on the right."""

    #: Asked of the window, which is what has the other panes.
    load_wanted = Signal()
    transmit_wanted = Signal(str)  # a message's name
    trace_wanted = Signal(str)  # a message's name
    plot_wanted = Signal(str)  # a signal's key in Signals and Plot
    #: A database saved as a new file: the file it came from, the new one,
    #: and its messages renamed, old name -> new.
    saved_as = Signal(str, str, dict)

    def __init__(
        self, dbc: DbcDecoder, hub: SignalHub, ctx: Context, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.dbc = dbc
        self.hub = hub
        self.ctx = ctx
        self._message: Message | None = None
        #: The file of the message shown, for the edits made to it.
        self._path = ""
        #: The database being changed, one at a time, and the file it is of.
        self._edit: DatabaseEdit | None = None

        self.search = QLineEdit()
        self.search.setPlaceholderText("message, identifier or signal...")
        self.search.setClearButtonEnabled(True)
        self.search.setToolTip(FIND_TIP)
        self.search.setAccessibleName("Find")
        self.search.textChanged.connect(self._fill_messages)
        self.file = QComboBox()
        self.file.setToolTip(FILE_TIP)
        self.file.setAccessibleName("File")
        self.file.currentIndexChanged.connect(self._fill_messages)
        self.load_btn = QPushButton("Load...")
        self.load_btn.setToolTip(LOAD_TIP)
        self.load_btn.clicked.connect(self.load_wanted)
        bar = QHBoxLayout()
        bar.addWidget(QLabel("Find"))
        bar.addWidget(self.search, 1)
        bar.addWidget(QLabel("File"))
        bar.addWidget(self.file)
        bar.addWidget(self.load_btn)
        # Only while there are changes: what they are, and what can be done.
        self.edit_bar = QWidget()
        self.edit_bar.setToolTip(EDITING_TIP)
        edits = QHBoxLayout(self.edit_bar)
        edits.setContentsMargins(0, 0, 0, 0)
        self.edit_label = QLabel()
        self.undo_btn = QPushButton("Undo")
        self.undo_btn.clicked.connect(self.undo)
        self.save_btn = QPushButton("Save as...")
        self.save_btn.clicked.connect(self.save_as)
        self.discard_btn = QPushButton("Discard changes")
        self.discard_btn.clicked.connect(self.discard)
        edits.addWidget(self.edit_label, 1)
        edits.addWidget(self.undo_btn)
        edits.addWidget(self.save_btn)
        edits.addWidget(self.discard_btn)
        self.edit_bar.hide()

        self.messages = QTreeWidget()
        self.messages.setHeaderLabels(["ID", "Message / signal", "Len", "Cycle"])
        self.messages.setFont(fonts.mono())
        self.messages.setAccessibleName("Messages")
        self.messages.setUniformRowHeights(True)
        self.messages.currentItemChanged.connect(self._on_row_chosen)
        self.messages.itemDoubleClicked.connect(self._on_message_double_clicked)
        self.messages.setContextMenuPolicy(Qt.CustomContextMenu)
        self.messages.customContextMenuRequested.connect(self._messages_menu)
        self._message_widths = ColumnWidths(
            self.messages, ctx.settings, "database.messages", stretch=NAME, last_stretches=False
        )
        self.count = QLabel()
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(self.messages, 1)
        left_layout.addWidget(self.count)

        # --- the chosen message ---
        frame, inside, top = framed("Message")
        self.title = QLabel(NOTHING_CHOSEN)
        self.title.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.source = QLabel()
        top.addWidget(self.title, 1)
        top.addWidget(self.source)
        self.comment = QLabel()
        self.comment.setWordWrap(True)
        self.comment.setTextInteractionFlags(Qt.TextSelectableByMouse)
        inside.addWidget(self.comment)
        self.transmit_btn = QPushButton("Add to Transmit")
        self.transmit_btn.setToolTip(TRANSMIT_TIP)
        self.transmit_btn.clicked.connect(lambda: self.transmit_wanted.emit(self._message.name))
        self.trace_btn = QPushButton("Show in Trace")
        self.trace_btn.setToolTip(TRACE_TIP)
        self.trace_btn.clicked.connect(lambda: self.trace_wanted.emit(self._message.name))
        buttons = QHBoxLayout()
        buttons.addWidget(self.transmit_btn)
        buttons.addWidget(self.trace_btn)
        buttons.addStretch()
        inside.addLayout(buttons)
        message_frame = frame

        frame, inside, top = framed("Layout")
        self.layout_note = QLabel("bit 7 at the left of each byte")
        self.layout_note.setEnabled(False)
        top.addWidget(self.layout_note, 1)
        # Only for a multiplexed message: its signals take turns at the same
        # bits, and drawn all at once they look like a database in error.
        self.mux_label = QLabel()
        self.mux = QComboBox()
        self.mux.setToolTip(MUX_TIP)
        self.mux.setAccessibleName("Multiplexer value")
        self.mux.currentIndexChanged.connect(self._show_multiplexed)
        top.addWidget(self.mux_label)
        top.addWidget(self.mux)
        self.mux_label.hide()
        self.mux.hide()
        self.layout_view = Layout()
        self.layout_view.chosen.connect(self.select_signal)
        self.layout_view.edit_wanted.connect(self.edit_signal)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(self.layout_view)
        self._layout_scroll = scroll
        inside.addWidget(scroll)
        layout_frame = frame

        frame, inside, top = framed("Signals")
        top.addStretch()
        self.plot_btn = QPushButton("Plot")
        self.plot_btn.setToolTip(PLOT_TIP)
        self.plot_btn.clicked.connect(self._plot)
        top.addWidget(self.plot_btn)
        self.signals = QTreeWidget()
        self.signals.setHeaderLabels(["Signal", "Bits", "Scale", "Range", "Now"])
        self.signals.setRootIsDecorated(False)
        self.signals.setFont(fonts.mono())
        self.signals.setAccessibleName("Signals")
        self.signals.setUniformRowHeights(True)
        self.signals.currentItemChanged.connect(self._on_signal_chosen)
        self.signals.itemDoubleClicked.connect(
            lambda item, _column: self.edit_signal(item.data(0, ROLE_SIGNAL))
        )
        self.signals.setContextMenuPolicy(Qt.CustomContextMenu)
        self.signals.customContextMenuRequested.connect(self._signals_menu)
        self._signal_widths = ColumnWidths(self.signals, ctx.settings, "database.signals")
        inside.addWidget(self.signals, 1)
        self.signal_about = QLabel()
        self.signal_about.setWordWrap(True)
        self.signal_about.setTextInteractionFlags(Qt.TextSelectableByMouse)
        inside.addWidget(self.signal_about)
        signals_frame = frame

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(message_frame)
        right_layout.addWidget(layout_frame)
        right_layout.addWidget(signals_frame, 1)

        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.addWidget(left)
        self.splitter.addWidget(right)
        self.splitter.setStretchFactor(0, 2)
        self.splitter.setStretchFactor(1, 3)
        self.splitter.setChildrenCollapsible(False)

        outer = QVBoxLayout(self)
        outer.addLayout(bar)
        outer.addWidget(self.edit_bar)
        outer.addWidget(self.splitter, 1)

        #: Only while showing: a pane nobody is looking at reads no values.
        self._now_timer = QTimer(self, interval=NOW_MS, timeout=self._show_now)
        dbc.listeners.append(self.refresh)
        self.refresh()

    # --- what is loaded -----------------------------------------------------------------
    def refresh(self) -> None:
        """The databases changed: one loaded, or one removed."""
        wanted = self.file.currentData()
        self.file.blockSignals(True)
        self.file.clear()
        paths = list(self.dbc.databases)
        self.file.addItem(f"All ({len(paths)} loaded)", None)
        for path in paths:
            self.file.addItem(Path(path).name, path)
            self.file.setItemData(self.file.count() - 1, path, Qt.ToolTipRole)
        self.file.setCurrentIndex(max(0, self.file.findData(wanted)))
        self.file.blockSignals(False)
        self._fill_messages()

    def _fill_messages(self) -> None:
        chosen = None if self._message is None else self._message.name
        text = self.search.text().strip().lower()
        only = self.file.currentData()
        self.messages.blockSignals(True)
        self.messages.clear()
        total = 0
        again = None
        shadowed = self.dbc.shadowed()
        touched = self._edit.touched() if self._edit is not None else set()
        for path, db in self._databases():
            if only is not None and path != only:
                continue
            for msg in sorted(db.messages, key=lambda m: (m.frame_id, m.name)):
                total += 1
                ident = id_text(msg)
                by_signal = [s.name for s in msg.signals if text and text in s.name.lower()]
                if text and text not in msg.name.lower() and text not in ident.lower():
                    if not by_signal:
                        continue
                cycle = f"{msg.cycle_time} ms" if msg.cycle_time else ""
                item = QTreeWidgetItem([ident, msg.name, str(msg.length), cycle])
                item.setData(0, ROLE_MESSAGE, msg)
                item.setData(0, ROLE_PATH, path)
                item.setToolTip(NAME, path)
                if other := shadowed.get(id(msg)):
                    for column in range(self.messages.columnCount()):
                        item.setForeground(column, self.palette().brush(QPalette.PlaceholderText))
                    item.setToolTip(NAME, SHADOWED_TIP.format(other=Path(other).name))
                if self._is_edited(path) and msg.name in touched:
                    # Said in words as well as by the look: the tooltip, and
                    # the count in the bar above.
                    font = item.font(NAME)
                    font.setItalic(True)
                    for column in range(self.messages.columnCount()):
                        item.setFont(column, font)
                    item.setToolTip(NAME, CHANGED_TIP)
                for signal in msg.signals:
                    child = QTreeWidgetItem(["", signal.name, "", ""])
                    child.setData(0, ROLE_MESSAGE, msg)
                    child.setData(0, ROLE_PATH, path)
                    child.setData(0, ROLE_SIGNAL, signal.name)
                    item.addChild(child)
                self.messages.addTopLevelItem(item)
                item.setExpanded(bool(by_signal))
                if msg.name == chosen and again is None:
                    again = item
        self.messages.blockSignals(False)
        shown_now = self.messages.topLevelItemCount()
        if not self.dbc.databases:
            self.count.setText(NOTHING_LOADED)
        elif shown_now == total:
            self.count.setText(f"{total} message" + ("" if total == 1 else "s"))
        else:
            self.count.setText(f"{shown_now} of {total} messages match")
        self._message_widths.apply()
        if again is not None:
            self.messages.setCurrentItem(again)
        else:
            self._show_message(None)

    # --- the chosen message -------------------------------------------------------------
    def _on_row_chosen(self, item: QTreeWidgetItem | None, _before=None) -> None:
        msg = None if item is None else item.data(0, ROLE_MESSAGE)
        if msg is not self._message:
            self._show_message(msg, "" if item is None else item.data(0, ROLE_PATH))
        self._path = "" if item is None else item.data(0, ROLE_PATH)
        if item is not None and (name := item.data(0, ROLE_SIGNAL)):
            self.select_signal(name)

    def select_message(self, name: str) -> bool:
        """Choose a message by name, as a click on its row would. False if it
        is not in the list as it is filtered now."""
        for row in range(self.messages.topLevelItemCount()):
            item = self.messages.topLevelItem(row)
            if item.data(0, ROLE_MESSAGE).name == name:
                self.messages.setCurrentItem(item)
                return True
        return False

    def message(self) -> Message | None:
        return self._message

    def _colours(self, msg: Message) -> dict[str, QColor]:
        return {s.name: QColor(COLOURS[at % len(COLOURS)]) for at, s in enumerate(msg.signals)}

    def _show_message(self, msg: Message | None, path: str = "") -> None:
        self._message = msg
        for button in (self.transmit_btn, self.trace_btn):
            button.setEnabled(msg is not None)
        self.signals.blockSignals(True)
        self.signals.clear()
        if msg is None:
            self.title.setText(NOTHING_CHOSEN if self.dbc.databases else NOTHING_LOADED)
            self.source.setText("")
            self.comment.setText("")
            self.layout_view.show_message(None, {})
            self._fill_multiplexer(None)
            self._size_layout(0)
            self.signals.blockSignals(False)
            self._on_signal_chosen(None)
            self._now_timer.stop()
            return
        parts = [f"{msg.name}  {id_text(msg)}", f"{msg.length} bytes"]
        if msg.cycle_time:
            parts.append(f"{msg.cycle_time} ms")
        if msg.senders:
            parts.append("from " + ", ".join(msg.senders))
        self.title.setText(", ".join(parts))
        self.source.setText(Path(path).name)
        self.source.setToolTip(path)
        self.comment.setText(msg.comment or "")
        self.comment.setVisible(bool(msg.comment))
        colours = self._colours(msg)
        self._fill_multiplexer(msg)
        self.layout_view.show_message(msg, colours, self._present())
        self._size_layout(msg.length)
        size = fonts.text_height() - 4
        for signal in msg.signals:
            item = QTreeWidgetItem(
                [
                    signal.name,
                    f"{signal.start}:{signal.length}",
                    scale_text(signal),
                    range_text(signal),
                    "",
                ]
            )
            item.setIcon(SIGNAL, _swatch(colours[signal.name], size))
            item.setData(0, ROLE_SIGNAL, signal.name)
            item.setToolTip(SIGNAL, about(signal))
            self.signals.addTopLevelItem(item)
        self.signals.blockSignals(False)
        self._grey_absent()
        self._signal_widths.apply()
        self._on_signal_chosen(None)
        self._show_now()
        if self.isVisible():
            self._now_timer.start()

    # --- editing ----------------------------------------------------------------------------
    def _databases(self):
        """Each database loaded, with the copy being changed in place of its own."""
        for path, db in self.dbc.databases.items():
            yield path, (self._edit.database if self._is_edited(path) else db)

    def _is_edited(self, path: str) -> bool:
        return self._edit is not None and str(self._edit.path) == str(path)

    def editing(self) -> DatabaseEdit | None:
        return self._edit

    def _session(self, path: str) -> DatabaseEdit | None:
        """The edit of this file: the one there is, a new one, or None -- said why."""
        if self._is_edited(path):
            return self._edit
        name = Path(path).name
        if Path(path).suffix.lower() != ".dbc":
            kind = Path(path).suffix.lstrip(".").upper() or "database"
            messages.information(self, "Not changed", NOT_DBC.format(name=name, kind=kind))
            return None
        if self._edit is not None and self._edit.changed:
            other = self._edit.path.name
            messages.information(self, "Not changed", ONE_AT_A_TIME.format(name=other))
            return None
        self._edit = DatabaseEdit(path)
        return self._edit

    def _make(self, changes, select: str = "", signal: str = "") -> bool:
        """Make a dialog's changes, as one; refused, it is said and nothing is."""
        if not changes:
            return True
        try:
            self._edit.apply_all(changes)
        except EditError as exc:
            messages.warning(self, "Not changed", str(exc))
            return False
        self._changed(select, signal)
        return True

    def _changed(self, select: str = "", signal: str = "") -> None:
        """Show the copy as it now is, with what was being looked at still chosen."""
        wanted = select or (self._message.name if self._message is not None else "")
        self._message = None
        self._fill_messages()
        if wanted:
            self.select_message(wanted)
        if signal:
            self.select_signal(signal)
        self._show_edit_bar()

    def _show_edit_bar(self) -> None:
        edit = self._edit
        count = len(edit.changes) if edit is not None else 0
        self.edit_bar.setVisible(bool(count))
        if count:
            self.edit_label.setText(
                f"{count} change" + ("" if count == 1 else "s") + f" to {edit.path.name}, not saved"
            )

    def _on_message_double_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        if name := item.data(0, ROLE_SIGNAL):
            self.edit_signal(name)
        else:
            self.edit_message()

    def edit_message(self) -> None:
        """The chosen message, in its dialog."""
        msg = self._message
        if msg is None or self._session(self._path) is None:
            return
        dialog = MessageDialog(
            self,
            self._edit.message(msg.name),
            nodes=self._node_names(),
            attributes=self._edit.attributes(("message", msg.name)),
        )
        focus_first(dialog)
        while dialog.exec() == MessageDialog.Accepted:
            name = dialog.name.text().strip()
            if self._make(dialog.changes(), select=name):
                return

    def add_message(self, path: str = "") -> None:
        path = path or self._path or next(iter(self.dbc.databases), "")
        if not path or self._session(path) is None:
            return
        dialog = MessageDialog(
            self, nodes=self._node_names(), attributes=self._edit.attributes(("message",))
        )
        focus_first(dialog)
        while dialog.exec() == MessageDialog.Accepted:
            if self._make(dialog.changes(), select=dialog.name.text().strip()):
                return

    def remove_message(self) -> None:
        msg = self._message
        if msg is None or self._session(self._path) is None:
            return
        self._make([RemoveMessage(msg.name)])

    def edit_signal(self, name: str) -> None:
        msg = self._message
        if msg is None or not name or self._session(self._path) is None:
            return
        current = self._edit.message(msg.name)
        signal = next((s for s in current.signals if s.name == name), None)
        if signal is None:
            return
        dialog = SignalDialog(
            self,
            msg.name,
            signal,
            multiplexers=_multiplexers(current, signal.name),
            tables=self._edit.value_tables(),
            nodes=self._node_names(),
            attributes=self._edit.attributes(("signal", msg.name, signal.name)),
        )
        focus_first(dialog)
        while dialog.exec() == SignalDialog.Accepted:
            if self._make(dialog.changes(), signal=dialog.name.text().strip()):
                return

    def add_signal(self) -> None:
        msg = self._message
        if msg is None or self._session(self._path) is None:
            return
        dialog = SignalDialog(
            self,
            msg.name,
            multiplexers=_multiplexers(self._edit.message(msg.name), ""),
            tables=self._edit.value_tables(),
            nodes=self._node_names(),
            attributes=self._edit.attributes(("signal",)),
        )
        focus_first(dialog)
        while dialog.exec() == SignalDialog.Accepted:
            if self._make(dialog.changes(), signal=dialog.name.text().strip()):
                return

    def _node_names(self) -> list[str]:
        return [node.name for node in self._edit.nodes()]

    def _database_session(self, path: str = "") -> DatabaseEdit | None:
        """The edit of the database being looked at, for what is the whole
        database's: its value tables, nodes and attributes."""
        path = path or self._path or next(iter(self.dbc.databases), "")
        return self._session(path) if path else None

    def _run(self, dialog, **chosen) -> None:
        """A dialog, again after a change it asked for is refused, until it is
        made or cancelled."""
        while dialog.exec() == QDialog.Accepted:
            if self._make(dialog.changes(), **chosen):
                return

    def edit_value_tables(self, path: str = "") -> None:
        """The database's value tables, in their dialog."""
        if (edit := self._database_session(path)) is not None:
            self._run(ValueTablesDialog(self, edit.value_tables()))

    def edit_nodes(self, path: str = "") -> None:
        """The database's nodes: names, comments and attributes."""
        if (edit := self._database_session(path)) is None:
            return
        nodes = edit.nodes()
        dialog = NodesDialog(
            self,
            [(node.name, node.comment or "") for node in nodes],
            {node.name: edit.attributes(("node", node.name)) for node in nodes},
            edit.attributes(("node",)),
        )
        self._run(dialog)

    def edit_definitions(self, path: str = "") -> None:
        """What attributes there are."""
        if (edit := self._database_session(path)) is None:
            return
        specs = edit.definitions()
        self._run(DefinitionsDialog(self, specs, {s.name: edit.uses(s.name) for s in specs}))

    def edit_database_attributes(self, path: str = "") -> None:
        if (edit := self._database_session(path)) is not None:
            self._run(AttributesDialog(self, edit.attributes(())))

    def edit_signal_groups(self) -> None:
        """The chosen message's signal groups."""
        msg = self._message
        if msg is None or self._session(self._path) is None:
            return
        current = self._edit.message(msg.name)
        groups = [
            (g.name, g.repetitions, list(g.signal_names)) for g in current.signal_groups or []
        ]
        dialog = SignalGroupsDialog(self, msg.name, [s.name for s in current.signals], groups)
        self._run(dialog, select=msg.name)

    def remove_signal(self) -> None:
        msg, name = self._message, self.signal_chosen()
        if msg is None or not name or self._session(self._path) is None:
            return
        self._make([RemoveSignal(msg.name, name)])

    def _messages_menu(self, at) -> None:
        item = self.messages.itemAt(at)
        if item is not None:
            self.messages.setCurrentItem(item)
        menu = QMenu(self.messages)
        on_message = item is not None
        on_signal = on_message and bool(item.data(0, ROLE_SIGNAL))
        menu.addAction("Add message...", lambda: self.add_message(self._path))
        menu.addAction("Change message...", self.edit_message).setEnabled(on_message)
        menu.addAction("Remove message", self.remove_message).setEnabled(on_message)
        menu.addSeparator()
        menu.addAction("Add signal...", self.add_signal).setEnabled(on_message)
        menu.addAction(
            "Change signal...", lambda: self.edit_signal(item.data(0, ROLE_SIGNAL))
        ).setEnabled(on_signal)
        menu.addAction("Remove signal", self.remove_signal).setEnabled(on_signal)
        menu.addAction("Signal groups...", self.edit_signal_groups).setEnabled(on_message)
        menu.addSeparator()
        # The whole database's, not the message's.
        menu.addAction("Nodes...", lambda: self.edit_nodes(self._path))
        menu.addAction("Value tables...", lambda: self.edit_value_tables(self._path))
        menu.addAction("Attribute definitions...", lambda: self.edit_definitions(self._path))
        menu.addAction("Database attributes...", lambda: self.edit_database_attributes(self._path))
        menu.exec(self.messages.viewport().mapToGlobal(at))

    def _signals_menu(self, at) -> None:
        item = self.signals.itemAt(at)
        if item is not None:
            self.signals.setCurrentItem(item)
        menu = QMenu(self.signals)
        menu.addAction("Add signal...", self.add_signal).setEnabled(self._message is not None)
        menu.addAction(
            "Change signal...", lambda: self.edit_signal(self.signal_chosen())
        ).setEnabled(item is not None)
        menu.addAction("Remove signal", self.remove_signal).setEnabled(item is not None)
        menu.exec(self.signals.viewport().mapToGlobal(at))

    def undo(self) -> None:
        if self._edit is not None and self._edit.undo() is not None:
            self._changed()

    def discard(self) -> None:
        edit = self._edit
        if edit is None or not edit.changed:
            return
        count = len(edit.changes)
        answer = messages.question(
            self,
            "Discard changes",
            f"Discard the {count} change" + ("" if count == 1 else "s") + f" to {edit.path.name}?",
            QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if answer == QMessageBox.Discard:
            self._edit = None
            self._changed()

    def may_discard(self, path: str | None = None) -> bool:
        """Whether changes not saved can go: asked before the window closes,
        before another workspace opens, and before the database being changed
        is removed -- ``path``, where only that one matters.

        Save writes them as a new file first; Cancel is Cancel for whatever
        was going to happen.
        """
        edit = self._edit
        if edit is None or not edit.changed:
            return True
        if path is not None and not self._is_edited(path):
            return True
        count = len(edit.changes)
        answer = messages.question(
            self,
            "Changes not saved",
            f"{count} change" + ("" if count == 1 else "s") + f" to {edit.path.name} "
            "have not been saved. Save them as a new file first?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Save,
        )
        if answer == QMessageBox.Cancel:
            return False
        if answer == QMessageBox.Save:
            return self.save_as(offer=False)
        self._edit = None
        self._changed()
        return True

    def save_as(self, offer: bool = True) -> bool:
        """Write the changes to a new file, and -- with ``offer`` -- offer it in
        place of the old one. Whether it was written."""
        edit = self._edit
        if edit is None or not edit.changed:
            return False
        found = edit.problems()
        errors = [p for p in found if p.level == ERROR]
        if errors:
            listed = "\n".join(f"{p.where}: {p.what}" for p in errors[:8])
            messages.warning(self, "Not saved", f"Put these right first:\n\n{listed}")
            return False
        if found:
            listed = "\n".join(f"{p.where}: {p.what}" for p in found[:8])
            answer = messages.question(
                self,
                "Save as",
                f"Saved as it is, it has:\n\n{listed}\n\nSave it anyway?",
                QMessageBox.Save | QMessageBox.Cancel,
                QMessageBox.Cancel,
            )
            if answer != QMessageBox.Save:
                return False
        target = folders.save_file(
            self,
            self.ctx,
            folders.DBC,
            f"Save {edit.path.name} as",
            "CAN database (*.dbc);;All files (*)",
            edit.path.parent,
            suggested=f"{edit.path.stem}_changed.dbc",
        )
        if not target:
            return False
        try:
            differences = edit.save_as(target)
        except (EditError, OSError) as exc:
            messages.warning(self, "Not saved", str(exc))
            return False
        if differences:
            listed = "\n".join(f"{f.where}: {f.what}" for f in differences[:8])
            messages.warning(
                self,
                "Saved, but it reads back differently",
                f"{Path(target).name} was written, and reading it back gives:\n\n{listed}",
            )
        old, renames = str(edit.path), edit.renamed_messages()
        self._edit = None
        self._changed()
        if offer:
            self.saved_as.emit(old, str(target), renames)
        return True

    # --- multiplexing -------------------------------------------------------------------
    def _fill_multiplexer(self, msg: Message | None) -> None:
        top = multiplexer(msg)
        values = [] if top is None else multiplexer_values(msg, top)
        self.mux.blockSignals(True)
        self.mux.clear()
        for value in values:
            name = (top.choices or {}).get(value)
            self.mux.addItem(f"{name} ({value})" if name is not None else str(value), value)
        self.mux.blockSignals(False)
        wanted = bool(values)
        self.mux_label.setText(f"Multiplexer {top.name}" if wanted else "")
        self.mux_label.setVisible(wanted)
        self.mux.setVisible(wanted)

    def multiplexer_value(self) -> int | None:
        """The value the layout is drawn for; None for a message that has none."""
        return self.mux.currentData() if self.mux.count() else None

    def set_multiplexer_value(self, value: int) -> None:
        self.mux.setCurrentIndex(self.mux.findData(value))

    def _present(self) -> set[str] | None:
        if self._message is None or not self.mux.count():
            return None
        return present(self._message, self.mux.currentData())

    def _show_multiplexed(self) -> None:
        if self._message is None:
            return
        chosen = self.signal_chosen()
        self.layout_view.show_message(self._message, self._colours(self._message), self._present())
        self.layout_view.select(chosen)
        self._grey_absent()

    def _grey_absent(self) -> None:
        """The signals not in a frame with the value chosen, greyed in the list."""
        there = self._present()
        faint = self.palette().brush(QPalette.PlaceholderText)
        normal = self.palette().brush(QPalette.Text)
        for row in range(self.signals.topLevelItemCount()):
            item = self.signals.topLevelItem(row)
            absent = there is not None and item.data(0, ROLE_SIGNAL) not in there
            for column in range(self.signals.columnCount()):
                item.setForeground(column, faint if absent else normal)

    def _size_layout(self, length: int) -> None:
        """As tall as the message, up to a classic frame's eight bytes: a CAN
        FD message's sixty-four are scrolled through."""
        rows = max(1, min(length, 8))
        self._layout_scroll.setFixedHeight(rows * self.layout_view.row_height() + 4)

    # --- the chosen signal --------------------------------------------------------------
    def select_signal(self, name: str) -> None:
        for row in range(self.signals.topLevelItemCount()):
            item = self.signals.topLevelItem(row)
            if item.data(0, ROLE_SIGNAL) == name:
                self.signals.setCurrentItem(item)
                return

    def signal_chosen(self) -> str:
        item = self.signals.currentItem()
        return "" if item is None else item.data(0, ROLE_SIGNAL)

    def _signal(self, name: str) -> DbSignal | None:
        if self._message is None:
            return None
        return next((s for s in self._message.signals if s.name == name), None)

    def key_of(self, name: str) -> str:
        """A signal's key in Signals and Plot, which is where its values are."""
        return f"DBC {self._message.name}/{name}"

    def _on_signal_chosen(self, item: QTreeWidgetItem | None, _before=None) -> None:
        name = "" if item is None else item.data(0, ROLE_SIGNAL)
        signal = self._signal(name)
        there = self._present()
        if signal is not None and there is not None and name not in there:
            # A signal not in the frame drawn: draw one it is in.
            self.set_multiplexer_value(signal.multiplexer_ids[0])
        self.layout_view.select(name)
        self.signal_about.setText("" if signal is None else about(signal))
        self.signal_about.setVisible(signal is not None)
        self._plot_follows()

    def _plot_follows(self) -> None:
        name = self.signal_chosen()
        self.plot_btn.setEnabled(bool(name) and self.hub.get(self.key_of(name)) is not None)

    def _plot(self) -> None:
        if name := self.signal_chosen():
            self.plot_wanted.emit(self.key_of(name))

    # --- live values --------------------------------------------------------------------
    def _show_now(self) -> None:
        if self._message is None:
            return
        for row in range(self.signals.topLevelItemCount()):
            item = self.signals.topLevelItem(row)
            name = item.data(0, ROLE_SIGNAL)
            series = self.hub.get(self.key_of(name))
            text = "" if series is None else value_text(series.latest, self._signal(name))
            if item.text(NOW) != text:
                item.setText(NOW, text)
        self._plot_follows()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._message is not None:
            self._now_timer.start()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._now_timer.stop()
