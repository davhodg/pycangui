# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Plot pane: rolling time plot of signals selected in the Signals pane.

pyqtgraph redraws only on a 50 ms timer, pulling the visible window out of
the hub's history, so the cost is independent of the incoming sample rate.

A signal can be drawn against a second Y axis on the right, with a scale of
its own: a speed in thousands and a temperature in tens share one axis badly,
because the temperature becomes a flat line along the bottom.

An axis whose signals all have the same named values is marked with the
names -- *Off*, *Run*, *Fault* -- in place of 0, 1 and 2.

Two cursors, A and B, can be put on the plot and dragged: the time between
them is on the plot's top row, and each signal's value at each, the change
between them and what it did in the stretch between them are columns in the
signal list beside or below (see signals_view.py), where a screen reader can
reach them and nothing is drawn over the curves.
"""

from __future__ import annotations

import math
from bisect import bisect_left, bisect_right

import pyqtgraph as pg
from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QAction, QActionGroup, QFontMetrics
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from pycangui.core.context import Context
from pycangui.core.signals import SignalHub
from pycangui.ui import plot_colours
from pycangui.ui.persist import remember
from pycangui.ui.refresh import FAST_MS, SLOW_MS, display_choice
from pycangui.ui.refresh import LABEL as SLOW_LABEL

DISPLAY_TIP = (
    "Only what this plot shows. Samples go on being collected, a recording\n"
    "goes on recording and the other panes go on updating, whatever is\n"
    "chosen here: pausing the plot does not pause the data.\n"
    "\n"
    "Live redraws twenty times a second.\n"
    f"{SLOW_LABEL} redraws four times a second, for data changing so fast\n"
    "that the curve is a shimmer.\n"
    "Paused holds the plot still, to look at; what arrives meanwhile is\n"
    "collected, and there when it is back to Live refresh or Slow refresh."
)
#: Each choice's own tooltip, for when it is hovered in the list.
DISPLAY_ITEM_TIPS = (
    "The plot redraws twenty times a second.",
    "The plot redraws four times a second. Display only: every sample is\n"
    "still collected as it arrives.",
    "The plot holds still. Display only: samples are still collected, and\n"
    "drawn when it is back to Live refresh or Slow refresh.",
)
#: Which stretch of time is on screen: one question, which used to be a
#: box, a tick and a button.
FOLLOW, ALL, MANUAL = "Follow", "All", "Manual"
TIME_CHOICES = (FOLLOW, ALL, MANUAL)
TIME_TIP = (
    "Which stretch of time the plot shows.\n"
    "\n"
    "Follow keeps the newest samples in view, the last so many seconds.\n"
    "All shows everything held for the plotted signals, and goes on\n"
    "showing all of it as more arrives.\n"
    "Manual leaves the plot where it is: drag or zoom it and that is where\n"
    "it stays."
)
TIME_ITEM_TIPS = (
    "Keep the newest samples in view, for the number of seconds beside\n"
    "this. It follows the data, so a quiet bus holds still rather than\n"
    "scrolling the trace off the edge.",
    "Everything held for the plotted signals, wherever in time it is --\n"
    "which is how to find data imported from a file, sitting at its own\n"
    "times. Kept in view as more arrives. Choose it again to fit again.",
    "The plot stays where you put it. Dragging or zooming the plot\nchooses this by itself.",
)
WINDOW_TIP = "How many seconds Follow keeps in view."
CURSORS_TEXT = "Cursors"
CURSORS_TIP = (
    "Two cursors, A and B, to drag along the time axis. The time between them\n"
    "is shown here; each signal's value at each, the change between them, and\n"
    "its lowest, highest and mean between them are columns in the signal list.\n"
    "They stay at the times they are put, so in Follow they scroll off.\n"
    "\n"
    "From the keyboard, with the plot focused (Tab to it): A or B picks a\n"
    "cursor, Left and Right move it a hundredth of the time in view, with\n"
    "Shift a tenth, and with Ctrl to the previous or next sample."
)
PLOT_KEYS_DESCRIPTION = (
    "With the cursors on: A or B picks a cursor; Left and Right move it a hundredth "
    "of the time in view, Shift with them a tenth, Ctrl with them to the previous "
    "or next sample of a plotted signal."
)
#: How far Left and Right move a cursor, as shares of the time in view.
CURSOR_STEP, CURSOR_BIG_STEP = 1 / 100, 1 / 10
#: The cursor being moved from the keyboard is drawn solid, the other dashed,
#: so which one the keys move can be seen.
CURSOR_PENS = {True: (Qt.SolidLine, 2.0), False: (Qt.DashLine, 1.5)}
#: Where the cursors start, as shares of the time in view.
CURSOR_STARTS = (1 / 3, 2 / 3)
CURSOR_NAMES = ("A", "B")
#: How long a dragged cursor waits before the list works the stretch out
#: again: a drag moves it many times a second, and once a pause is enough.
CURSOR_SETTLE_MS = 60
NAMES_TEXT = "Value names on axis"
NAMES_TIP = (
    "Mark a Y axis with the names of its values -- Off, Run, Fault --\n"
    "where every signal on it has the same ones. An axis shared with a\n"
    "signal that has none keeps its numbers: put the named signal on the\n"
    "other axis, with Plot Y2, to give it one of its own."
)
LEFT, RIGHT = "left", "right"
VIEW_TEXT = "View"
VIEW_TIP = "How the pane is laid out, and what colours the plot is drawn in."


#: Where the marks on the time axis may fall once it shows more than a
#: minute, in seconds, each with the smaller step drawn between: whole
#: seconds, then whole minutes, then whole hours, as a clock is read.
TIME_STEPS = (
    (1, 0.5),
    (2, 1),
    (5, 1),
    (10, 5),
    (15, 5),
    (30, 10),
    (60, 30),
    (120, 60),
    (300, 60),
    (600, 300),
    (900, 300),
    (1800, 600),
    (3600, 1800),
    (7200, 3600),
    (14400, 3600),
    (21600, 7200),
    (43200, 21600),
    (86400, 21600),
)
#: About how far apart the marks are wanted, in pixels.
TIME_MARK_PX = 110


class TimeAxis(pg.AxisItem):
    """The plot's time axis: seconds, then minutes and seconds, then hours.

    pyqtgraph, told the axis is in seconds, scales it by thousands like any
    other unit, and a trace left running read *ks* along the bottom. Nobody
    thinks of twenty minutes as 1.2 kiloseconds. So under a minute it is
    seconds; past that it is written as a clock is, ``12:30`` and then
    ``1:02:30``, with the marks on whole minutes and whole hours rather than
    on round numbers of seconds.
    """

    def __init__(self, orientation: str = "bottom", **kwargs) -> None:
        super().__init__(orientation, **kwargs)
        self.enableAutoSIPrefix(False)
        self.setLabel("time")

    def tickSpacing(self, low, high, size):
        span = abs(high - low)
        if not math.isfinite(span) or span < 60 or size <= 0:
            return super().tickSpacing(low, high, size)  # seconds: tens and fives
        wanted = span / max(size / TIME_MARK_PX, 2)
        for major, minor in TIME_STEPS:
            if major >= wanted:
                return [(major, 0), (minor, 0)]
        days = math.ceil(wanted / 86400)
        return [(days * 86400, 0), (days * 21600, 0)]

    def tickStrings(self, values, scale, spacing):
        if not values:
            return []
        # Decimals for the seconds only where the marks are closer than one.
        places = max(0, math.ceil(-math.log10(spacing))) if 0 < spacing < 1 else 0
        clock = max(abs(v) for v in values) >= 60
        return [time_text(v, places, clock) for v in values]


def time_text(seconds: float, places: int = 0, clock: bool = True) -> str:
    """``12.5 s``, ``2:30`` or ``1:02:30``: a time on the plot's axis."""
    if not clock:
        return f"{seconds:.{places}f} s"
    sign = "-" if seconds < 0 else ""
    whole = round(abs(seconds), places)
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    width = places + 3 if places else 2
    tail = f"{secs:0{width}.{places}f}"
    if hours:
        return f"{sign}{int(hours)}:{int(minutes):02d}:{tail}"
    return f"{sign}{int(minutes)}:{tail}"


