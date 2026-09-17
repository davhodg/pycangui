# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Plot pane: rolling time plot of signals selected in the Signals pane.

pyqtgraph redraws only on a 50 ms timer, pulling the visible window out of
the hub's history, so the cost is independent of the incoming sample rate.

A signal can be drawn against a second Y axis on the right, with a scale of
its own: a speed in thousands and a temperature in tens share one axis badly,
because the temperature becomes a flat line along the bottom.
"""

from __future__ import annotations

import pyqtgraph as pg
from PySide6.QtCore import QTimer, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pycangui.core.context import Context
from pycangui.core.signals import SignalHub
from pycangui.ui.persist import remember

COLOURS = ("#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b", "#e377c2", "#17becf")


class PlotView(QWidget):
    def __init__(self, hub: SignalHub, now: callable, ctx: Context | None = None) -> None:
        super().__init__()
        self.hub = hub
        self._now = now  # () -> seconds on the same clock as the hub samples
        self._curves: dict[str, pg.PlotDataItem] = {}
        #: The plotted signals drawn against the right hand axis.  Always some
        #: of the keys of ``_curves``: a signal that is not plotted is on no axis.
        self._right: set[str] = set()

        pg.setConfigOptions(antialias=False, background="w", foreground="k")
        self.plot = pg.PlotWidget()
        self.plot.showGrid(x=True, y=True, alpha=0.3)
        self.plot.setLabel("bottom", "time", units="s")
        self.legend = self.plot.addLegend(offset=(10, 10))
        self._make_right_axis()

        self.window_s = QDoubleSpinBox()
        self.window_s.setRange(0.5, 3600)
        self.window_s.setValue(10)
        self.window_s.setSuffix(" s")
        self.pause = QCheckBox("Pause")
        self.pause.setToolTip("Hold the plot still. Samples carry on being collected.")
        self.follow = QCheckBox("Follow")
        self.follow.setToolTip(
            "Keep the newest samples in view as they arrive. The plot\n"
            "follows the data, so it stops when the data does -- a quiet\n"
            "bus, or a disconnected one, holds still rather than scrolling\n"
            "the trace off the edge.\n\n"
            "Untick it to look at what is already there -- and to see data\n"
            "imported from a file at all, which sits at its own times rather\n"
            "than at the clock this window is running on."
        )
        self.follow.setChecked(True)
        self.fit = QPushButton("Fit")
        self.fit.setToolTip("Zoom to everything being plotted, wherever in time it is.")
        self.fit.clicked.connect(self._fit)
        clear = QPushButton("Clear history")
        clear.setToolTip("Throw away the samples collected so far, for every signal")
        clear.clicked.connect(hub.clear)
        bar = QHBoxLayout()
        bar.addWidget(QLabel("Window:"))
        bar.addWidget(self.window_s)
        bar.addWidget(self.pause)
        bar.addWidget(self.follow)
        bar.addWidget(self.fit)
        bar.addStretch()
        bar.addWidget(clear)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.addLayout(bar)
        layout.addWidget(self.plot)

        if ctx is not None:
            # Not Pause: an application that started up paused, showing a
            # frozen plot of a live bus, would be reported as a bug.
            remember(ctx, "plot.window_s", self.window_s)
            remember(ctx, "plot.follow", self.follow)

        self._timer = QTimer(self, interval=50, timeout=self._redraw)
        self._timer.start()

    # --- the right hand axis --------------------------------------------------------------
    def _make_right_axis(self) -> None:
        """A second view box for the right hand axis, the way pyqtgraph does it.

        A view box has one Y scale, so a second scale needs a second box: laid
        exactly over the plot's own, sharing its X range through a link, and
        drawn against the right axis.  Nothing in pyqtgraph's layout sizes a
        box added like this, so it is kept the same size as the plot's own by
        hand, whenever that one is resized.

        Put behind the plot rather than on top of it.  The two boxes cover the
        same area and whichever is on top takes the mouse: on top, dragging
        and scrolling in the plot would scale the right axis and leave the left
        one, and the legend, out of reach.  Behind, the plot answers the mouse
        exactly as it did before there was a second axis.
        """
        item = self.plot.getPlotItem()
        self.right_view = pg.ViewBox()
        # X comes from the plot, through the link.  An automatic X range of its
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

    def _common_unit(self, keys) -> str:
        """The unit all of these signals are in, or nothing if they differ.

        Signals in different units on one axis leave it no unit it can honestly
        be labelled with, and a signal with no unit at all is a difference too.
        """
        units = {s.unit if (s := self.hub.get(key)) else "" for key in keys}
        return units.pop() if len(units) == 1 else ""

    def _attach(self, key: str, curve: pg.PlotDataItem) -> None:
        """Put a curve in the view box for its axis, with its legend entry."""
        if key in self._right:
            self.right_view.addItem(curve)
            # The legend belongs to the plot, and the plot only makes entries
            # for what is added to the plot, so this one is made here.  Marked,
            # because two scales share one legend.
            self.legend.addItem(curve, f"{curve.name()} (Y2)")
        else:
            self.plot.addItem(curve)
        # After adding rather than before: the plot applies its own settings
        # for these to whatever is added to it, and would undo them.
        #
        # An imported file can be a million points, and pyqtgraph drawing
        # every one of them into eight hundred pixels is time spent to no
        # visible effect.  Peak downsampling keeps the spikes, which are
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
            pen = pg.mkPen(COLOURS[len(self._curves) % len(COLOURS)], width=1.5)
            label = f"{s.name} [{s.unit}]" if s and s.unit else (s.name if s else key)
            curve = pg.PlotDataItem([], [], pen=pen, name=label)
            self._curves[key] = curve
            self._attach(key, curve)
        elif not on and key in self._curves:
            self._detach(key, self._curves.pop(key))
            self._right.discard(key)
        self._show_axes()

    @Slot(str, bool)
    def set_right(self, key: str, on: bool) -> None:
        """Draw a plotted signal against the right hand axis, or back on the left.

        The same curve moves from one view box to the other, so it keeps its
        colour.  Putting a signal that is not plotted on the right plots it,
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

        The way to find imported data.  A file recorded yesterday, or one
        exported from 235 s into a run, sits nowhere near the clock this
        window is counting on, and hunting for it by dragging is no way to
        find anything.
        """
        self.follow.setChecked(False)
        # Redrawn first.  The curves are filled on a timer, so fitting before
        # the next tick would fit whatever was on screen a moment ago -- and
        # for a file just imported, that is nothing at all.
        self._redraw()
        self.plot.enableAutoRange()
        self.plot.autoRange()
        if not self._right:
            return
        self.right_view.enableAutoRange(axis=pg.ViewBox.YAxis)
        # The plot's own fit only knows about the curves in its own box, and
        # with every signal on the right that is none of them.  X is shared,
        # so it is fitted to both from the samples themselves.
        ends = [
            t
            for key in self._curves
            if (s := self.hub.get(key)) and len(s.times)
            for t in (s.times[0], s.times[-1])
        ]
        if ends:
            self.plot.setXRange(min(ends), max(ends))

    def _newest(self) -> float | None:
        """The latest timestamp across the plotted signals, or None if none."""
        times = [s.times[-1] for key in self._curves if (s := self.hub.get(key)) and s.times]
        return max(times) if times else None

    def _edge(self) -> float:
        """Where the right hand edge goes while following.

        The newest sample rather than the clock.  ``now()`` runs off
        ``time.monotonic`` and advances whether or not a bus is open or a
        single frame has arrived, so following it made the axis march left
        for ever with the data standing still and sliding off the edge --
        during a quiet trace, and on after disconnecting.  Following the data
        stops when the data stops, which is also the right answer for a
        connected but idle bus: a flat line marching left says nothing that a
        stopped plot does not.

        Never later than the clock, though.  A file imported at its own times
        -- last Tuesday, or an epoch stamp -- would otherwise drag the window
        off to wherever it was recorded and take the live trace off screen.
        Finding imported data is what *Fit* is for.
        """
        now = self._now()
        newest = self._newest()
        return min(newest, now) if newest is not None else now

    def _redraw(self) -> None:
        if self.pause.isChecked() or not self._curves or not self.isVisible():
            return
        following = self.follow.isChecked()
        edge = self._edge()
        # Following, only the last few seconds are wanted and slicing to them
        # is most of what makes a live plot cheap.  Not following, everything
        # is wanted: an imported file lies outside any window measured back
        # from now, and slicing to one would show nothing and look empty.
        t_from = edge - self.window_s.value() if following else float("-inf")
        for key, curve in self._curves.items():
            s = self.hub.get(key)
            if s is None:
                continue
            ts, vs = s.window(t_from)
            curve.setData(ts, vs)
        if following:
            # The right hand box is linked to this X range and follows it.
            self.plot.setXRange(t_from, edge, padding=0)
