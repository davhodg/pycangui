# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""The plot's two cursors, and what the signal list reads at and between them."""

import time

import pytest
from PySide6.QtCore import Qt

from pycangui.core.signals import SignalHub
from pycangui.ui.plot_view import PlotView
from pycangui.ui.signals_view import (
    AT_A,
    AT_B,
    CHANGE,
    CURSOR_COLUMNS,
    HIGHEST,
    LOWEST,
    MEAN,
    PLOT,
    SignalsView,
)


def series(*samples, name="x"):
    hub = SignalHub()
    for t, v in samples:
        hub.push("g", name, t, v)
    return hub, hub.get(f"g/{name}")


def test_the_value_at_a_time_is_the_sample_there_or_the_last_before():
    _, s = series((1.0, 10), (2.0, 20), (3.0, 30))
    assert s.value_at(2.0) == 20, "on a sample"
    assert s.value_at(2.5) == 20, "between two: held, not interpolated"
    assert s.value_at(0.5) is None, "before the first there was no value"
    assert s.value_at(9.0) == 30, "after the last it is still the last"


def test_lowest_highest_and_mean_between_two_times_either_way_round():
    _, s = series((1.0, 4), (2.0, 1), (3.0, 7), (4.0, 2), (5.0, 100))
    stretch = s.between(1.5, 4.0)
    assert (stretch.lowest, stretch.highest, stretch.count) == (1, 7, 3)
    assert stretch.mean == pytest.approx(10 / 3)
    assert s.between(4.0, 1.5) == stretch, "B left of A is the same stretch"
    assert s.between(1.2, 1.8) is None, "no samples between"
    assert s.between(2.0, 2.0).count == 1, "a cursor on a sample takes it in"


def view_with(hub, keys):
    """A list with these signals ticked for the plot."""
    view = SignalsView(hub)
    for key in keys:
        view._items[key].setCheckState(PLOT, Qt.Checked)
    return view


def readouts(view, key):
    item = view._items[key]
    return {column: item.text(column) for column in CURSOR_COLUMNS}


def test_the_list_reads_each_plotted_signal_at_and_between_the_cursors(app):
    hub, _ = series((1.0, 10), (2.0, 15), (3.0, 12), (4.0, 30))
    hub.push("g", "quiet", 1.0, 5)
    view = view_with(hub, ["g/x"])
    assert all(view.tree.isColumnHidden(c) for c in CURSOR_COLUMNS), "none until cursors"

    view.set_cursors((1.5, 3.5))
    assert not any(view.tree.isColumnHidden(c) for c in CURSOR_COLUMNS)
    got = readouts(view, "g/x")
    assert (got[AT_A], got[AT_B], got[CHANGE]) == ("10", "12", "2")
    assert (got[LOWEST], got[HIGHEST], got[MEAN]) == ("12", "15", "13.5")
    assert set(readouts(view, "g/quiet").values()) == {""}, "only what is plotted"

    view.set_cursors((3.5, 1.5))
    swapped = readouts(view, "g/x")
    assert (swapped[AT_A], swapped[AT_B], swapped[CHANGE]) == ("12", "10", "-2")
    assert swapped[MEAN] == got[MEAN]


def test_a_cursor_before_the_first_sample_reads_nothing_there(app):
    hub, _ = series((2.0, 10), (3.0, 20))
    view = view_with(hub, ["g/x"])
    view.set_cursors((1.0, 2.5))
    got = readouts(view, "g/x")
    assert (got[AT_A], got[AT_B], got[CHANGE]) == ("", "10", "")
    assert got[LOWEST] == got[HIGHEST] == "10"


def test_named_values_are_shown_by_name_and_a_change_as_a_number(app):
    hub, s = series((1.0, 0), (2.0, 1), (3.0, 2))
    s.choices = {0: "Off", 1: "Run", 2: "Fault"}
    view = view_with(hub, ["g/x"])
    view.set_cursors((1.0, 3.0))
    got = readouts(view, "g/x")
    assert (got[AT_A], got[AT_B], got[CHANGE]) == ("Off (0)", "Fault (2)", "2")
    assert (got[LOWEST], got[HIGHEST], got[MEAN]) == ("Off (0)", "Fault (2)", "1")


def test_new_samples_between_the_cursors_are_taken_in(app):
    hub, _ = series((1.0, 10), (2.0, 20))
    view = view_with(hub, ["g/x"])
    view.show()
    view.set_cursors((0.0, 10.0))
    assert readouts(view, "g/x")[HIGHEST] == "20"
    hub.push("g", "x", 3.0, 50)
    view._refresh_values()
    assert readouts(view, "g/x")[HIGHEST] == "50"
    view.close()


def test_turning_the_cursors_off_takes_the_readouts_away(app):
    hub, _ = series((1.0, 10), (2.0, 20))
    view = view_with(hub, ["g/x"])
    view.set_cursors((1.0, 2.0))
    view.set_cursors(None)
    assert view.cursors is None
    assert all(view.tree.isColumnHidden(c) for c in CURSOR_COLUMNS)
    assert set(readouts(view, "g/x").values()) == {""}
    assert "Count" not in view.columns() and view.columns() == ["Unit"], "not a chosen column"


def test_the_plot_puts_two_cursors_and_says_where_they_are(app):
    hub, _ = series(*[(t / 10, t) for t in range(101)])
    plot = PlotView(hub, now=lambda: 10.0)
    plot.show()
    plot.set_plotted("g/x", True)
    plot.time.setCurrentIndex(plot.time.findText("All"))
    plot._redraw()
    heard = []
    plot.cursors_changed.connect(heard.append)

    plot.cursors.setChecked(True)
    a, b = plot.cursor_positions()
    low, high = plot.plot.getViewBox().viewRange()[0]
    assert low < a < b < high, "inside the time in view, A left of B"
    assert heard[-1] == (a, b)
    assert plot.cursor_times.text(), "the times are shown on the bar"

    plot.set_cursor_positions(2.0, 7.5)
    assert heard[-1] == (2.0, 7.5)

    plot.cursors.setChecked(False)
    assert heard[-1] is None and plot.cursor_positions() is None
    assert not plot.cursor_times.text()
    assert not any(item in plot.plot.items() for item in plot._lines)
    plot.close()


