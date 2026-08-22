"""Transmit pane: a list of messages to send once or cyclically.

Each row: ID (hex), Ext, FD, Data (hex bytes), Period ms, Cyclic.  Ticking
*Cyclic* starts a python-can periodic task (hardware timed on adapters that
support it); editing the data of a running row updates the task in place.
The list is saved in settings.json ("tx.messages") and restored on start.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pycangui.core.bus import BusManager
from pycangui.core.context import Context

COL_ID, COL_EXT, COL_FD, COL_DATA, COL_PERIOD, COL_CYCLIC, COL_NAME = range(7)
HEADERS = ("ID", "Ext", "FD", "Data", "Period ms", "Cyclic", "Comment")
DEFAULT_ROW = {
    "id": "123",
    "ext": False,
    "fd": False,
    "data": "00 11 22 33",
    "period": 100,
    "name": "",
}


def parse_hex_bytes(text: str) -> bytes:
    return bytes.fromhex(text.replace(",", " ").replace("0x", ""))


class TxView(QWidget):
    def __init__(self, bus: BusManager, ctx: Context) -> None:
        super().__init__()
        self.bus = bus
        self.ctx = ctx
        self._tasks: dict[int, object] = {}  # row -> periodic task
        self._loading = False

        self.table = QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.setFont(QFont("Consolas", 9))
        self.table.verticalHeader().setDefaultSectionSize(20)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(COL_DATA, QHeaderView.Stretch)
        header.setStretchLastSection(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.cellDoubleClicked.connect(self._on_double_clicked)

        send = QPushButton("Send selected")
        send.clicked.connect(self.send_selected)
        add = QPushButton("Add")
        add.clicked.connect(lambda: self.add_row(DEFAULT_ROW))
        remove = QPushButton("Remove")
        remove.clicked.connect(self.remove_selected)
        stop_all = QPushButton("Stop all cyclic")
        stop_all.clicked.connect(self.stop_all)
        bar = QHBoxLayout()
        for b in (send, add, remove, stop_all):
            bar.addWidget(b)
        bar.addStretch()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.addLayout(bar)
        layout.addWidget(self.table)

        bus.disconnected.connect(self._on_disconnected)
        self._load()

    # --- rows ------------------------------------------------------------------
    def add_row(self, spec: dict) -> int:
        self._loading = True
        r = self.table.rowCount()
        self.table.insertRow(r)
        for col, value in (
            (COL_ID, spec.get("id", "")),
            (COL_DATA, spec.get("data", "")),
            (COL_PERIOD, str(spec.get("period", 100))),
            (COL_NAME, spec.get("name", "")),
        ):
            self.table.setItem(r, col, QTableWidgetItem(str(value)))
        for col, key in ((COL_EXT, "ext"), (COL_FD, "fd"), (COL_CYCLIC, None)):
            item = QTableWidgetItem()
            item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            item.setCheckState(Qt.Checked if key and spec.get(key) else Qt.Unchecked)
            self.table.setItem(r, col, item)
        self._loading = False
        self._save()
        return r

    def remove_selected(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            self._stop_row(r)
            self.table.removeRow(r)
        # row numbers shifted: rebuild the task map from the Cyclic column
        self._tasks = {}
        for r in range(self.table.rowCount()):
            if self.table.item(r, COL_CYCLIC).checkState() == Qt.Checked:
                self._start_row(r)
        self._save()

    def _spec(self, r: int) -> dict:
        return {
            "id": self.table.item(r, COL_ID).text(),
            "ext": self.table.item(r, COL_EXT).checkState() == Qt.Checked,
            "fd": self.table.item(r, COL_FD).checkState() == Qt.Checked,
            "data": self.table.item(r, COL_DATA).text(),
            "period": self.table.item(r, COL_PERIOD).text(),
            "name": self.table.item(r, COL_NAME).text(),
        }

    def _message(self, r: int) -> tuple[int, bytes, bool, bool, float]:
        """Parse a row; raises ValueError with a readable message."""
        spec = self._spec(r)
        try:
            can_id = int(spec["id"], 16)
            data = parse_hex_bytes(spec["data"])
            period = float(spec["period"] or 0) / 1000
        except ValueError as exc:
            raise ValueError(f"row {r + 1}: {exc}") from exc
        if len(data) > (64 if spec["fd"] else 8):
            raise ValueError(f"row {r + 1}: {len(data)} data bytes is too many")
        return can_id, data, spec["ext"], spec["fd"], period

    # --- sending -----------------------------------------------------------------
    def send_row(self, r: int) -> None:
        try:
            can_id, data, ext, fd, _ = self._message(r)
        except ValueError as exc:
            self.ctx.log(f"TX {exc}")
            return
        self.bus.send(can_id, data, extended=ext, fd=fd)

    @Slot()
    def send_selected(self) -> None:
        for r in sorted({i.row() for i in self.table.selectedIndexes()}):
            self.send_row(r)

    def _start_row(self, r: int) -> None:
        try:
            can_id, data, ext, fd, period = self._message(r)
            if period <= 0:
                raise ValueError(f"row {r + 1}: period must be > 0 for cyclic")
        except ValueError as exc:
            self.ctx.log(f"TX {exc}")
            self._set_cyclic(r, False)
            return
        task = self.bus.send_periodic(can_id, data, period, extended=ext, fd=fd)
        if task is None:
            self._set_cyclic(r, False)
            return
        self._tasks[r] = task

    def _stop_row(self, r: int) -> None:
        task = self._tasks.pop(r, None)
        if task is not None:
            task.stop()

    def _set_cyclic(self, r: int, on: bool) -> None:
        self._loading = True
        self.table.item(r, COL_CYCLIC).setCheckState(Qt.Checked if on else Qt.Unchecked)
        self._loading = False

    @Slot()
    def stop_all(self) -> None:
        for r in list(self._tasks):
            self._stop_row(r)
            self._set_cyclic(r, False)

    @Slot()
    def _on_disconnected(self) -> None:
        self._tasks.clear()  # the bus stops its own tasks
        for r in range(self.table.rowCount()):
            self._set_cyclic(r, False)

    # --- edits -------------------------------------------------------------------
    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._loading:
            return
        r, col = item.row(), item.column()
        if col == COL_CYCLIC:
            if item.checkState() == Qt.Checked:
                self._start_row(r)
            else:
                self._stop_row(r)
        elif r in self._tasks:  # live edit of a running cyclic message
            self._stop_row(r)
            self._start_row(r)
        self._save()

    def _on_double_clicked(self, r: int, col: int) -> None:
        if col in (COL_EXT, COL_FD, COL_CYCLIC):
            return
        if not self.table.item(r, col).flags() & Qt.ItemIsEditable:
            self.send_row(r)

    # --- persistence ---------------------------------------------------------------
    def _save(self) -> None:
        if not self._loading:
            self.ctx.settings.set(
                "tx.messages", [self._spec(r) for r in range(self.table.rowCount())]
            )

    def _load(self) -> None:
        for spec in self.ctx.settings.get("tx.messages", []):
            self.add_row(spec)
