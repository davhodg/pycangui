"""One row per CAN id, showing the latest data, a count and the receive rate.

Rate and cycle time are measured across a window of recent arrivals: the span
from the oldest kept to the newest, divided by the gaps between them.

The obvious method -- how many arrived since the last refresh, over how long
that was -- cannot describe a slow message, and the refresh interval is where
it breaks.  Half a second contains no frames at all of a 1 Hz message and
either none or one of a 2 Hz message, so the answer alternated between nothing
and twice the truth, for exactly the cyclic messages whose cycle time somebody
is trying to read.  A window over arrivals has no such floor: it answers for a
message as slow as one every hundred seconds, and is steadier than the old one
for a fast message, since it averages over gaps rather than over a boundary.

Bytes that changed since the previous frame are marked so the eye is drawn to
activity.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor

from pycangui.core.bus import Frame

COLUMNS = ("ID", "Kind", "Channel", "Dir", "DLC", "Data", "Count", "Rate", "Period", "Last")
ROLE_GROUP = Qt.UserRole + 1
ROLE_CHANNEL = Qt.UserRole + 2
ROLE_SEARCH = Qt.UserRole + 3
CHANGED_COLOUR = QColor(220, 120, 0)

#: Arrivals kept per id.  Thirty-two of them span a third of a second of a
#: 100 Hz message and three seconds of a 10 Hz one: enough gaps to average
#: over, and recent enough to still be describing now.
RATE_SAMPLES = 32
#: ...and nothing older than this counts, which is what governs a message
#: slower than about 6 Hz.  It is a smoothing choice rather than a floor:
#: because two arrivals are always kept, a message slower than the window is
#: still measured, from its one gap.  Five seconds averages five periods of a
#: 1 Hz message and picks up a changed rate within five seconds; ten would
#: smooth more and take twice as long to admit that anything had changed.
RATE_WINDOW = 5.0
#: Nothing for this long, or for three of the message's own periods, and it
#: has stopped rather than slowed.  A stopped message has no rate, and saying
#: it still runs at 10 Hz because it used to is the one wrong answer here.
STOPPED_AFTER = 2.0


@dataclass(slots=True)
class _Row:
    frame: Frame
    count: int = 1
    rate_hz: float = 0.0
    period_s: float = 0.0  # the mean gap over the window, so 1 / rate exactly
    prev_data: bytes = b""
    #: Bus timestamps of the recent arrivals, oldest first.  Bus time rather
    #: than ours: the driver stamps a frame when it arrives, which survives
    #: the GUI delivering a hundred of them in one batch.
    times: deque[float] = field(default_factory=lambda: deque(maxlen=RATE_SAMPLES))
    #: When the last one reached us, on the clock the staleness check reads.
    #: The two cannot be one clock -- bus time starts again at zero on every
    #: connect, and "how long since" has to mean something across that.
    last_seen: float = field(default_factory=time.monotonic)

    def saw(self, frame: Frame, now: float) -> None:
        if self.times and frame.timestamp < self.times[-1]:
            # Bus time went backwards: a reconnect, or a replay starting over.
            # Averaging across that reports one frame per minus a second.
            self.times.clear()
        self.times.append(frame.timestamp)
        self.last_seen = now


class LatestModel(QAbstractTableModel):
    def __init__(self) -> None:
        super().__init__()
        self._rows: list[_Row] = []
        self._index: dict[tuple[str, int, bool], int] = {}  # key -> row number

    # --- required overrides --------------------------------------------------
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return COLUMNS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        row = self._rows[index.row()]
        f = row.frame
        col = index.column()
        if role == Qt.DisplayRole:
            match col:
                case 0:
                    return f"{f.can_id:08X}" if f.extended else f"{f.can_id:03X}"
                case 1:
                    return f.kind
                case 2:
                    return f.channel
                case 3:
                    return "Rx" if f.rx else "Tx"
                case 4:
                    return f"{f.dlc} FD" if f.fd else str(f.dlc)
                case 5:
                    return f.data.hex(" ").upper()
                case 6:
                    return str(row.count)
                case 7:
                    return _rate_text(row.rate_hz)
                case 8:
                    return _period_text(row.period_s)
                case 9:
                    return f"{f.timestamp:.3f}"
        elif role == Qt.ForegroundRole and col == 5 and row.prev_data != f.data and row.count > 1:
            return CHANGED_COLOUR
        elif role == ROLE_GROUP:
            return f.group
        elif role == ROLE_CHANNEL:
            return f.channel
        elif role == ROLE_SEARCH:
            return _searchable(f)
        elif role == Qt.UserRole:  # raw value for sorting
            match col:
                case 0:
                    return f.can_id
                case 4:
                    return f.dlc
                case 6:
                    return row.count
                case 7:
                    return row.rate_hz
                case 8:
                    return row.period_s
                case 9:
                    return f.timestamp
                case _:
                    return self.data(index, Qt.DisplayRole)
        return None

    # --- updates -------------------------------------------------------------
    def append(self, frames: list[Frame]) -> None:
        now = time.monotonic()
        touched: set[int] = set()
        for f in frames:
            key = (f.channel, f.can_id, f.extended)
            r = self._index.get(key)
            if r is None:
                self._index[key] = len(self._rows)
                self.beginInsertRows(QModelIndex(), len(self._rows), len(self._rows))
                row = _Row(f)
                row.saw(f, now)
                self._rows.append(row)
                self.endInsertRows()
            else:
                row = self._rows[r]
                row.prev_data = row.frame.data
                row.frame = f
                row.count += 1
                row.saw(f, now)
                # So the second frame of a pair already says the gap, rather
                # than the row sitting blank until the next refresh.
                _measure(row, now)
                touched.add(r)
        if touched:
            lo, hi = min(touched), max(touched)
            self.dataChanged.emit(self.index(lo, 0), self.index(hi, len(COLUMNS) - 1))

    def refresh_rates(self) -> None:
        """Call periodically (e.g. every 500 ms) to update Rate and Period.

        The interval is how often the figures are redrawn and no longer what
        they are measured over, so it can be chosen for the eye alone.
        """
        now = time.monotonic()
        for row in self._rows:
            _measure(row, now)
        if self._rows:
            self.dataChanged.emit(self.index(0, 7), self.index(len(self._rows) - 1, 8))

    def clear(self) -> None:
        self.beginResetModel()
        self._rows.clear()
        self._index.clear()
        self.endResetModel()


def _measure(row: _Row, now: float) -> None:
    """Set the row's rate and cycle time from the arrivals it has kept."""
    times = row.times
    while len(times) > 2 and times[-1] - times[0] > RATE_WINDOW:
        # Two are always kept, so a message slower than the window still gets
        # an answer: the only thing available about one frame every twenty
        # seconds is the twenty seconds.
        times.popleft()
    span = times[-1] - times[0] if len(times) > 1 else 0.0
    if span <= 0:
        row.rate_hz, row.period_s = 0.0, 0.0
        return
    period = span / (len(times) - 1)
    if now - row.last_seen > max(STOPPED_AFTER, 3 * period):
        row.rate_hz, row.period_s = 0.0, 0.0
        return
    row.rate_hz, row.period_s = 1.0 / period, period


def _rate_text(hz: float) -> str:
    """Two decimals below 1 Hz, where one of them would round to nothing."""
    if hz <= 0:
        return ""
    return f"{hz:.2f} Hz" if hz < 1 else f"{hz:.1f} Hz"


def _period_text(seconds: float) -> str:
    """A cycle time is read in milliseconds, until it is long enough not to be."""
    if seconds <= 0:
        return ""
    return f"{seconds:.2f} s" if seconds >= 1 else f"{seconds * 1000:.1f} ms"


def _searchable(f: Frame) -> str:
    """What the filter box matches against: id, name, channel and data."""
    ident = f"{f.can_id:08X}" if f.extended else f"{f.can_id:03X}"
    return f"{ident} {f.kind} {f.channel} {f.data.hex(' ')} {'rx' if f.rx else 'tx'}".lower()