def test_the_cursors_stay_where_they_were_put_while_following(app):
    hub, _ = series(*[(t / 10, t) for t in range(101)])
    clock = [10.0]
    plot = PlotView(hub, now=lambda: clock[0])
    plot.show()
    plot.set_plotted("g/x", True)
    plot._redraw()
    plot.set_cursor_positions(9.0, 9.5)
    for i in range(20):
        hub.push("g", "x", 10.1 + i / 10, i)
    clock[0] = 12.0
    plot._redraw()
    assert plot.cursor_positions() == (9.0, 9.5)
    plot.close()


def test_a_scope_pane_hands_the_cursors_to_its_list_and_remembers_them_on(
    app, tmp_path, monkeypatch
):
    from pycangui.core.context import Context
    from pycangui.ui.scope_view import ScopeView

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    ctx = Context(log=print)
    hub, _ = series((1.0, 10), (2.0, 20))
    pane = ScopeView(hub, lambda: 2.0, ctx, key="scope")
    pane.signals_view._items["g/x"].setCheckState(PLOT, Qt.Checked)
    pane.plot.set_cursor_positions(1.0, 2.0)
    assert pane.signals_view.cursors == (1.0, 2.0)
    assert readouts(pane.signals_view, "g/x")[CHANGE] == "10"

    again = ScopeView(hub, lambda: 2.0, ctx, key="scope")
    assert again.plot.cursors.isChecked(), "on, remembered"
    pane.plot.cursors.setChecked(False)
    assert pane.signals_view.cursors is None
    assert not ScopeView(hub, lambda: 2.0, ctx, key="scope").plot.cursors.isChecked()


def test_a_million_samples_are_read_between_the_cursors_quickly():
    from array import array

    _, s = series((0.0, 0))
    s.times = array("d", (i / 1000 for i in range(1_000_000)))
    s.values = array("d", (float(i % 997) for i in range(1_000_000)))
    started = time.perf_counter()
    for _ in range(10):
        stretch = s.between(10.0, 990.0)
        s.value_at(500.0)
    took = (time.perf_counter() - started) / 10
    assert stretch.count == 980_001 and stretch.highest == 996
    assert took < 0.25, f"{took:.3f}s for one reading"


def keyed_plot(hub):
    plot = PlotView(hub, now=lambda: 10.0)
    plot.show()
    plot.set_plotted("g/x", True)
    plot.time.setCurrentIndex(plot.time.findText("Manual"))
    plot.plot.setXRange(0.0, 10.0, padding=0)
    plot.set_cursor_positions(2.0, 8.0)
    return plot


def test_the_cursors_move_from_the_keyboard(app):
    from PySide6.QtTest import QTest

    hub, _ = series(*[(t / 4, t) for t in range(41)])  # a sample every 0.25 s
    plot = keyed_plot(hub)
    heard = []
    plot.cursors_changed.connect(heard.append)
    width = plot.plot.getViewBox().viewRange()[0]
    hundredth = (width[1] - width[0]) / 100

    assert plot.active_cursor == 0, "A to begin with"
    QTest.keyClick(plot.plot, Qt.Key_Right)
    assert plot.cursor_positions()[0] == pytest.approx(2.0 + hundredth)
    QTest.keyClick(plot.plot, Qt.Key_Left, Qt.ShiftModifier)
    assert plot.cursor_positions()[0] == pytest.approx(2.0 + hundredth - 10 * hundredth)

    QTest.keyClick(plot.plot, Qt.Key_B)
    assert plot.active_cursor == 1
    QTest.keyClick(plot.plot, Qt.Key_Right, Qt.ControlModifier)
    assert plot.cursor_positions()[1] == 8.25, "to the next sample"
    QTest.keyClick(plot.plot, Qt.Key_Left, Qt.ControlModifier)
    QTest.keyClick(plot.plot, Qt.Key_Left, Qt.ControlModifier)
    assert plot.cursor_positions()[1] == 7.75, "back past where it began"

    # Held keys are told to the list once they pause, not at every step.
    deadline = time.monotonic() + 2.0
    while not (heard and heard[-1] == plot.cursor_positions()) and time.monotonic() < deadline:
        QTest.qWait(10)
    assert heard and heard[-1] == plot.cursor_positions()
    plot.close()


def test_ctrl_stops_at_the_last_sample_and_keys_do_nothing_without_cursors(app):
    from PySide6.QtTest import QTest

    hub, _ = series((1.0, 1), (2.0, 2))
    plot = keyed_plot(hub)
    QTest.keyClick(plot.plot, Qt.Key_B)
    QTest.keyClick(plot.plot, Qt.Key_Right, Qt.ControlModifier)
    assert plot.cursor_positions()[1] == 8.0, "no sample after B: it stays"
    plot.cursors.setChecked(False)
    QTest.keyClick(plot.plot, Qt.Key_Right)  # not swallowed, and nothing to move
    assert plot.cursor_positions() is None
    plot.close()


def test_dragging_a_cursor_makes_it_the_one_the_keys_move(app):
    hub, _ = series((1.0, 1), (2.0, 2))
    plot = keyed_plot(hub)
    plot._lines[1].sigDragged.emit(plot._lines[1])
    assert plot.active_cursor == 1
    plot.close()
