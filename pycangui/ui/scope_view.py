# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Signals and Plot in one pane.

The two belong together: the checkbox that plots a signal lives in the signal
list, so as separate docks Qt would happily tab them and hide the list behind
the plot -- ticking a signal and watching the trace it draws would mean
switching tabs. In one pane with a splitter between them, both are always
visible, and either can be dragged shut when only the other is wanted: a full
width plot, or a plain watch window of live values.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import QSplitter, QVBoxLayout, QWidget

from pycangui.core.context import Context
from pycangui.core.signals import SignalHub
from pycangui.ui.plot_view import PlotView
from pycangui.ui.signals_view import SignalsView

# Enough for a signal name and its value; the plot takes whatever is left.
LIST_WIDTH = 320


def _saved_keys(saved) -> list[str]:
    """A saved list of signal keys, or an empty one for anything else.

    Anything else being settings from before the list existed, or a
    hand-edited ``settings.json``.
    """
    return [str(key) for key in saved] if isinstance(saved, list) else []


class ScopeView(QWidget):
    def __init__(
        self, hub: SignalHub, now: callable, ctx: Context | None = None, key: str = "scope"
    ) -> None:
        super().__init__()
        self.hub = hub
        self.signals_view = SignalsView(hub)
        self.plot = PlotView(hub, now, ctx)
        self.signals_view.plot_toggled.connect(self.plot.set_plotted)
        self.signals_view.axis_toggled.connect(self.plot.set_right)

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

        if ctx is not None:
            self._remember_plotted(ctx, key)

    # --- what is plotted, and on which axis, kept between runs --------------------------
    def _remember_plotted(self, ctx: Context, key: str) -> None:
        """Plot again what this pane was plotting when pycangui last closed.

        Kept under the pane's instance name, as a trace keeps its settings, so
        a second plot keeps its own signals rather than showing the first
        one's. Which of them are on the right hand axis is kept beside them.

        Signals are put back as they turn up rather than all at once, because
        at startup hardly any of them exist yet: a DBC signal is in the hub
        once a frame of it has been decoded, and an imported one once its file
        is imported again. Until then a signal stays on the saved list -- it
        is not forgotten for not having arrived yet.
        """
        self._settings = ctx.settings
        self._key = key
        self._wanted = _saved_keys(ctx.settings.get(f"{key}.plotted"))
        # Settings from before there was a right axis have no list, and so
        # put everything on the left, which is where it was.
        self._wanted_right = set(_saved_keys(ctx.settings.get(f"{key}.right_axis")))
        self.signals_view.plot_toggled.connect(self._note_plotted)
        self.signals_view.axis_toggled.connect(self._note_right)
        self.signals_view.all_unplotted.connect(self._forget_plotted)
        # Connected after the list's own, so the row is there to be ticked.
        self.hub.added.connect(self._restore)
        for signal in self.hub.keys():
            self._restore(signal)

    @Slot(str)
    def _restore(self, signal: str) -> None:
        if signal not in self._wanted:
            return
        self.signals_view.set_plotted(signal, True)
        self.plot.set_plotted(signal, True)
        if signal in self._wanted_right:
            self.signals_view.set_right(signal, True)
            self.plot.set_right(signal, True)

    @Slot(str, bool)
    def _note_plotted(self, signal: str, on: bool) -> None:
        if on and signal not in self._wanted:
            self._wanted.append(signal)
        elif not on:
            if signal in self._wanted:
                self._wanted.remove(signal)
            self._wanted_right.discard(signal)
        self._save_plotted()

    @Slot(str, bool)
    def _note_right(self, signal: str, on: bool) -> None:
        if on:
            self._wanted_right.add(signal)
        else:
            self._wanted_right.discard(signal)
        self._save_plotted()

    @Slot()
    def _forget_plotted(self) -> None:
        self._wanted.clear()
        self._wanted_right.clear()
        self._save_plotted()

    def _save_plotted(self) -> None:
        self._settings.set(f"{self._key}.plotted", list(self._wanted))
        right = [signal for signal in self._wanted if signal in self._wanted_right]
        self._settings.set(f"{self._key}.right_axis", right)

    # --- layout, saved with the rest of the window state ---------------------
    def save_state(self):
        return self.splitter.saveState()

    def restore_state(self, state) -> None:
        if state is not None:
            self.splitter.restoreState(state)
