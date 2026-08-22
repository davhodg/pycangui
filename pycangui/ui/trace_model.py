"""Table model for the live trace.

Qt's model/view split: the *model* owns the data and answers questions
("how many rows?", "what is in cell (r, c)?"); the *view* (a QTableView) only
asks about the cells that are currently visible.  That is what makes a
large trace cheap -- nothing is copied into widgets.
"""

from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt

from pycangui.core.bus import Frame
from pycangui.core.classify import group_of

COLUMNS = ("Time", "Ch", "Dir", "ID", "Kind", "Type", "DLC", "Data")
ROLE_GROUP = Qt.UserRole + 1
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
            return group_of(f.kind)
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
                return ("FD" if f.fd else "CAN") + ("x" if f.extended else "")
            case 6:
                return str(f.dlc)
            case 7:
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
