# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""The plot's rolling window, and what its right hand edge is measured from.

Reported: the plot keeps scrolling during a live trace, and carries on
scrolling after the bus is disconnected. It followed *the clock* --
``time.monotonic`` since the bus manager started, which advances whether or
not a bus is open or a single frame has arrived -- so the axis marched left
for ever while the data stood still and slid off the edge.

It follows the data now. These tests drive the clock and the samples apart
on purpose, because on a busy bus the two are indistinguishable and every
wrong answer here looks right.
"""

import numpy as np
import pytest

from pycangui.core.signals import SignalHub
from pycangui.ui.plot_view import PlotView

WINDOW_S = 10.0


class Clock:
    """A stand-in for the bus clock, which the tests move by hand."""

    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def plot(app):
    hub = SignalHub()
    clock = Clock()
    view = PlotView(hub, clock)
    view.window_s.setValue(WINDOW_S)
    view.follow.setChecked(True)
    view.resize(600, 400)
    view.show()
    app.processEvents()
    yield view, hub, clock
    view.close()


def live(hub, upto: float, every: float = 0.1) -> str:
    """A signal arriving on the bus clock, from zero to ``upto``."""
    for t in np.arange(0.0, upto, every):
        hub.push("Live", "RPM", float(t), 900.0)
    return "Live/RPM"


# --- the reported bug -----------------------------------------------------------------------
def test_the_plot_holds_still_once_the_frames_stop(app, plot):
    """The disconnected case, which is what made it obvious: nothing arrives
    and the trace slides off the left anyway."""
    view, hub, clock = plot
    clock.t = 30.0
    view.set_plotted(live(hub, upto=30.0), True)
    view._redraw()
    _low, edge = view.plot.viewRange()[0]

    clock.t = 300.0  # five minutes of disconnected silence
    view._redraw()
    _low, still = view.plot.viewRange()[0]
    assert still == pytest.approx(edge, abs=0.2), (
        f"the axis moved from {edge} to {still} with nothing arriving"
    )


def test_a_quiet_bus_does_not_scroll_either(app, plot):
    """Connected and idle is the same picture, and the same answer: a flat
    line marching left says nothing that a stopped plot does not."""
    view, hub, clock = plot
    clock.t = 5.0
    view.set_plotted(live(hub, upto=5.0), True)
    view._redraw()

    clock.t = 45.0
    view._redraw()
    _low, edge = view.plot.viewRange()[0]
    assert edge == pytest.approx(4.9, abs=0.2), f"right edge at {edge}, not at the last sample"


def test_it_does_follow_while_frames_are_arriving(app, plot):
    """The behaviour being kept. Stopping when the data stops is only right
    if it moves when the data moves."""
    view, hub, clock = plot
    key = live(hub, upto=5.0)
    view.set_plotted(key, True)
    clock.t = 5.0
    view._redraw()
    _low, before = view.plot.viewRange()[0]

    for t in np.arange(5.0, 20.0, 0.1):
        hub.push("Live", "RPM", float(t), 900.0)
    clock.t = 20.0
    view._redraw()
    low, after = view.plot.viewRange()[0]
    assert after > before + 10, f"the window did not advance: {before} -> {after}"
    assert low == pytest.approx(after - WINDOW_S, abs=0.01), "and it is still a window"


# --- what following must not start doing ------------------------------------------------
def test_an_imported_file_does_not_drag_the_window_to_its_own_times(app, plot):
    """A file recorded last Tuesday carries timestamps far ahead of this
    session's clock. Following the newest sample must not mean following
    that one and taking the live trace off screen; *Fit* is how a file is
    found."""
    view, hub, clock = plot
    view.set_plotted(live(hub, upto=8.0), True)
    stamps = np.arange(1.7e9, 1.7e9 + 5.0, 0.1)
    view.set_plotted(hub.set_series("drive.mf4", "Speed", stamps, np.arange(stamps.size)), True)

    clock.t = 8.0
    view._redraw()
    _low, edge = view.plot.viewRange()[0]
    assert edge < 100.0, f"the file pulled the axis out to {edge}"


def test_the_edge_falls_back_to_the_clock_with_nothing_plotted(app, plot):
    """Nothing has a newest sample, so there is nothing else to use -- and
    the alternative is an axis that never initialises."""
    view, _hub, clock = plot
    clock.t = 12.0
    assert view._edge() == pytest.approx(12.0)


def test_a_signal_with_no_samples_yet_does_not_pin_the_edge(app, plot):
    """Plotted the moment it is ticked, filled when a frame arrives. An
    empty series in between must not be read as "the newest sample is
    nothing"."""
    view, hub, clock = plot
    hub.push("Live", "RPM", 3.0, 900.0)
    view.set_plotted("Live/RPM", True)
    hub.push("Live", "Temp", 0.0, 20.0)
    hub.get("Live/Temp").times.clear()
    hub.get("Live/Temp").values.clear()
    view.set_plotted("Live/Temp", True)

    clock.t = 9.0
    assert view._edge() == pytest.approx(3.0), "the one with samples decides"
