"""Table model for the live trace.

Qt's model/view split: the *model* owns the data and answers questions
("how many rows?", "what is in cell (r, c)?"); the *view* (a QTableView) only
asks about the cells that are currently visible.  That is what makes a
large trace cheap -- nothing is copied into widgets.
"""

from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt

from pycangui.core.bus import Frame

# "Channel" replaces the old CAN / CANx column: with several buses connected
# the channel is what you need to see, and 11 vs 29-bit is already obvious from
# the width of the id.  FD is marked on the length instead.
COLUMNS = ("Time", "Channel", "Dir", "ID", "Kind", "DLC", "Data")
ROLE_GROUP = Qt.UserRole + 1
ROLE_CHANNEL = Qt.UserRole + 2
ROLE_SEARCH = Qt.UserRole + 3  # everything about a row, lowercased, for the filter box
MAX_ROWS = 500_000


class TraceModel(QAbstractTableModel):
    def __init__(self) -> None:
        super().__init__()
        self._rows: list[Frame] = []

    # --- required overrides (Qt naming, hence camelCase) ----------------------
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return COLUMNS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        f = self._rows[index.row()]
        if role == ROLE_GROUP:
            return f.group
        if role == ROLE_CHANNEL:
            return f.channel
        if role == ROLE_SEARCH:
            return _searchable(f)
        if role != Qt.DisplayRole:
            return None
        match index.column():
            case 0:
                return f"{f.timestamp:12.6f}"
            case 1:
                return f.channel
            case 2:
                return "Rx" if f.rx else "Tx"
            case 3:
                return f"{f.can_id:08X}" if f.extended else f"{f.can_id:03X}"
            case 4:
                return f.kind
            case 5:
                return f"{f.dlc} FD" if f.fd else str(f.dlc)
            case 6:
                return f.data.hex(" ").upper()
        return None

    # --- mutation ---------------------------------------------------------------
    def append(self, frames: list[Frame]) -> None:
        if not frames:
            return
        overflow = len(self._rows) + len(frames) - MAX_ROWS
        if overflow > 0:
            self.beginRemoveRows(QModelIndex(), 0, overflow - 1)
            del self._rows[:overflow]
            self.endRemoveRows()
        first = len(self._rows)
        self.beginInsertRows(QModelIndex(), first, first + len(frames) - 1)
        self._rows.extend(frames)
        self.endInsertRows()

    def clear(self) -> None:
        self.beginResetModel()
        self._rows.clear()
        self.endResetModel()


def _searchable(f: Frame) -> str:
    """What the filter box matches against: id, name, channel and data."""
    ident = f"{f.can_id:08X}" if f.extended else f"{f.can_id:03X}"
    return f"{ident} {f.kind} {f.channel} {f.data.hex(' ')} {'rx' if f.rx else 'tx'}".lower()
