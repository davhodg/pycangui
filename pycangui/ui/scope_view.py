# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Signals and Plot in one pane.

The two belong together: the checkbox that plots a signal lives in the signal
list, so as separate docks Qt would happily tab them and hide the list behind
the plot -- ticking a signal and watching the trace it draws would mean
switching tabs.  In one pane with a splitter between them, both are always
visible, and either can be dragged shut when only the other is wanted: a full
width plot, or a plain watch window of live values.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QSplitter, QVBoxLayout, QWidget

from pycangui.core.context import Context
from pycangui.core.signals import SignalHub
from pycangui.ui.plot_view import PlotView
from pycangui.ui.signals_view import SignalsView

# Enough for a signal name and its value; the plot takes whatever is left.
LIST_WIDTH = 320


class ScopeView(QWidget):
    def __init__(self, hub: SignalHub, now: callable, ctx: Context | None = None) -> None:
        super().__init__()
        self.signals_view = SignalsView(hub)
        self.plot = PlotView(hub, now, ctx)
        self.signals_view.plot_toggled.connect(self.plot.set_plotted)

        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.addWidget(self.signals_view)
        self.splitter.addWidget(self.plot)
        # Either side may be dragged shut, and neither imposes a width of its
        # own, so the splitter shrinks rather than the pane growing scrollbars.
        self.splitter.setChildrenCollapsible(True)
        self.splitter.setHandleWidth(6)
        for i, widget in enumerate((self.signals_view, self.plot)):
            widget.setMinimumWidth(0)
            self.splitter.setStretchFactor(i, i)  # the plot takes the extra room
        self.splitter.setSizes([LIST_WIDTH, 2 * LIST_WIDTH])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.splitter)

    # --- layout, saved with the rest of the window state ---------------------
    def save_state(self):
        return self.splitter.saveState()

    def restore_state(self, state) -> None:
        if state is not None:
            self.splitter.restoreState(state)
