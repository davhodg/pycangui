"""Live trace dock content: table + clear/autoscroll controls."""

from __future__ import annotations

from PySide6.QtCore import Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from pycangui.core.bus import Frame
from pycangui.ui.trace_model import TraceModel


class TraceView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.model = TraceModel()

        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setFont(QFont("Consolas", 9))
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(18)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setStretchLastSection(True)

        self.autoscroll = QCheckBox("Autoscroll")
        self.autoscroll.setChecked(True)
        clear = QPushButton("Clear")
        clear.clicked.connect(self.model.clear)

        bar = QHBoxLayout()
        bar.addWidget(self.autoscroll)
        bar.addStretch()
        bar.addWidget(clear)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.addLayout(bar)
        layout.addWidget(self.table)

    @Slot(list)
    def on_frames(self, frames: list[Frame]) -> None:
        self.model.append(frames)
        if self.autoscroll.isChecked():
            self.table.scrollToBottom()
