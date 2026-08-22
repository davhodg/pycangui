"""Trace dock: chronological list, or one row per id with the latest data."""

from __future__ import annotations

from PySide6.QtCore import QSortFilterProxyModel, Qt, QTimer, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QStackedWidget,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from pycangui.core.bus import Frame
from pycangui.ui.latest_model import LatestModel
from pycangui.ui.trace_model import TraceModel

MODES = ("Chronological", "Latest per ID")


def _table(model, font: QFont) -> QTableView:
    table = QTableView()
    table.setModel(model)
    table.setFont(font)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(18)
    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeToContents)
    header.setStretchLastSection(True)
    return table


class TraceView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        mono = QFont("Consolas", 9)
        self.model = TraceModel()
        self.latest = LatestModel()
        # Proxy gives click-to-sort on the per-id view using the raw values
        self._latest_proxy = QSortFilterProxyModel()
        self._latest_proxy.setSourceModel(self.latest)
        self._latest_proxy.setSortRole(Qt.UserRole)
        self._latest_proxy.setDynamicSortFilter(True)

        self.table = _table(self.model, mono)
        self.latest_table = _table(self._latest_proxy, mono)
        self.latest_table.setSortingEnabled(True)
        self.latest_table.sortByColumn(0, Qt.AscendingOrder)

        self.stack = QStackedWidget()
        self.stack.addWidget(self.table)
        self.stack.addWidget(self.latest_table)

        self.mode = QComboBox()
        self.mode.addItems(MODES)
        self.mode.currentIndexChanged.connect(self._on_mode_changed)
        self.autoscroll = QCheckBox("Autoscroll")
        self.autoscroll.setChecked(True)
        clear = QPushButton("Clear")
        clear.clicked.connect(self.clear)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("View:"))
        bar.addWidget(self.mode)
        bar.addWidget(self.autoscroll)
        bar.addStretch()
        bar.addWidget(clear)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.addLayout(bar)
        layout.addWidget(self.stack)

        self._rate_timer = QTimer(self, interval=500, timeout=self.latest.refresh_rates)
        self._rate_timer.start()

    def _on_mode_changed(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        self.autoscroll.setEnabled(index == 0)

    @Slot(list)
    def on_frames(self, frames: list[Frame]) -> None:
        self.model.append(frames)
        self.latest.append(frames)
        if self.autoscroll.isChecked() and self.stack.currentIndex() == 0:
            self.table.scrollToBottom()

    @Slot()
    def clear(self) -> None:
        self.model.clear()
        self.latest.clear()
