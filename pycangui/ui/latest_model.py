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

#: The last five are the timing statistics, hidden until somebody asks for
#: them: they answer a different question from Rate and Period (see TIPS),
#: and fifteen columns in a pane that opens in a quarter of the window is a
#: table nobody can read.
COLUMNS = (
    "ID",
    "Kind",
    "Channel",
    "Dir",
    "DLC",
    "Data",
    "Count",
    "Rate",
    "Period",
    "Last",
    "First",
    "Period min",
    "Period avg",
    "Period max",
    "Jitter",
)
STATISTICS = COLUMNS[10:]

#: On the header, because two time bases in one row is worth a sentence
#: each where somebody will read it rather than a paragraph in the manual.
TIPS = {
    "Count": "Frames seen since this id first appeared, or since Clear",
    "Rate": "Measured over the last few seconds, so it describes now",
    "Period": "The same measurement as Rate, written as a cycle time",
    "Last": "Bus time of the newest frame",
    "First": "Bus time of the first frame counted",
    "Period min": "Shortest gap since this id first appeared, or since Clear",
    "Period avg": (
        "Mean gap over that whole span -- not the same as Period,\nwhich describes"
        " the last few seconds"
    ),
    "Period max": "Longest gap since this id first appeared, or since Clear",
    "Jitter": (
        "Longest gap minus shortest, over that whole span.  A message\non a timer"
        " that has drifted, stalled or been blocked shows it here"
    ),
}
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
    #: Every gap, not the recent ones.  ``times`` is deliberately a short
    #: window so that Rate describes *now*; these describe the whole run, so
    #: that a message which stalled once an hour ago still says so.  Kept as
    #: running totals rather than a list: one message at 1 kHz for an hour is
    #: three and a half million gaps nobody wants stored.
    first: float = -1.0  # bus time of the first frame counted
    gaps: int = 0
    gap_min: float = 0.0
    gap_max: float = 0.0
    gap_sum: float = 0.0

    def saw(self, frame: Frame, now: float) -> None:
        if self.times and frame.timestamp < self.times[-1]:
            # Bus time went backwards: a reconnect, or a replay starting over.
            # Averaging across that reports one frame per minus a second --
            # and the statistics so far describe a clock that no longer runs,
            # so they start again too rather than quietly spanning both.
            self.times.clear()
            self.forget_gaps()
        elif self.times:
            self.note(frame.timestamp - self.times[-1])
        if self.first < 0:
            self.first = frame.timestamp
        self.times.append(frame.timestamp)
        self.last_seen = now

    def note(self, gap: float) -> None:
        self.gap_min = gap if not self.gaps else min(self.gap_min, gap)
        self.gap_max = max(self.gap_max, gap)
        self.gap_sum += gap
        self.gaps += 1

    def forget_gaps(self) -> None:
        self.first = -1.0
        self.gaps = 0
        self.gap_min = self.gap_max = self.gap_sum = 0.0

    @property
    def gap_avg(self) -> float:
        return self.gap_sum / self.gaps if self.gaps else 0.0

    @property
    def jitter(self) -> float:
        """Peak to peak, which is what a cycle time is quoted with.

        Two gaps is enough to have one: a message that arrived twice on time
        and once late has a jitter, and waiting for a sample count before
        admitting it would hide exactly the case worth seeing.
        """
        return self.gap_max - self.gap_min if self.gaps > 1 else 0.0


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
        if orientation == Qt.Horizontal:
            if role == Qt.DisplayRole:
                return COLUMNS[section]
            if role == Qt.ToolTipRole:
                return TIPS.get(COLUMNS[section])
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
                case 10:
                    return f"{row.first:.3f}" if row.first >= 0 else ""
                case 11:
                    return _period_text(row.gap_min)
                case 12:
                    return _period_text(row.gap_avg)
                case 13:
                    return _period_text(row.gap_max)
                case 14:
                    # Zero jitter is an answer -- a perfectly regular message
                    # -- where zero of the others only means "not yet known",
                    # so this one says the number rather than going blank.
                    return "" if row.gaps < 2 else _period_text(row.jitter) or "0.0 ms"
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
                case 10:
                    return row.first
                case 11:
                    return row.gap_min
                case 12:
                    return row.gap_avg
                case 13:
                    return row.gap_max
                case 14:
                    return row.jitter
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