class PlotView(QWidget):
    #: *Export...* was pressed. The plot holds the samples but not the file
    #: dialogs or the Event Log, so whoever made it does the exporting.
    export_requested = Signal()
    #: The cursors' times, (A, B), as they are put and moved; None when they
    #: are turned off.
    cursors_changed = Signal(object)

    def __init__(self, hub: SignalHub, now: callable, ctx: Context | None = None) -> None:
        super().__init__()
        self.hub = hub
        self._now = now  # () -> seconds on the same clock as the hub samples
        self._curves: dict[str, pg.PlotDataItem] = {}
        #: The plotted signals drawn against the right hand axis. Always some
        #: of the keys of ``_curves``: a signal that is not plotted is on no axis.
        self._right: set[str] = set()
        #: What each Y axis is marked with in place of numbers: value ->
        #: name, and empty for an axis that shows numbers. See ``_show_names``.
        self._named: dict[str, dict[int, str]] = {LEFT: {}, RIGHT: {}}

        pg.setConfigOptions(antialias=False, background="w", foreground="k")
        self.plot = pg.PlotWidget(axisItems={"bottom": TimeAxis("bottom")})
        self.plot.setObjectName("plot")
        self.plot.setAccessibleName("Signal plot")
        self.plot.setAccessibleDescription(PLOT_KEYS_DESCRIPTION)
        # Focus shown, since the keys move the cursors only while it has it.
        # The border is there unfocused too, so focusing does not shift the plot.
        self.plot.setStyleSheet(
            "#plot { border: 2px solid transparent; }"
            "#plot:focus { border: 2px solid palette(highlight); }"
        )
        self.plot.installEventFilter(self)
        self.plot.showGrid(x=True, y=True, alpha=0.3)
        self.legend = self.plot.addLegend(offset=(10, 10))
        # A tick underneath, never shown, and an entry in the
        # plot's own right-click menu: a choice about the axes, made about
        # once, which is what that menu holds and what the bar has no room for.
        self.names = QCheckBox(NAMES_TEXT, self)
        self.names.hide()
        self.names.setChecked(True)
        self.names_action = QAction(NAMES_TEXT, self, checkable=True, checked=True)
        self.names_action.setToolTip(NAMES_TIP)
        self.names_action.toggled.connect(self.names.setChecked)
        self.names.toggled.connect(self._on_names)
        menu = self.plot.getViewBox().menu
        menu.setToolTipsVisible(True)
        menu.addSeparator()
        menu.addAction(self.names_action)
        # After the tick above: the axes ask it whether to show names.
        self._make_right_axis()

        self.window_s = QDoubleSpinBox()
        self.window_s.setRange(0.5, 3600)
        self.window_s.setValue(10)
        self.window_s.setSuffix(" s")
        self.window_s.setToolTip(WINDOW_TIP)
        # Two ticks, shown as one menu -- Live refresh, Slow refresh or Paused; see
        # refresh.display_choice.
        self.pause = QCheckBox("Pause", self)
        # A tick underneath, never shown: it is what is remembered between
        # runs and what the console and the tests set. The menu follows it.
        self.follow = QCheckBox("Follow", self)
        self.follow.hide()
        self.follow.setChecked(True)
        #: Showing everything, and keeping it all in view as more arrives.
        self._all = False
        self._all_range: tuple[float, float] | None = None
        self.time = QComboBox()
        self.time.addItems(TIME_CHOICES)
        self.time.setToolTip(TIME_TIP)
        for index, tip in enumerate(TIME_ITEM_TIPS):
            self.time.setItemData(index, tip, Qt.ToolTipRole)
        # activated, not currentIndexChanged: choosing All again fits again.
        self.time.activated.connect(self._on_time_chosen)
        self.follow.toggled.connect(self._on_follow)
        self.plot.getViewBox().sigRangeChangedManually.connect(self._on_dragged)
        self.slow = QCheckBox(SLOW_LABEL, self)
        self.slow.toggled.connect(self._on_slow)
        self.display = display_choice(self.pause, self.slow, DISPLAY_TIP, DISPLAY_ITEM_TIPS)
        # The two things done to the samples held, side by side: out to a
        # file, or thrown away. Export is for every signal, plotted or not.
        self.export = QPushButton("Export...")
        self.export.setToolTip(
            "Write the samples held for every decoded signal -- plotted or not --\n"
            "to a file a spreadsheet can read. The same as File > Export signals."
        )
        self.export.clicked.connect(self.export_requested)
        clear = QPushButton("Clear history")
        clear.setToolTip("Throw away the samples collected so far, for every signal")
        clear.clicked.connect(hub.clear)
        # A tick, worded: turned on and off often, so on the bar.
        self.cursors = QCheckBox(CURSORS_TEXT)
        self.cursors.installEventFilter(self)
        self._update_cursor_indicator()
        self.cursors.setToolTip(CURSORS_TIP)
        self.cursors.setAccessibleName("Cursors on the plot")
        self.cursors.toggled.connect(self._on_cursors)
        self.cursor_times = QLabel("")
        self.cursor_times.setAccessibleName("Cursor times")
        self.cursor_times.setTextInteractionFlags(Qt.TextSelectableByMouse)
        # Choices made about once, so behind one worded button rather than
        # on the bar: the pane adds where its list goes, above the colours.
        self.view_menu = QMenu(self)
        self.view_menu.setToolTipsVisible(True)
        self.view_menu.addSection("Plot colours")
        self.colour_actions = QActionGroup(self)
        for choice, text in plot_colours.CHOICES:
            action = self.view_menu.addAction(text)
            action.setCheckable(True)
            action.setData(choice)
            action.setToolTip(plot_colours.TIP)
            self.colour_actions.addAction(action)
        self.colour_actions.triggered.connect(lambda a: plot_colours.choose(a.data()))
        self.view_button = QToolButton()
        self.view_button.setText(VIEW_TEXT)
        self.view_button.setToolTip(VIEW_TIP)
        self.view_button.setAccessibleName("View choices")
        self.view_button.setPopupMode(QToolButton.InstantPopup)
        self.view_button.setMenu(self.view_menu)
        self.view_menu.aboutToShow.connect(self._show_colour_choice)
        self._lines: list[pg.InfiniteLine] = []
        #: Which cursor the keys move: 0 for A, 1 for B.
        self._active = 0
        self._place_cursors = False
        self._cursors_moved = QTimer(
            self, singleShot=True, interval=CURSOR_SETTLE_MS, timeout=self._say_cursors
        )
        bar = QHBoxLayout()
        bar.addWidget(self.display)
        bar.addWidget(QLabel("Time:"))
        bar.addWidget(self.time)
        bar.addWidget(self.window_s)
        bar.addWidget(self.cursors)
        bar.addWidget(self.cursor_times)
        bar.addStretch()
        bar.addWidget(self.view_button)
        bar.addWidget(self.export)
        bar.addWidget(clear)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.addLayout(bar)
        layout.addWidget(self.plot)

        # Before the settings are restored, not after: a saved Slow refresh
        # sets the box from inside this constructor, and _on_slow reaches
        # for the timer the moment it does.
        self._timer = QTimer(self, interval=FAST_MS, timeout=self._redraw)
        self._timer.start()

        if ctx is not None:
            # Not Pause: an application that started up paused, showing a
            # frozen plot of a live bus, would be reported as a bug.
            remember(ctx, "plot.window_s", self.window_s)
            remember(ctx, "plot.follow", self.follow)
            # Unlike Pause, this one is restored: a plot that comes back
            # drawing four times a second is still a live plot.
            remember(ctx, "plot.slow", self.slow)
            remember(ctx, "plot.value_names", self.names)
            # Whether they are on; not where, which is a bus time from one connection.
            remember(ctx, "plot.cursors", self.cursors)
        self._show_time()
        #: Which of the scheme's colours each curve has: kept, so a curve keeps
        #: its place in the order when the colours change.
        self._colour_of: dict[str, int] = {}
        self._scheme = plot_colours.scheme_for(self)
        plot_colours.Follower(self, self.repaint_colours)
        self.repaint_colours()

    # --- colours --------------------------------------------------------------------------
    @property
    def scheme(self) -> plot_colours.Scheme:
        return self._scheme

    def repaint_colours(self) -> None:
        """Draw everything in the colours chosen, as they now are."""
        self._scheme = scheme = plot_colours.scheme_for(self)
        plot_colours.paint(self.plot, scheme)
        for key, curve in self._curves.items():
            curve.setPen(pg.mkPen(scheme.curve(self._colour_of.get(key, 0)), width=1.5))
        for line in self._lines:
            line.setHoverPen(pg.mkPen(scheme.cursor_hover, width=2.5))
            line.label.setColor(scheme.cursor_label)
            line.label.fill = pg.mkBrush(scheme.cursor_label_fill)
            line.label.update()
        self.set_active_cursor(self._active)

    def _show_colour_choice(self) -> None:
        choice = plot_colours.chosen()
        for action in self.colour_actions.actions():
            action.setChecked(action.data() == choice)

    # --- which stretch of time ------------------------------------------------------------
    def _on_time_chosen(self, index: int) -> None:
        choice = TIME_CHOICES[index]
        if choice == FOLLOW:
            self.follow.setChecked(True)
        elif choice == ALL:
            self._fit()
        else:
            self._all = False
            self.follow.setChecked(False)
        self._show_time()

    @Slot(bool)
    def _on_follow(self, on: bool) -> None:
        if on:
            self._all = False
        self._show_time()

    def _on_dragged(self, *_args) -> None:
        """Dragged or zoomed by hand: that is where it stays."""
        self._all = False
        self.follow.setChecked(False)
        self._show_time()

    def time_choice(self) -> str:
        if self.follow.isChecked():
            return FOLLOW
        return ALL if self._all else MANUAL

    def _show_time(self) -> None:
        choice = self.time_choice()
        self.time.setCurrentIndex(TIME_CHOICES.index(choice))
        self.window_s.setEnabled(choice == FOLLOW)  # the seconds are Follow's

    @Slot(bool)
    def _on_slow(self, slow: bool) -> None:
        self._timer.setInterval(SLOW_MS if slow else FAST_MS)

    # --- the cursors ----------------------------------------------------------------------
    @Slot(bool)
    def _on_cursors(self, on: bool) -> None:
        for line in self._lines:
            self.plot.removeItem(line)
        self._lines = []
        self._place_cursors = False
        if not on:
            self.cursor_times.setText("")
            self.cursors_changed.emit(None)
            return
        for name in CURSOR_NAMES:
            line = pg.InfiniteLine(
                angle=90,
                movable=True,
                hoverPen=pg.mkPen(self._scheme.cursor_hover, width=2.5),
                label=name,
                labelOpts={
                    "position": 0.95,
                    "color": self._scheme.cursor_label,
                    "fill": self._scheme.cursor_label_fill,
                },
            )
            line.sigPositionChanged.connect(self._on_cursor_moved)
            # The one dragged last is the one the keys go on with.
            line.sigDragged.connect(lambda _line, i=len(self._lines): self.set_active_cursor(i))
            self.plot.addItem(line, ignoreBounds=True)
            self._lines.append(line)
        self.set_active_cursor(self._active)
        # Where the data is, if there is any yet: a plot restored with its
        # cursors on has no time range worth a third of until it has drawn.
        if self._curves and any(len(s.times) for k in self._curves if (s := self.hub.get(k))):
            self._put_cursors()
        else:
            self._place_cursors = True

    def _put_cursors(self) -> None:
        """At a third and two thirds of the time in view."""
        low, high = self.plot.getViewBox().viewRange()[0]
        for line, share in zip(self._lines, CURSOR_STARTS, strict=True):
            line.blockSignals(True)
            line.setValue(low + share * (high - low))
            line.blockSignals(False)
        self._place_cursors = False
        self._say_cursors()

    def cursor_positions(self) -> tuple[float, float] | None:
        """The cursors' times, (A, B), or None while they are off."""
        if len(self._lines) != 2:
            return None
        return float(self._lines[0].value()), float(self._lines[1].value())

    def set_cursor_positions(self, a: float, b: float) -> None:
        """Put the cursors at these times, for the console and the tests."""
        if not self.cursors.isChecked():
            self.cursors.setChecked(True)
        for line, t in zip(self._lines, (a, b), strict=True):
            line.blockSignals(True)
            line.setValue(t)
            line.blockSignals(False)
        self._place_cursors = False
        self._say_cursors()

    @property
    def active_cursor(self) -> int:
        """Which cursor the keys move: 0 for A, 1 for B."""
        return self._active

    def set_active_cursor(self, which: int) -> None:
        self._active = which
        for i, line in enumerate(self._lines):
            style, width = CURSOR_PENS[i == which]
            line.setPen(pg.mkPen(self._scheme.cursor, width=width, style=style))

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.cursors and event.type() in (
            QEvent.FontChange,
            QEvent.ApplicationFontChange,
        ):
            self._update_cursor_indicator()
        if watched is self.plot and event.type() == QEvent.KeyPress and self._lines:
            return self._cursor_key(event)
        return super().eventFilter(watched, event)

    def _update_cursor_indicator(self) -> None:
        indicator = QFontMetrics(self.cursors.font()).height()
        self.cursors.setStyleSheet(
            f"QCheckBox::indicator {{ width: {indicator}px; height: {indicator}px; }}"
        )

    def _cursor_key(self, event) -> bool:
        """Pick or move a cursor; False for a key that is not about them."""
        key, mods = event.key(), event.modifiers()
        if key in (Qt.Key_A, Qt.Key_B) and not mods & ~Qt.KeypadModifier:
            self.set_active_cursor(0 if key == Qt.Key_A else 1)
            return True
        if key not in (Qt.Key_Left, Qt.Key_Right):
            return False
        ahead = key == Qt.Key_Right
        line = self._lines[self._active]
        t = float(line.value())
        if mods & Qt.ControlModifier:
            to = self._next_sample(t, ahead)
            if to is None:
                return True  # nothing further that way: the key is still spent
        else:
            low, high = self.plot.getViewBox().viewRange()[0]
            share = CURSOR_BIG_STEP if mods & Qt.ShiftModifier else CURSOR_STEP
            to = t + (share if ahead else -share) * (high - low)
        line.setValue(to)  # sigPositionChanged shows it and tells the list
        return True

    def _next_sample(self, t: float, ahead: bool) -> float | None:
        """The nearest sample time past ``t`` that way, over every plotted signal."""
        found = []
        for key in self._curves:
            s = self.hub.get(key)
            if s is None or not len(s.times):
                continue
            if ahead:
                i = bisect_right(s.times, t)
                if i < len(s.times):
                    found.append(s.times[i])
            else:
                i = bisect_left(s.times, t) - 1
                if i >= 0:
                    found.append(s.times[i])
        if not found:
            return None
        return min(found) if ahead else max(found)

    def _on_cursor_moved(self, *_args) -> None:
        self._show_cursor_times()
        self._cursors_moved.start()

    def _say_cursors(self) -> None:
        self._show_cursor_times()
        self.cursors_changed.emit(self.cursor_positions())

    def _show_cursor_times(self) -> None:
        where = self.cursor_positions()
        if where is None:
            self.cursor_times.setText("")
            return
        a, b = where
        clock = max(abs(a), abs(b)) >= 60
        self.cursor_times.setText(
            f"A {time_text(a, 3, clock)}   B {time_text(b, 3, clock)}   "
            f"B - A {time_text(b - a, 3, abs(b - a) >= 60)}"
        )

    # --- the right hand axis --------------------------------------------------------------
    def _make_right_axis(self) -> None:
        """A second view box for the right hand axis, the way pyqtgraph does it.

        A view box has one Y scale, so a second scale needs a second box: laid
        exactly over the plot's own, sharing its X range through a link, and
        drawn against the right axis. Nothing in pyqtgraph's layout sizes a
        box added like this, so it is kept the same size as the plot's own by
        hand, whenever that one is resized.

        Put behind the plot rather than on top of it. The two boxes cover the
        same area and whichever is on top takes the mouse: on top, dragging
        and scrolling in the plot would scale the right axis and leave the left
        one, and the legend, out of reach. Behind, the plot answers the mouse
        exactly as it did before there was a second axis.
        """
        item = self.plot.getPlotItem()
        self.right_view = pg.ViewBox()
        # X comes from the plot, through the link. An automatic X range of its
        # own as well would have the two boxes arguing over it on every redraw.
        self.right_view.enableAutoRange(axis=pg.ViewBox.XAxis, enable=False)
        self.right_view.setZValue(-1)
        item.scene().addItem(self.right_view)
        item.getAxis("right").linkToView(self.right_view)
        self.right_view.setXLink(item)
        item.getViewBox().sigResized.connect(self._match_right_view)
        self._match_right_view()
        self._show_axes()

    def _match_right_view(self) -> None:
        main = self.plot.getViewBox()
        # Its own rectangle rather than its bounding one, which is half a pixel
        # wider for the pen and would leave the two scales slightly apart.
        self.right_view.setGeometry(main.mapRectToScene(main.rect()))
        # The link lines the two X ranges up by where the boxes are on screen,
        # so a box that has just been moved has to line up again.
        self.right_view.linkedViewChanged(main, pg.ViewBox.XAxis)

    def _show_axes(self) -> None:
        """The right axis only while something is on it, and each axis labelled."""
        on = bool(self._right)
        self.plot.showAxis("right", on)
        self.right_view.setVisible(on)
        left = [key for key in self._curves if key not in self._right]
        # As text rather than as pyqtgraph's units, which would put an SI prefix
        # on whatever it is given and label a speed in "krpm".
        self.plot.getAxis("left").setLabel(self._common_unit(left))
        self.plot.getAxis("right").setLabel(self._common_unit(self._right))
        self._show_names()

    def _common_unit(self, keys) -> str:
        """The unit all of these signals are in, or nothing if they differ.

        Signals in different units on one axis leave it no unit it can honestly
        be labelled with, and a signal with no unit at all is a difference too.
        """
        units = {s.unit if (s := self.hub.get(key)) else "" for key in keys}
        return units.pop() if len(units) == 1 else ""

    # --- names for values -----------------------------------------------------------------
    @Slot(bool)
    def _on_names(self, on: bool) -> None:
        self.names_action.setChecked(on)
        self._show_names()

    def _common_names(self, keys) -> dict[int, str]:
        """The named values all of these signals share, or none if they differ.

        The same rule as the unit, for the same reason: an axis has one set
        of marks, and *Run* beside 1 is only true of a signal that calls 1
        that. One signal with no names, or with other ones, and the axis
        has nothing it can honestly be marked with but numbers.
        """
        tables = [s.choices if (s := self.hub.get(key)) else {} for key in keys]
        if not tables or not tables[0] or any(table != tables[0] for table in tables):
            return {}
        return dict(tables[0])

    def _show_names(self) -> None:
        """Mark each Y axis with names or with numbers, where that has changed.

        Asked on every redraw as well as when the plotted signals change: a
        signal's names can arrive after it is plotted -- they come with the
        first frame decoded, and a plot that is restored is there before it.
        So it is a comparison, and the axis is only touched when they differ.
        """
        on = self.names.isChecked()
        left = [key for key in self._curves if key not in self._right]
        for side, keys in ((LEFT, left), (RIGHT, self.right_axis())):
            wanted = self._common_names(keys) if on else {}
            if wanted == self._named[side]:
                continue
            self._named[side] = wanted
            marks = [(float(value), name) for value, name in sorted(wanted.items())]
            # None gives the axis back its own numbers.
            self.plot.getAxis(side).setTicks([marks] if marks else None)

    def axis_names(self, side: str = LEFT) -> dict[int, str]:
        """What a Y axis is marked with in place of numbers; empty if it shows numbers."""
        return dict(self._named[side])

    def _attach(self, key: str, curve: pg.PlotDataItem) -> None:
        """Put a curve in the view box for its axis, with its legend entry."""
        # Off while it is being added. A curve that holds data and clips to
        # its view asks for that view the moment it enters the scene, before
        # it has been given one, and pyqtgraph then asks the plot widget
        # instead -- which fails, leaves the curve half added, and fails again
        # on every redraw. A new curve is empty, so only moving one between
        # the axes ran into it.
        curve.setClipToView(False)
        curve.setDownsampling(auto=False)
        if key in self._right:
            self.right_view.addItem(curve)
            # The legend belongs to the plot, and the plot only makes entries
            # for what is added to the plot, so this one is made here. Marked,
            # because two scales share one legend.
            self.legend.addItem(curve, f"{curve.name()} (Y2)")
        else:
            self.plot.addItem(curve)
        # After adding rather than before: the plot applies its own settings
        # for these to whatever is added to it, and would undo them.
        #
        # An imported file can be a million points, and pyqtgraph drawing
        # every one of them into eight hundred pixels is time spent to no
        # visible effect. Peak downsampling keeps the spikes, which are
        # the part somebody is looking for.
        curve.setDownsampling(auto=True, method="peak")
        curve.setClipToView(True)

    def _detach(self, key: str, curve: pg.PlotDataItem) -> None:
        if key in self._right:
            self.right_view.removeItem(curve)
            self.legend.removeItem(curve)
        else:
            self.plot.removeItem(curve)  # and its legend entry with it

    # --- what is plotted ------------------------------------------------------------------
    @Slot(str, bool)
    def set_plotted(self, key: str, on: bool) -> None:
        if on and key not in self._curves:
            s = self.hub.get(key)
            self._colour_of[key] = colour = len(self._curves)
            pen = pg.mkPen(self._scheme.curve(colour), width=1.5)
            label = f"{s.name} [{s.unit}]" if s and s.unit else (s.name if s else key)
            curve = pg.PlotDataItem([], [], pen=pen, name=label)
            self._curves[key] = curve
            self._attach(key, curve)
        elif not on and key in self._curves:
            self._detach(key, self._curves.pop(key))
            self._colour_of.pop(key, None)
            self._right.discard(key)
        self._show_axes()

    @Slot(str, bool)
    def set_right(self, key: str, on: bool) -> None:
        """Draw a plotted signal against the right hand axis, or back on the left.

        The same curve moves from one view box to the other, so it keeps its
        colour. Putting a signal that is not plotted on the right plots it,
        which is what ticking the box in the list does as well.
        """
        if on and key not in self._curves:
            self.set_plotted(key, True)
        curve = self._curves.get(key)
        if curve is None or on == (key in self._right):
            return
        self._detach(key, curve)
        if on:
            self._right.add(key)
        else:
            self._right.discard(key)
        self._attach(key, curve)
        self._show_axes()

    def plotted(self) -> list[str]:
        return list(self._curves)

    def right_axis(self) -> list[str]:
        """The plotted signals on the right hand axis, in the order they were plotted."""
        return [key for key in self._curves if key in self._right]

    def _fit(self) -> None:
        """Show everything that is plotted, wherever in time it happens to be.

        The way to find imported data. A file recorded yesterday, or one
        exported from 235 s into a run, sits nowhere near the clock this
        window is counting on, and hunting for it by dragging is no way to
        find anything.
        """
        self.follow.setChecked(False)
        # Redrawn first. The curves are filled on a timer, so fitting before
        # the next tick would fit whatever was on screen a moment ago -- and
        # for a file just imported, that is nothing at all. Paused or not:
        # Fit is asked for, and a pause is about what arrives by itself.
        self._redraw(paused_too=True)
        # The time range from the samples themselves, not from the curves.
        # A curve is clipped to what is in view, so asking the plot to fit
        # its curves measured only what was already on screen, and each
        # press of Fit showed a little more than the last.
        self._all_range = None
        self._show_all()
        # Y after X, so each axis is fitted to all of what is now in view.
        self.plot.enableAutoRange(axis=pg.ViewBox.YAxis)
        if self._right:
            self.right_view.enableAutoRange(axis=pg.ViewBox.YAxis)
        self._all = True
        self._show_time()

    def _show_all(self) -> None:
        """Bring the time axis to everything held for the plotted signals."""
        ends = [
            t
            for key in self._curves
            if (s := self.hub.get(key)) and len(s.times)
            for t in (s.times[0], s.times[-1])
        ]
        if not ends:
            return
        wanted = (min(ends), max(ends))
        if wanted != self._all_range:  # only when there is more to show
            self._all_range = wanted
            self.plot.setXRange(*wanted)

    def _newest(self) -> float | None:
        """The latest timestamp across the plotted signals, or None if none."""
        times = [s.times[-1] for key in self._curves if (s := self.hub.get(key)) and s.times]
        return max(times) if times else None

    def _edge(self) -> float:
        """Where the right hand edge goes while following.

        The newest sample rather than the clock. ``now()`` runs off
        ``time.monotonic`` and advances whether or not a bus is open or a
        single frame has arrived, so following it made the axis march left
        for ever with the data standing still and sliding off the edge --
        during a quiet trace, and on after disconnecting. Following the data
        stops when the data stops, which is also the right answer for a
        connected but idle bus: a flat line marching left says nothing that a
        stopped plot does not.

        Never later than the clock, though. A file imported at its own times
        -- last Tuesday, or an epoch stamp -- would otherwise drag the window
        off to wherever it was recorded and take the live trace off screen.
        Finding imported data is what *Fit* is for.
        """
        now = self._now()
        newest = self._newest()
        return min(newest, now) if newest is not None else now

    def _redraw(self, paused_too: bool = False) -> None:
        if (self.pause.isChecked() and not paused_too) or not self._curves or not self.isVisible():
            return
        self._show_names()
        following = self.follow.isChecked()
        edge = self._edge()
        # Following, only the last few seconds are wanted and slicing to them
        # is most of what makes a live plot cheap. Not following, everything
        # is wanted: an imported file lies outside any window measured back
        # from now, and slicing to one would show nothing and look empty.
        t_from = edge - self.window_s.value() if following else float("-inf")
        for key, curve in self._curves.items():
            s = self.hub.get(key)
            if s is None:
                continue
            ts, vs = s.window(t_from)
            curve.setData(ts, vs)
        if self._all and not following:
            self._show_all()  # All means all of it, as more arrives
        if following:
            # The right hand box is linked to this X range and follows it.
            self.plot.setXRange(t_from, edge, padding=0)
        if self._place_cursors:
            self._put_cursors()
