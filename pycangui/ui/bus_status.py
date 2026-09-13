# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""The status bar: a coloured indicator per channel, then everything else.

Each channel's dot says how its controller is doing -- green taking part,
amber counting errors, red bus off, grey not connected -- and clicking the
channel opens the way back from bus off.  Colour alone is not the message: the
text says the state too whenever it is anything but normal.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMenu, QToolButton, QWidget

from pycangui.core import bus_health
from pycangui.core.channels import Channels

COLOURS = {
    bus_health.DOWN: "#9e9e9e",
    bus_health.OK: "#2e9d3a",
    bus_health.WARNING: "#e69500",
    bus_health.PASSIVE: "#e69500",
    bus_health.BUS_OFF: "#d32f2f",
}

DOT_SIZE = 12

RECOVER_TIP = (
    "Restart this channel's CAN controller.\n\n"
    "Fix what put it bus off first: restarted onto the wrong bitrate or a bad\n"
    "line, it goes straight back to putting error frames on the bus.\n"
    "Where the adapter has no reset of its own, the channel is closed and\n"
    "opened again, which the protocol panes see as a reconnect."
)


def dot(colour: str, size: int = DOT_SIZE) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(colour))
    painter.drawEllipse(1, 1, size - 2, size - 2)
    painter.end()
    return QIcon(pixmap)


class ChannelIndicator(QToolButton):
    """One channel: its dot, its state and load, and a menu to recover it."""

    def __init__(self, channels: Channels, name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._channels = channels
        self.name = name
        self.health = ""
        self.setAutoRaise(True)
        self.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.setPopupMode(QToolButton.InstantPopup)
        self.setStyleSheet("QToolButton::menu-indicator { image: none; }")
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        self.recover_action = menu.addAction("Recover from bus off", self._recover)
        self.recover_action.setToolTip(RECOVER_TIP)
        self.setMenu(menu)

    def refresh(self, active: bool) -> None:
        bus = self._channels.get(self.name)
        mark = "*" if active else ""  # the protocol panes' channel
        connected = bus is not None and bus.is_connected
        health = bus.health if connected else bus_health.DOWN
        if connected:
            state = "" if health == bus_health.OK else f"{health}, "
            self.setText(f"{mark}{self.name}: {state}{bus.load_percent:.1f}% load")
            self.setToolTip(f"{bus.health_detail}\n\nClick for Recover from bus off.")
        else:
            self.setText(f"{mark}{self.name}: down")
            self.setToolTip(bus_health.MEANING[bus_health.DOWN])
        self.recover_action.setEnabled(connected)
        if health != self.health:
            self.health = health
            self.setIcon(dot(COLOURS[health]))

    def _recover(self) -> None:
        bus = self._channels.get(self.name)
        if bus is not None:
            bus.recover()


class BusStatus(QWidget):
    """The indicators, one per channel in channel order, and a summary after."""

    def __init__(self, channels: Channels, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._channels = channels
        self.indicators: dict[str, ChannelIndicator] = {}
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(4)
        #: Recording and frame count: what the window says beside the channels.
        self.summary = QLabel()
        self._layout.addWidget(self.summary, 1)

    def refresh(self) -> None:
        names = self._channels.names()
        if list(self.indicators) != names:
            self._rebuild(names)
        for name, indicator in self.indicators.items():
            indicator.refresh(name == self._channels.active)

    def _rebuild(self, names: list[str]) -> None:
        for indicator in self.indicators.values():
            self._layout.removeWidget(indicator)
            indicator.deleteLater()
        self.indicators = {}
        for position, name in enumerate(names):
            indicator = ChannelIndicator(self._channels, name, self)
            self._layout.insertWidget(position, indicator)
            self.indicators[name] = indicator
