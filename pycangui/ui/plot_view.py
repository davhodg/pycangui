"""Plot pane: rolling time plot of signals selected in the Signals pane.

pyqtgraph redraws only on a 50 ms timer, pulling the visible window out of
the hub's history, so the cost is independent of the incoming sample rate.
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

        pg.setConfigOptions(antialias=False, background="w", foreground="k")
        self.plot = pg.PlotWidget()
        self.plot.showGrid(x=True, y=True, alpha=0.3)
        self.plot.setLabel("bottom", "time", units="s")
        self.plot.addLegend(offset=(10, 10))

        self.window_s = QDoubleSpinBox()
        self.window_s.setRange(0.5, 3600)
        self.window_s.setValue(10)
        self.window_s.setSuffix(" s")
        self.pause = QCheckBox("Pause")
        self.pause.setToolTip("Hold the plot still.  Samples carry on being collected.")
        self.follow = QCheckBox("Follow")
        self.follow.setToolTip(
            "Keep the newest samples in view as they arrive.\n\n"
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

    @Slot(str, bool)
    def set_plotted(self, key: str, on: bool) -> None:
        if on and key not in self._curves:
            s = self.hub.get(key)
            pen = pg.mkPen(COLOURS[len(self._curves) % len(COLOURS)], width=1.5)
            label = f"{s.name} [{s.unit}]" if s and s.unit else (s.name if s else key)
            curve = self.plot.plot([], [], pen=pen, name=label)
            # An imported file can be a million points, and pyqtgraph drawing
            # every one of them into eight hundred pixels is time spent to no
            # visible effect.  Peak downsampling keeps the spikes, which are
            # the part somebody is looking for.
            curve.setDownsampling(auto=True, method="peak")
            curve.setClipToView(True)
            self._curves[key] = curve
        elif not on and key in self._curves:
            self.plot.removeItem(self._curves.pop(key))

    def plotted(self) -> list[str]:
        return list(self._curves)

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

    def _redraw(self) -> None:
        if self.pause.isChecked() or not self._curves or not self.isVisible():
            return
        following = self.follow.isChecked()
        now = self._now()
        # Following, only the last few seconds are wanted and slicing to them
        # is most of what makes a live plot cheap.  Not following, everything
        # is wanted: an imported file lies outside any window measured back
        # from now, and slicing to one would show nothing and look empty.
        t_from = now - self.window_s.value() if following else float("-inf")
        for key, curve in self._curves.items():
            s = self.hub.get(key)
            if s is None:
                continue
            ts, vs = s.window(t_from)
            curve.setData(ts, vs)
        if following:
            self.plot.setXRange(t_from, now, padding=0)
