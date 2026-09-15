# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""A second Y axis, on the right of the plot, for signals with a scale of their own.

A speed in thousands and a temperature in tens share one axis badly: the
temperature is a flat line along the bottom.  The plot draws those against a
second view box on the right, the list has a second checkbox that chooses
them, and each Signals and Plot pane remembers the choice between runs.
"""

import numpy as np
import pytest
from PySide6.QtCore import QSettings, Qt

from pycangui.core.context import Context
from pycangui.core.signals import SignalHub
from pycangui.ui.main_window import MainWindow
from pycangui.ui.plot_view import PlotView
from pycangui.ui.scope_view import ScopeView
from pycangui.ui.signals_view import PLOT, RIGHT, SignalsView

SPEED = "Live/Speed"
TEMP = "Live/Temp"


def settle(app, times=5):
    for _ in range(times):
        app.processEvents()


class Clock:
    """A stand-in for the bus clock, which the tests move by hand."""

    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def speed_and_temperature(hub, upto: float = 10.0) -> None:
    """Two signals a long way apart in size, arriving together."""
    for t in np.arange(0.0, upto, 0.1):
        hub.push("Live", "Speed", float(t), 3000.0 + t, unit="rpm")
        hub.push("Live", "Temp", float(t), 20.0 + t, unit="degC")


@pytest.fixture
def plot(app):
    hub = SignalHub()
    clock = Clock()
    view = PlotView(hub, clock)
    view.resize(600, 400)
    view.show()
    settle(app)
    yield view, hub, clock
    view.close()


def legend_labels(view) -> list[str]:
    return [label.text for _sample, label in view.legend.items]


# --- the plot -----------------------------------------------------------------------------
def test_the_right_axis_is_only_there_while_a_signal_is_on_it(app, plot):
    view, hub, _clock = plot
    speed_and_temperature(hub)
    view.set_plotted(SPEED, True)
    view.set_plotted(TEMP, True)
    right = view.plot.getAxis("right")
    assert not right.isVisible(), "two signals on the left need no second axis"

    view.set_right(TEMP, True)
    assert right.isVisible()
    assert view.right_axis() == [TEMP]

    view.set_right(TEMP, False)
    assert not right.isVisible()
    assert view.right_axis() == []


def test_a_curve_moved_to_the_right_keeps_its_colour_and_its_legend_entry(app, plot):
    """It is the same curve in the other view box, not a new one with the
    next colour along."""
    view, hub, _clock = plot
    speed_and_temperature(hub)
    view.set_plotted(SPEED, True)
    view.set_plotted(TEMP, True)
    curve = view._curves[TEMP]
    colour = curve.opts["pen"].color().name()

    view.set_right(TEMP, True)
    assert curve.getViewBox() is view.right_view
    assert curve.opts["pen"].color().name() == colour
    assert len(legend_labels(view)) == 2, "still one legend entry each"

    view.set_right(TEMP, False)
    assert curve.getViewBox() is view.plot.getViewBox()
    assert sorted(legend_labels(view)) == ["Speed [rpm]", "Temp [degC]"]


def test_putting_an_unplotted_signal_on_the_right_plots_it(app, plot):
    view, hub, _clock = plot
    speed_and_temperature(hub)
    view.set_right(TEMP, True)
    assert view.plotted() == [TEMP]
    assert view.right_axis() == [TEMP]


def test_unplotting_a_signal_takes_it_off_the_right_axis_too(app, plot):
    """Or plotting it again later would put it straight back on the right,
    which nobody asked for that time."""
    view, hub, _clock = plot
    speed_and_temperature(hub)
    view.set_right(TEMP, True)
    view.set_plotted(TEMP, False)
    assert view.right_axis() == []
    assert not view.plot.getAxis("right").isVisible()
    assert legend_labels(view) == []

    view.set_plotted(TEMP, True)
    assert view.right_axis() == []


def test_each_axis_is_labelled_with_a_unit_only_where_its_signals_share_one(app, plot):
    view, hub, _clock = plot
    speed_and_temperature(hub)
    left, right = view.plot.getAxis("left"), view.plot.getAxis("right")
    view.set_plotted(SPEED, True)
    view.set_plotted(TEMP, True)
    assert left.labelText == "", "rpm and degC have no unit in common"

    view.set_right(TEMP, True)
    assert (left.labelText, right.labelText) == ("rpm", "degC")

    hub.push("Live", "Current", 0.0, 1.0, unit="A")
    view.set_right("Live/Current", True)
    assert right.labelText == ""


def test_the_right_view_box_stays_over_the_plot_when_the_pane_is_resized(app, plot):
    view, hub, _clock = plot
    speed_and_temperature(hub)
    view.set_right(TEMP, True)
    view.resize(900, 700)
    settle(app)
    main, right = view.plot.getViewBox(), view.right_view
    assert right.mapRectToScene(right.rect()) == main.mapRectToScene(main.rect())


def test_follow_moves_both_axes_along_together(app, plot):
    view, hub, clock = plot
    view.window_s.setValue(5.0)
    view.follow.setChecked(True)
    speed_and_temperature(hub, upto=10.0)
    view.set_plotted(SPEED, True)
    view.set_right(TEMP, True)
    clock.t = 10.0
    view._redraw()
    settle(app)

    low, high = view.plot.getViewBox().viewRange()[0]
    assert high == pytest.approx(9.9, abs=0.2)
    assert view.right_view.viewRange()[0] == pytest.approx([low, high], abs=1e-6)


def test_fit_scales_each_axis_to_its_own_signals(app, plot):
    view, hub, _clock = plot
    speed_and_temperature(hub)
    view.set_plotted(SPEED, True)
    view.set_right(TEMP, True)
    view._fit()
    settle(app)

    low, high = view.plot.getViewBox().viewRange()[1]
    assert low <= 3000.5 and 3009 <= high < 3100, f"left axis {low}..{high}"
    low, high = view.right_view.viewRange()[1]
    assert low <= 20.5 and 29 <= high < 100, f"right axis {low}..{high}"


def test_fit_finds_a_file_that_is_only_on_the_right_axis(app, plot):
    """The plot's own fit knows only the curves in its own view box.  With
    the one signal on the right, that is nothing, and the file would stay
    wherever the live trace had left the axis."""
    view, hub, _clock = plot
    t = np.arange(235.0, 240.0, 0.1)
    key = hub.set_series("drive.mf4", "Temp", t, 20.0 + np.arange(t.size) * 0.1, "degC")
    view.set_right(key, True)
    view._fit()
    settle(app)

    low, high = view.plot.getViewBox().viewRange()[0]
    assert 230 < low <= 235.0 and 239.9 <= high < 245, (low, high)
    assert view.right_view.viewRange()[0] == pytest.approx([low, high], abs=1e-6)


def test_pause_holds_the_right_axis_still_as_well(app, plot):
    view, hub, clock = plot
    speed_and_temperature(hub, upto=5.0)
    view.set_right(TEMP, True)
    clock.t = 5.0
    view._redraw()
    before = len(view._curves[TEMP].getData()[0])

    view.pause.setChecked(True)
    for t in np.arange(5.0, 8.0, 0.1):
        hub.push("Live", "Temp", float(t), 30.0)
    clock.t = 8.0
    view._redraw()
    assert len(view._curves[TEMP].getData()[0]) == before


# --- the list -----------------------------------------------------------------------------
@pytest.fixture
def listed(app):
    hub = SignalHub()
    signals = SignalsView(hub)
    plot = PlotView(hub, now=lambda: 10.0)
    signals.plot_toggled.connect(plot.set_plotted)
    signals.axis_toggled.connect(plot.set_right)
    speed_and_temperature(hub)
    return signals, plot


def test_ticking_plot_y2_plots_the_signal_on_the_right_only(app, listed):
    signals, plot = listed
    item = signals._items[TEMP]
    item.setCheckState(RIGHT, Qt.Checked)
    assert item.checkState(PLOT) == Qt.Unchecked, "one axis at a time"
    assert plot.plotted() == [TEMP]
    assert plot.right_axis() == [TEMP]


def test_unticking_plot_y2_takes_the_signal_off_the_plot(app, listed):
    signals, plot = listed
    item = signals._items[TEMP]
    item.setCheckState(RIGHT, Qt.Checked)
    item.setCheckState(RIGHT, Qt.Unchecked)
    assert item.checkState(PLOT) == Qt.Unchecked
    assert plot.plotted() == [] and plot.right_axis() == []


def test_ticking_the_other_box_moves_the_signal_to_that_axis(app, listed):
    """The same curve moving, so it keeps its colour and is plotted once."""
    signals, plot = listed
    item = signals._items[TEMP]
    item.setCheckState(PLOT, Qt.Checked)
    item.setCheckState(RIGHT, Qt.Checked)
    assert item.checkState(PLOT) == Qt.Unchecked
    assert plot.plotted() == [TEMP] and plot.right_axis() == [TEMP]

    item.setCheckState(PLOT, Qt.Checked)
    assert item.checkState(RIGHT) == Qt.Unchecked
    assert plot.plotted() == [TEMP] and plot.right_axis() == []


def test_unplot_all_takes_signals_off_both_axes(app, listed):
    signals, plot = listed
    signals._items[SPEED].setCheckState(PLOT, Qt.Checked)
    signals._items[TEMP].setCheckState(RIGHT, Qt.Checked)
    signals.unplot_all()
    assert plot.plotted() == []
    for key in (SPEED, TEMP):
        item = signals._items[key]
        assert (item.checkState(PLOT), item.checkState(RIGHT)) == (Qt.Unchecked, Qt.Unchecked)


# --- remembered between runs --------------------------------------------------------------
@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))


def scope(key: str = "scope") -> tuple[ScopeView, SignalHub]:
    """A pane as a fresh start would build it, reading settings from disk."""
    hub = SignalHub()
    return ScopeView(hub, lambda: 10.0, Context(log=print), key=key), hub


def test_a_pane_remembers_its_signals_and_which_axis_they_are_on(app, home):
    first, hub = scope()
    speed_and_temperature(hub)
    first.signals_view._items[SPEED].setCheckState(PLOT, Qt.Checked)
    first.signals_view._items[TEMP].setCheckState(RIGHT, Qt.Checked)
    first.close()

    again, hub = scope()
    assert again.plot.plotted() == [], "nothing has arrived to plot yet"
    speed_and_temperature(hub)
    assert again.plot.plotted() == [SPEED, TEMP]
    assert again.plot.right_axis() == [TEMP]
    temp, speed = again.signals_view._items[TEMP], again.signals_view._items[SPEED]
    assert (temp.checkState(PLOT), temp.checkState(RIGHT)) == (Qt.Unchecked, Qt.Checked)
    assert (speed.checkState(PLOT), speed.checkState(RIGHT)) == (Qt.Checked, Qt.Unchecked)
    again.close()


def test_a_second_pane_keeps_its_own_signals(app, home):
    first, hub = scope("scope")
    speed_and_temperature(hub)
    first.signals_view._items[TEMP].setCheckState(RIGHT, Qt.Checked)
    first.close()

    second, hub = scope("scope 2")
    speed_and_temperature(hub)
    assert second.plot.plotted() == []
    second.close()


def test_settings_from_before_the_right_axis_still_load(app, home):
    """What was plotted comes back, all of it on the left where it was."""
    Context(log=print).settings.set("scope.plotted", [SPEED, TEMP])
    view, hub = scope()
    speed_and_temperature(hub)
    assert view.plot.plotted() == [SPEED, TEMP]
    assert view.plot.right_axis() == []
    view.close()


def test_a_hand_edited_right_axis_list_is_ignored_rather_than_fatal(app, home):
    settings = Context(log=print).settings
    settings.set("scope.plotted", [TEMP])
    settings.set("scope.right_axis", "Temp")
    view, hub = scope()
    speed_and_temperature(hub)
    assert view.plot.plotted() == [TEMP]
    assert view.plot.right_axis() == []
    view.close()


def test_a_signal_that_has_not_arrived_yet_is_not_forgotten(app, home):
    """Unticking one signal must not write the saved list back as only the
    signals that happen to exist this minute."""
    Context(log=print).settings.set("scope.plotted", [SPEED, "drive.mf4/Temp"])
    Context(log=print).settings.set("scope.right_axis", ["drive.mf4/Temp"])
    view, hub = scope()
    speed_and_temperature(hub)
    view.signals_view._items[SPEED].setCheckState(PLOT, Qt.Unchecked)

    settings = Context(log=print).settings
    assert settings.get("scope.plotted") == ["drive.mf4/Temp"]
    assert settings.get("scope.right_axis") == ["drive.mf4/Temp"]
    view.close()


def test_unplot_all_forgets_signals_that_have_not_arrived_yet_too(app, home):
    Context(log=print).settings.set("scope.plotted", [SPEED, "drive.mf4/Temp"])
    view, hub = scope()
    speed_and_temperature(hub)
    view.signals_view.unplot_all()

    settings = Context(log=print).settings
    assert settings.get("scope.plotted") == []
    assert settings.get("scope.right_axis") == []
    view.close()


def test_the_right_axis_survives_a_restart_of_the_window(app, tmp_path, monkeypatch):
    def fresh_window():
        monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
        QSettings().clear()
        return MainWindow()

    window = fresh_window()
    speed_and_temperature(window.signals)
    window.signals_view._items[TEMP].setCheckState(RIGHT, Qt.Checked)
    window.close()

    window = fresh_window()
    speed_and_temperature(window.signals)
    assert window.plot.right_axis() == [TEMP]
    window.close()
