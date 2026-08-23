"""Toolbar for the channels: pick one, configure it, connect it.

The selected channel is also the one the protocol panes (CANopen, UDS, J1939,
XCP) work with, so there is a single notion of "the channel I am on".  The
trace, the recorder and the decoders always see every connected channel.
"""

from __future__ import annotations

from PySide6.QtCore import Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QToolBar,
)

from pycangui.core.bus import available_interfaces
from pycangui.core.channels import Channels
from pycangui.core.context import Context

BITRATES = (125_000, 250_000, 500_000, 1_000_000)


class ConnectBar(QToolBar):
    connect_requested = Signal(str, str, int, bool)  # interface, channel, bitrate, fd
    disconnect_requested = Signal()

    def __init__(self, channels: Channels, ctx: Context) -> None:
        super().__init__("Connection")
        self.setObjectName("connect_bar")  # needed for saveState/restoreState
        self.setMovable(False)
        self.channels = channels
        self.ctx = ctx
        self._loading = False

        self.selector = QComboBox()
        self.selector.setToolTip("The channel the protocol panes work with")
        self.selector.currentTextChanged.connect(self._on_selected)
        add = QPushButton("+")
        add.setToolTip("Add another channel (a second port, or a second adapter)")
        add.setFixedWidth(28)
        add.clicked.connect(self._add_channel)
        remove = QPushButton("-")
        remove.setToolTip("Remove this channel")
        remove.setFixedWidth(28)
        remove.clicked.connect(self._remove_channel)

        self.interface = QComboBox()
        self.interface.addItems(available_interfaces())
        self.interface.currentTextChanged.connect(lambda _t: self._save_settings())
        self.channel = QLineEdit("vcan0")
        self.channel.setFixedWidth(90)
        self.channel.editingFinished.connect(self._save_settings)
        self.bitrate = QComboBox()
        for b in BITRATES:
            self.bitrate.addItem(f"{b // 1000} kbit/s", b)
        self.bitrate.setCurrentIndex(2)
        self.bitrate.currentIndexChanged.connect(lambda _i: self._save_settings())
        self.fd = QCheckBox("FD")
        self.fd.toggled.connect(lambda _c: self._save_settings())
        self.button = QPushButton("Connect")
        self.button.setCheckable(True)
        self.button.toggled.connect(self._on_toggled)

        self.addWidget(QLabel(" Channel: "))
        self.addWidget(self.selector)
        self.addWidget(add)
        self.addWidget(remove)
        self.addSeparator()
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

        channels.channel_added.connect(self._refresh_selector)
        channels.channel_removed.connect(self._refresh_selector)
        channels.state_changed.connect(self._on_state_changed)
        self._refresh_selector()

    # --- channels -----------------------------------------------------------------
    def _refresh_selector(self, _name: str = "") -> None:
        self._loading = True
        self.selector.clear()
        self.selector.addItems(self.channels.names())
        if self.channels.active:
            self.selector.setCurrentText(self.channels.active)
        self._loading = False
        self._load_settings()

    def _add_channel(self) -> None:
        suggestion = f"CAN {len(self.channels.names()) + 1}"
        name, ok = QInputDialog.getText(self, "Add channel", "Name:", text=suggestion)
        if ok and name.strip():
            self.channels.add(name.strip())
            self.channels.set_active(name.strip())

    def _remove_channel(self) -> None:
        if len(self.channels.names()) <= 1:
            self.ctx.log("Channels: at least one channel is needed")
            return
        self.channels.remove(self.selector.currentText())

    @Slot(str)
    def _on_selected(self, name: str) -> None:
        if self._loading or not name:
            return
        self.channels.set_active(name)
        self._load_settings()

    # --- per-channel settings ---------------------------------------------------------
    def _save_settings(self) -> None:
        if self._loading or not self.selector.currentText():
            return
        self.ctx.settings.set(
            f"channels.{self.selector.currentText()}",
            {
                "interface": self.interface.currentText(),
                "channel": self.channel.text(),
                "bitrate": self.bitrate.currentData(),
                "fd": self.fd.isChecked(),
            },
        )

    def _load_settings(self) -> None:
        name = self.selector.currentText()
        if not name:
            return
        saved = self.ctx.settings.get(f"channels.{name}", {})
        self._loading = True
        self.interface.setCurrentText(saved.get("interface", "virtual"))
        self.channel.setText(saved.get("channel", "vcan0"))
        index = self.bitrate.findData(saved.get("bitrate", 500_000))
        self.bitrate.setCurrentIndex(index if index >= 0 else 2)
        self.fd.setChecked(bool(saved.get("fd", False)))
        self._loading = False
        bus = self.channels.get(name)
        self.set_connected(bool(bus and bus.is_connected))

    # --- connecting -------------------------------------------------------------------
    @Slot(bool)
    def _on_toggled(self, checked: bool) -> None:
        if self._loading:
            return
        if checked:
            self._save_settings()
            self.connect_requested.emit(
                self.interface.currentText(),
                self.channel.text(),
                self.bitrate.currentData(),
                self.fd.isChecked(),
            )
        else:
            self.disconnect_requested.emit()

    @Slot(str, bool)
    def _on_state_changed(self, name: str, connected: bool) -> None:
        if name == self.selector.currentText():
            self.set_connected(connected)

    def set_connected(self, connected: bool) -> None:
        # Block our own handler so syncing the button does not re-trigger it
        was_loading = self._loading
        self._loading = True
        self.button.setChecked(connected)
        self.button.setText("Disconnect" if connected else "Connect")
        for w in (self.interface, self.channel, self.bitrate, self.fd):
            w.setEnabled(not connected)
        self._loading = was_loading
