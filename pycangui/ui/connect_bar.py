"""Toolbar with interface / channel / bitrate / FD and a Connect toggle."""

from __future__ import annotations

from PySide6.QtCore import Signal, Slot
from PySide6.QtWidgets import QCheckBox, QComboBox, QLabel, QLineEdit, QPushButton, QToolBar

from pycangui.core.bus import available_interfaces

BITRATES = (125_000, 250_000, 500_000, 1_000_000)


class ConnectBar(QToolBar):
    connect_requested = Signal(str, str, int, bool)  # interface, channel, bitrate, fd
    disconnect_requested = Signal()

    def __init__(self) -> None:
        super().__init__("Connection")
        self.setObjectName("connect_bar")  # needed for saveState/restoreState
        self.setMovable(False)

        self.interface = QComboBox()
        self.interface.addItems(available_interfaces())
        self.channel = QLineEdit("vcan0")
        self.channel.setFixedWidth(90)
        self.bitrate = QComboBox()
        for b in BITRATES:
            self.bitrate.addItem(f"{b // 1000} kbit/s", b)
        self.bitrate.setCurrentIndex(2)
        self.fd = QCheckBox("FD")
        self.button = QPushButton("Connect")
        self.button.setCheckable(True)
        self.button.toggled.connect(self._on_toggled)

        for label, widget in (
            ("Interface", self.interface),
            ("Channel", self.channel),
            ("Bitrate", self.bitrate),
        ):
            self.addWidget(QLabel(f" {label}: "))
            self.addWidget(widget)
        self.addWidget(self.fd)
        self.addSeparator()
        self.addWidget(self.button)

    @Slot(bool)
    def _on_toggled(self, checked: bool) -> None:
        if checked:
            self.connect_requested.emit(
                self.interface.currentText(),
                self.channel.text(),
                self.bitrate.currentData(),
                self.fd.isChecked(),
            )
        else:
            self.disconnect_requested.emit()

    def set_connected(self, connected: bool) -> None:
        # Block signals so syncing the button programmatically doesn't re-trigger connect
        self.button.blockSignals(True)
        self.button.setChecked(connected)
        self.button.setText("Disconnect" if connected else "Connect")
        self.button.blockSignals(False)
        for w in (self.interface, self.channel, self.bitrate, self.fd):
            w.setEnabled(not connected)
