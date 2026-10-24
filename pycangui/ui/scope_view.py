# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Signals and Plot in one pane.

The two belong together: the checkbox that plots a signal lives in the signal
list, so as separate docks Qt would happily tab them and hide the list behind
the plot -- ticking a signal and watching the trace it draws would mean
switching tabs. In one pane with a splitter between them, both are always
visible, and either can be dragged shut when only the other is wanted: a full
width plot, or a plain watch window of live values.

The list is beside the plot, or below it -- chosen on the list's right-click
and kept with the pane. Below gives the plot the whole width and the list room
for its statistics columns, which is what a pane undocked and made full screen
has the space for.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import QSplitter, QVBoxLayout, QWidget

from pycangui.core.context import Context
from pycangui.core.signals import SignalHub
from pycangui.ui.plot_view import PlotView
from pycangui.ui.signals_view import DEFAULT_COLUMNS, SignalsView

# Enough for a signal name and its value; the plot takes whatever is left.
LIST_WIDTH = 320
#: Below the plot, the share of the height the list starts with.
LIST_SHARE_BELOW = 1 / 3


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
        #: Where this pane's choices are kept: none for a pane made without a context.
        self._settings = None
        self._key = key
        self.signals_view = SignalsView(hub, ctx.settings if ctx is not None else None, now)
        self.plot = PlotView(hub, now, ctx)
        self.signals_view.plot_toggled.connect(self.plot.set_plotted)
        self.signals_view.axis_toggled.connect(self.plot.set_right)
        #: Passed on from the plot's Export button, for whoever made this pane.
        self.export_requested = self.plot.export_requested
        hub.removed.connect(self._on_removed)

        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.addWidget(self.signals_view)
        self.splitter.addWidget(self.plot)
        # Either side may be dragged shut, and neither imposes a size of its
        # own, so the splitter shrinks rather than the pane growing scrollbars.
        self.splitter.setChildrenCollapsible(True)
        self.splitter.setHandleWidth(6)
        for widget in (self.signals_view, self.plot):
            widget.setMinimumSize(0, 0)
        self._arrange(below=False)
        self.signals_view.below_wanted.connect(self.set_list_below)
        self.plot.cursors_changed.connect(self.signals_view.set_cursors)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.splitter)

        if ctx is not None:
            self._remember_plotted(ctx, key)

    @Slot(list)
    def _on_removed(self, keys: list) -> None:
        """Take forgotten signals off the plot.

        Straight to the plot rather than by unticking the list, so they stay
        on the list of what to plot: a database removed and loaded again puts
        back what was plotted from it, as a restart does.
        """
        for key in keys:
            self.plot.set_plotted(key, False)

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
        if ctx.settings.get(f"{key}.list_below", False) is True:
            self._arrange(below=True)
        self._wanted = _saved_keys(ctx.settings.get(f"{key}.plotted"))
        # Settings from before there was a right axis have no list, and so
        # put everything on the left, which is where it was.
        self._wanted_right = set(_saved_keys(ctx.settings.get(f"{key}.right_axis")))
        self.signals_view.plot_toggled.connect(self._note_plotted)
        self.signals_view.axis_toggled.connect(self._note_right)
        self.signals_view.all_unplotted.connect(self._forget_plotted)
        # Which statistics columns this pane shows, kept with what it plots.
        saved = ctx.settings.get(f"{key}.columns")
        self.signals_view.set_columns(DEFAULT_COLUMNS if saved is None else _saved_keys(saved))
        self.signals_view.columns_changed.connect(
            lambda names: ctx.settings.set(f"{key}.columns", list(names))
        )
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

    # --- the list beside the plot, or below it ------------------------------------
    @property
    def list_below(self) -> bool:
        return self.signals_view.below

    @Slot(bool)
    def set_list_below(self, on: bool) -> None:
        """Put the list below the plot, or back beside it, and keep the choice."""
        if on != self.list_below:
            self._arrange(below=on)
        if self._settings is not None:
            self._settings.set(f"{self._key}.list_below", on)

    def _arrange(self, below: bool) -> None:
        """Lay the two out: the list on the left, or the plot above the list.

        The plot comes first below, as a page reads: what is drawn, then the
        numbers for it. Whichever way, the plot takes the room left over.
        """
        self.signals_view.below = below
        first, second = (self.plot, self.signals_view) if below else (self.signals_view, self.plot)
        self.splitter.setOrientation(Qt.Vertical if below else Qt.Horizontal)
        self.splitter.insertWidget(0, first)
        self.splitter.insertWidget(1, second)
        self.splitter.setStretchFactor(0, 1 if below else 0)
        self.splitter.setStretchFactor(1, 0 if below else 1)
        if below:
            room = max(self.splitter.height(), 3 * LIST_WIDTH // 2)
            self.splitter.setSizes(
                [round(room * (1 - LIST_SHARE_BELOW)), round(room * LIST_SHARE_BELOW)]
            )
        else:
            self.splitter.setSizes([LIST_WIDTH, 2 * LIST_WIDTH])

    # --- layout, saved with the rest of the window state ---------------------
    def save_state(self):
        return self.splitter.saveState()

    def restore_state(self, state) -> None:
        """Where the divide was, unless it was saved the other way round.

        A splitter's saved state carries its orientation, and would turn a
        list chosen to be below back beside the plot -- with the sizes meant
        for the other order. The choice wins; the sizes only where they fit.
        """
        if state is None:
            return
        below = self.list_below
        self.splitter.restoreState(state)
        if (self.splitter.orientation() == Qt.Vertical) != below:
            self._arrange(below)
