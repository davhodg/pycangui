# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""The status bar: a coloured indicator per channel, then everything else.

Each channel's dot says how its controller is doing -- green taking part,
amber counting errors, red bus off, grey not connected -- and clicking the
channel opens the way back from bus off and the message filter. Colour alone
is not the message: the text says the state too whenever it is anything but
normal.

A channel with a message filter on it says FILTERED, and blinks. Everything
else pycangui hides can be found again by looking at the pane that hid it;
a filter is applied by the driver, so the frames are not anywhere, and the
symptom is a node that has apparently gone quiet. The indicator is
deliberately harder to ignore than the rest of this bar, and it goes on
blinking for as long as the filter is on: somebody who set it an hour ago
and forgot is exactly who it is for.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMenu, QToolButton, QWidget

from pycangui.core import bus_health
from pycangui.core.channels import Channels
from pycangui.core.context import Context
from pycangui.core.filters import describe
from pycangui.ui import message_filter
from pycangui.ui.message_filter import (
    BLINK_MS,
    FILTER_TIP,
    FILTERED_COLOUR,
    FILTERED_LABEL,
)

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

    def __init__(
        self,
        channels: Channels,
        name: str,
        parent: QWidget | None = None,
        ctx: Context | None = None,
    ) -> None:
        super().__init__(parent)
        self._channels = channels
        self._ctx = ctx
        self.name = name
        self.health = ""
        #: Which half of the blink is showing. Only ever moves while there
        #: is a filter on, so an unfiltered channel repaints as it always did.
        self.blink_on = False
        self._shown: tuple[str, bool] | None = None
        self.setAutoRaise(True)
        self.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.setPopupMode(QToolButton.InstantPopup)
        self.setStyleSheet("QToolButton::menu-indicator { image: none; }")
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        self.recover_action = menu.addAction("Recover from bus off", self._recover)
        self.recover_action.setToolTip(RECOVER_TIP)
        self.filter_action = menu.addAction("Message filter...", self._filter)
        self.filter_action.setToolTip(FILTER_TIP)
        self.filter_action.setEnabled(ctx is not None)
        self.setMenu(menu)

    def refresh(self, active: bool) -> None:
        bus = self._channels.get(self.name)
        mark = "*" if active else ""  # the protocol panes' channel
        connected = bus is not None and bus.is_connected
        health = bus.health if connected else bus_health.DOWN
        filters = bus.filters if bus is not None else []
        # First of all, before even the asterisk: on a bar somebody is
        # scanning, it is the thing most likely to explain what they are
        # puzzled by. The asterisk stays next to the name it marks.
        hidden = f"{FILTERED_LABEL} " if filters else ""
        if connected:
            state = "" if health == bus_health.OK else f"{health}, "
            self.setText(f"{hidden}{mark}{self.name}: {state}{bus.load_percent:.1f}% load")
            self.setToolTip(f"{bus.health_detail}\n\nClick for Recover from bus off.")
        else:
            self.setText(f"{hidden}{mark}{self.name}: down")
            self.setToolTip(bus_health.MEANING[bus_health.DOWN])
        if filters:
            self.setToolTip(
                f"Message filter on {self.name}: {describe(filters)}.\n"
                "Everything else is dropped before pycangui sees it: not traced,\n"
                "not decoded, not recorded, and not answered.\n\n"
                "Click to change it."
            )
        self.recover_action.setEnabled(connected)
        self.health = health
        # Painted only when it would actually change. The blink costs a
        # repaint twice a second, and a channel with no filter on it should
        # not be paying that.
        wanted = (health, bool(filters) and self.blink_on)
        if wanted != self._shown:
            self._shown = wanted
            self.setIcon(dot(FILTERED_COLOUR if wanted[1] else COLOURS[health]))

    def open_menu(self, at) -> None:
        """Show the menu at a screen position.

        Its own method because exec blocks until something is chosen, so a
        test can ask whether the menu was opened without opening it. The
        same split the CANopen pane's node menu needed.
        """
        self.menu().exec(at)

    def contextMenuEvent(self, event) -> None:
        """Right click opens the same menu the left one does.

        The button is an InstantPopup, so a left click already shows it,
        but a right click on something that plainly has a menu should not
        do nothing -- and a status bar is where people right click out of
        habit.
        """
        self.open_menu(event.globalPos())

    def wants_blinking(self) -> bool:
        bus = self._channels.get(self.name)
        return bus is not None and bool(bus.filters)

    def _recover(self) -> None:
        bus = self._channels.get(self.name)
        if bus is not None:
            bus.recover()

    def _filter(self) -> None:
        """Ask for this channel's rules, and keep them in the workspace.

        Set on the channel whether or not it is connected: a filter meant
        for a bus is not a property of this particular connection to it.
        """
        bus = self._channels.get(self.name)
        if bus is None or self._ctx is None:
            return
        rules = message_filter.ask(self.name, bus.filters, self)
        if rules is None:
            return
        bus.set_filters(rules)
        message_filter.remember(self._ctx, self.name, rules)
        self.refresh(self.name == self._channels.active)


class BusStatus(QWidget):
    """The indicators, one per channel in channel order, and a summary after."""

    #: True on the loud half of the blink, false on the other. One clock for
    #: everywhere that shows a filter, so the toolbar button and the status
    #: bar dot are never caught in opposite phases -- two things blinking out
    #: of step read as two different problems.
    blinked = Signal(bool)

    def __init__(
        self, channels: Channels, parent: QWidget | None = None, ctx: Context | None = None
    ) -> None:
        super().__init__(parent)
        self._channels = channels
        self._ctx = ctx
        self.indicators: dict[str, ChannelIndicator] = {}
        #: Which half of the blink everything is showing.
        self.blink_on = False
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(4)
        #: Recording and frame count: what the window says beside the channels.
        self.summary = QLabel()
        self._layout.addWidget(self.summary, 1)
        #: Runs only while some channel is filtered; see refresh.
        self._blink = QTimer(self, interval=BLINK_MS, timeout=self._flip)

    def refresh(self) -> None:
        names = self._channels.names()
        if list(self.indicators) != names:
            self._rebuild(names)
        for name, indicator in self.indicators.items():
            indicator.refresh(name == self._channels.active)
        blinking = any(i.wants_blinking() for i in self.indicators.values())
        if blinking and not self._blink.isActive():
            self._blink.start()
        elif not blinking and self._blink.isActive():
            self._blink.stop()
            self._flip(off=True)  # never leave one stuck on the blink colour

    def _flip(self, off: bool = False) -> None:
        self.blink_on = False if off else not self.blink_on
        for name, indicator in self.indicators.items():
            indicator.blink_on = self.blink_on
            indicator.refresh(name == self._channels.active)
        self.blinked.emit(self.blink_on)

    def _rebuild(self, names: list[str]) -> None:
        for indicator in self.indicators.values():
            self._layout.removeWidget(indicator)
            indicator.deleteLater()
        self.indicators = {}
        for position, name in enumerate(names):
            indicator = ChannelIndicator(self._channels, name, self, self._ctx)
            self._layout.insertWidget(position, indicator)
            self.indicators[name] = indicator
