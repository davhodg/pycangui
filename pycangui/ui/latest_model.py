"""One row per CAN id, showing the latest data, a count and the receive rate.

Rate is measured over the refresh interval of the view (count delta / time
delta), which gives a stable "approximately N Hz" rather than a jittery
per-frame figure.  Bytes that changed since the previous frame are marked so
the eye is drawn to activity.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor

from pycangui.core.bus import Frame
from pycangui.core.classify import group_of

COLUMNS = ("ID", "Kind", "Type", "Dir", "DLC", "Data", "Count", "Rate", "Period", "Last")
ROLE_GROUP = Qt.UserRole + 1
CHANGED_COLOUR = QColor(220, 120, 0)


@dataclass(slots=True)
class _Row:
    frame: Frame
    count: int = 1
    rate_hz: float = 0.0
    last_rate_count: int = 0
    last_rate_time: float = field(default_factory=time.monotonic)
    prev_data: bytes = b""
    period_s: float = 0.0  # between the last two frames


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
                    return ("FD" if f.fd else "CAN") + ("x" if f.extended else "")
                case 3:
                    return "Rx" if f.rx else "Tx"
                case 4:
                    return str(f.dlc)
                case 5:
                    return f.data.hex(" ").upper()
                case 6:
                    return str(row.count)
                case 7:
                    return f"{row.rate_hz:.1f} Hz" if row.rate_hz else ""
                case 8:
                    return f"{row.period_s * 1000:.1f} ms" if row.period_s else ""
                case 9:
                    return f"{f.timestamp:.3f}"
        elif role == Qt.ForegroundRole and col == 5 and row.prev_data != f.data and row.count > 1:
            return CHANGED_COLOUR
        elif role == ROLE_GROUP:
            return group_of(f.kind)
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
        touched: set[int] = set()
        for f in frames:
            key = (f.channel, f.can_id, f.extended)
            r = self._index.get(key)
            if r is None:
                self._index[key] = len(self._rows)
                self.beginInsertRows(QModelIndex(), len(self._rows), len(self._rows))
                self._rows.append(_Row(f))
                self.endInsertRows()
            else:
                row = self._rows[r]
                row.period_s = f.timestamp - row.frame.timestamp
                row.prev_data = row.frame.data
                row.frame = f
                row.count += 1
                touched.add(r)
        if touched:
            lo, hi = min(touched), max(touched)
            self.dataChanged.emit(self.index(lo, 0), self.index(hi, len(COLUMNS) - 1))

    def refresh_rates(self) -> None:
        """Call periodically (e.g. every 500 ms) to update the Rate column."""
        now = time.monotonic()
        for row in self._rows:
            dt = now - row.last_rate_time
            if dt >= 0.45:
                row.rate_hz = (row.count - row.last_rate_count) / dt
                row.last_rate_count = row.count
                row.last_rate_time = now
        if self._rows:
            self.dataChanged.emit(self.index(0, 7), self.index(len(self._rows) - 1, 7))

    def clear(self) -> None:
        self.beginResetModel()
        self._rows.clear()
        self._index.clear()
        self.endResetModel()
