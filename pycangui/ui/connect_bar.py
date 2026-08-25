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
    QPushButton,
    QToolBar,
)

from pycangui.core.bus import available_interfaces
from pycangui.core.channels import Channels
from pycangui.core.context import Context
from pycangui.core.detect import (
    Channel,
    channel_suggestions,
    channels_for,
    takes_a_channel,
)
from pycangui.core.worker import Worker

BITRATES = (125_000, 250_000, 500_000, 1_000_000)
ROLE_EXTRA = 0x0100  # Qt.UserRole: the rest of a detected adapter's configuration


class ConnectBar(QToolBar):
    # interface, channel, bitrate, fd, extra (the rest of a detected adapter's
    # configuration -- an IXXAT's unique_hardware_id, a Vector's serial)
    connect_requested = Signal(str, str, int, bool, object)
    disconnect_requested = Signal()

    def __init__(self, channels: Channels, ctx: Context) -> None:
        super().__init__("Connection")
        self.setObjectName("connect_bar")  # needed for saveState/restoreState
        self.setMovable(False)
        self.channels = channels
        self.ctx = ctx
        self._loading = False
        self._worker = Worker(self)  # detection talks to drivers; keep it off the GUI thread
        self._detecting = False

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
        self.interface.currentTextChanged.connect(self._on_interface_changed)
        # Editable: detection covers most adapters, but not every backend can
        # enumerate, and a channel can always be typed in.
        self.channel = QComboBox()
        self.channel.setEditable(True)
        self.channel.setMinimumWidth(150)
        self.channel.setToolTip("Pick a detected adapter, or type a channel")
        self.channel.currentTextChanged.connect(lambda _t: self._save_settings())
        self.detect = QPushButton("Detect")
        self.detect.setToolTip("Ask the selected interface which adapters are attached")
        self.detect.clicked.connect(lambda: self.detect_channels(announce=True))
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
        ):
            self.addWidget(QLabel(f" {label}: "))
            self.addWidget(widget)
        self.addWidget(self.detect)
        self.addWidget(QLabel(" Bitrate: "))
        self.addWidget(self.bitrate)
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

    # --- detection --------------------------------------------------------------------
    @Slot(str)
    def _on_interface_changed(self, _text: str) -> None:
        self._save_settings()
        if self._loading:
            return
        # A channel belongs to its interface -- "can0" means nothing to an
        # IXXAT -- so the old one goes rather than lingering in the list.
        interface = self.interface.currentText()
        self._fill_channels(channel_suggestions(interface))
        self._set_channel_enabled(interface)
        # Someone picking an interface wants to know what is attached to it.
        # Not on startup, though: enumerating adapters can take seconds and is
        # nobody's idea of a launch.
        self.detect_channels(keep_typed=False)

    def detect_channels(self, announce: bool = False, keep_typed: bool = True) -> None:
        """Ask the interface what is attached, off the GUI thread."""
        if self._detecting:
            return
        interface = self.interface.currentText()
        self._detecting = True
        self.detect.setEnabled(False)
        self.detect.setText("...")
        typed = self.channel.currentText() if keep_typed else ""
        self._worker.submit(
            lambda: channels_for(interface),
            lambda found, error: self._on_detected(interface, found, error, announce, typed),
        )

    def _on_detected(
        self, interface: str, found, error: str | None, announce: bool, typed: str
    ) -> None:
        self._detecting = False
        self.detect.setEnabled(True)
        self.detect.setText("Detect")
        if error is not None:
            self.ctx.log(f"Detect on {interface} failed: {error}")
            return
        # Whatever was typed into the box while detection was out also counts:
        # it runs in the background, and someone who started typing a channel
        # must not have it wiped when the answer arrives.  Text that matches an
        # item is a selection, not typing -- often one pycangui suggested
        # itself -- and detection is free to replace it.
        current = self.channel.currentText().strip()
        if not typed and current and self.channel.findText(current) < 0:
            typed = current
        entries = list(found or [])
        # Keeping what was typed means detection cannot discard a channel the
        # backend was unable to enumerate but which works perfectly well.
        if typed and typed not in {entry.text for entry in entries}:
            entries.insert(0, Channel(config={"channel": typed}, label=typed))
        self._fill_channels(entries, select=typed)
        if not found and announce:
            self.ctx.log(
                f"Detect: {interface} reported no adapters.  Either none is attached, "
                "its driver is not installed, or this backend cannot enumerate -- "
                "type the channel in and connect anyway."
            )
        elif announce:
            self.ctx.log(f"Detect: {interface} reported {len(found)} channel(s)")

    def _set_channel_enabled(self, interface: str) -> None:
        """Blank and disable the box for a backend that has no channel.

        Nothing in python-can needs this today -- every backend takes a
        channel, most with a default -- but a box you can type into whose
        value is discarded is worse than one that says it is not used.
        """
        usable = takes_a_channel(interface)
        self.channel.setEnabled(usable and not self.button.isChecked())
        if not usable:
            was_loading, self._loading = self._loading, True
            self.channel.clear()
            self.channel.setCurrentText("")
            self._loading = was_loading
        self.channel.setToolTip(
            "Pick a detected adapter, or type a channel"
            if usable
            else f"{interface} does not use a channel"
        )

    def _fill_channels(self, entries, select: str = "") -> None:
        """Put entries in the channel box, keeping the first of any duplicates.

        Duplicates are judged by the *label*, not by the channel: two IXXAT
        dongles both offer channel 0, and collapsing those would throw away
        the second adapter -- which is the whole thing this is here to fix.
        """
        was_loading = self._loading
        self._loading = True
        self.channel.clear()
        seen = set()
        for entry in entries:
            if entry.label in seen:
                continue
            seen.add(entry.label)
            self.channel.addItem(entry.label, entry.config)
        self.channel.setCurrentText(select or (entries[0].label if entries else ""))
        self._loading = was_loading

    def current_extra(self) -> dict:
        """The configuration of the selected adapter, beyond its channel name.

        Empty when the channel was typed rather than detected: there is then
        nothing to say about which device is meant, and the backend picks.
        """
        index = self.channel.findText(self.channel.currentText())
        if index < 0:
            return {}
        config = self.channel.itemData(index) or {}
        return {key: value for key, value in config.items() if key != "channel"}

    def current_channel(self) -> str:
        """The channel itself, as the backend names it."""
        index = self.channel.findText(self.channel.currentText())
        if index >= 0 and (config := self.channel.itemData(index)):
            return str(config.get("channel", self.channel.currentText()))
        return self.channel.currentText().strip()

    def shutdown(self) -> None:
        self._worker.stop()

    # --- per-channel settings ---------------------------------------------------------
    def _save_settings(self) -> None:
        if self._loading or not self.selector.currentText():
            return
        self.ctx.settings.set(
            f"channels.{self.selector.currentText()}",
            {
                "interface": self.interface.currentText(),
                "channel": self.current_channel(),
                # Saved as well as the channel, so reconnecting picks the same
                # adapter rather than whichever the driver enumerates first.
                "extra": self.current_extra(),
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
        channel = saved.get("channel", "vcan0")
        interface = saved.get("interface", "virtual")
        self._fill_channels(
            [
                Channel(config={"channel": channel, **saved.get("extra", {})}, label=channel),
                *channel_suggestions(interface),
            ],
            select=channel,
        )
        self._set_channel_enabled(interface)
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
                self.current_channel(),
                self.bitrate.currentData(),
                self.fd.isChecked(),
                self.current_extra(),
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
        for w in (self.interface, self.channel, self.detect, self.bitrate, self.fd):
            w.setEnabled(not connected)
        if not connected:
            self._set_channel_enabled(self.interface.currentText())
        self._loading = was_loading
