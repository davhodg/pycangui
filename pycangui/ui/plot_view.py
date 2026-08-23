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

from pycangui.core.signals import SignalHub

COLOURS = ("#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b", "#e377c2", "#17becf")


class PlotView(QWidget):
    def __init__(self, hub: SignalHub, now: callable) -> None:
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
        self.follow = QCheckBox("Follow")
        self.follow.setChecked(True)
        clear = QPushButton("Clear history")
        clear.clicked.connect(hub.clear)
        bar = QHBoxLayout()
        bar.addWidget(QLabel("Window:"))
        bar.addWidget(self.window_s)
        bar.addWidget(self.pause)
        bar.addWidget(self.follow)
        bar.addStretch()
        bar.addWidget(clear)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.addLayout(bar)
        layout.addWidget(self.plot)

        self._timer = QTimer(self, interval=50, timeout=self._redraw)
        self._timer.start()

    @Slot(str, bool)
    def set_plotted(self, key: str, on: bool) -> None:
        if on and key not in self._curves:
            s = self.hub.get(key)
            pen = pg.mkPen(COLOURS[len(self._curves) % len(COLOURS)], width=1.5)
            label = f"{s.name} [{s.unit}]" if s and s.unit else (s.name if s else key)
            self._curves[key] = self.plot.plot([], [], pen=pen, name=label)
        elif not on and key in self._curves:
            self.plot.removeItem(self._curves.pop(key))

    def plotted(self) -> list[str]:
        return list(self._curves)

    def _redraw(self) -> None:
        if self.pause.isChecked() or not self._curves or not self.isVisible():
            return
        now = self._now()
        window = self.window_s.value()
        t_from = now - window
        for key, curve in self._curves.items():
            s = self.hub.get(key)
            if s is None:
                continue
            ts, vs = s.window(t_from)
            curve.setData(ts, vs)
        if self.follow.isChecked():
            self.plot.setXRange(t_from, now, padding=0)
