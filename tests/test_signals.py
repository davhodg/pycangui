# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""DBC decoding, the signal hub, and the Signals/Plot panes."""

import struct

import pytest
from PySide6.QtCore import Qt

from pycangui import resources
from pycangui.core.bus import Frame
from pycangui.core.dbc import DbcDecoder
from pycangui.core.signals import SignalHub
from pycangui.ui.plot_view import PlotView
from pycangui.ui.signals_view import SignalsView


def frame(can_id: int, data: bytes, t: float = 0.0, ext: bool = False) -> Frame:
    return Frame(t, "vcan", can_id, ext, False, True, data)


def test_dbc_decode_demo_tpdo():
    dbc = DbcDecoder()
    assert not dbc.loaded
    dbc.load(resources.path("demo.dbc"))
    f = frame(0x185, struct.pack("<HhI", 0x0237, -40, 12345))
    assert dbc.message_name(f) == "DriveStatus"
    msg, values = dbc.decode(f)
    assert values == {"Statusword": 0x0237, "MotorSpeed": -40, "Odometer": 12.345}
    assert dbc.units(msg)["MotorSpeed"] == "rpm"
    assert dbc.decode(frame(0x777, b"")) is None
    assert dbc.decode(frame(0x185, b"\x01")) is not None  # truncated frames still decode
    dbc.unload(resources.path("demo.dbc"))
    assert not dbc.loaded


def test_hub_series_window_and_trim():
    hub = SignalHub()
    added = []
    hub.added.connect(added.append)
    for i in range(10):
        hub.push("g", "x", i * 0.1, i, unit="V")
    hub.push_many("g", 1.0, {"x": 10, "flag": True, "name": "text"})  # bool/str ignored
    assert added == ["g/x"]
    s = hub.get("g/x")
    assert s.unit == "V" and s.latest == 10
    ts, vs = s.window(0.75)
    assert list(ts) == [0.8, 0.9, 1.0] and list(vs) == [8, 9, 10]
    from pycangui.core import signals

    for i in range(int(signals.MAX_SAMPLES * 1.6)):
        hub.push("g", "y", float(i), i)
    assert len(hub.get("g/y").times) <= signals.MAX_SAMPLES * 1.5  # trimmed at least once


def test_signals_view_and_plot(app):
    hub = SignalHub()
    view = SignalsView(hub)
    plot = PlotView(hub, now=lambda: 2.0)
    plot.show()  # redraw is skipped while hidden
    view.plot_toggled.connect(plot.set_plotted)
    hub.push_many("DBC DriveStatus", 1.0, {"MotorSpeed": 5.0}, {"MotorSpeed": "rpm"})
    hub.push_many("DBC DriveStatus", 1.5, {"MotorSpeed": 7.5})
    item = view._items["DBC DriveStatus/MotorSpeed"]
    view._refresh_timer.timeout.emit()
    assert item.text(0) == "MotorSpeed" and item.text(2) == "rpm"
    item.setCheckState(3, Qt.Checked)
    assert plot.plotted() == ["DBC DriveStatus/MotorSpeed"]
    plot._redraw()
    xs, ys = plot._curves["DBC DriveStatus/MotorSpeed"].getData()
    assert list(xs) == [1.0, 1.5] and list(ys) == [5.0, 7.5]
    view.unplot_all()
    assert plot.plotted() == []
    view.search.setText("odo")
    assert item.isHidden()


def test_forgotten_signals_leave_the_list_and_the_plot(app):
    """A database removed left its signals listed, as though still decoded."""
    from pycangui.ui.scope_view import ScopeView

    hub = SignalHub()
    scope = ScopeView(hub, lambda: 0.0)
    hub.push("DBC Gone", "Speed", 0.0, 1.0)
    hub.push("DBC Gone", "Torque", 0.0, 2.0)
    hub.push("DBC Kept", "Rpm", 0.0, 3.0)
    scope.signals_view._items["DBC Gone/Speed"].setCheckState(3, Qt.Checked)
    assert "DBC Gone/Speed" in scope.plot.plotted()
    removed = []
    hub.removed.connect(removed.append)

    assert hub.forget_groups(["DBC Gone"]) == 2
    assert sorted(removed[0]) == ["DBC Gone/Speed", "DBC Gone/Torque"]
    tree = scope.signals_view.tree
    groups = [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())]
    assert groups == ["DBC Kept"], "the rows go, and the group with them"
    assert scope.plot.plotted() == [], "and off the plot"

    hub.push("DBC Gone", "Speed", 1.0, 4.0)
    assert "DBC Gone/Speed" in scope.signals_view._items, "it comes back when decoded again"


# --- count, rate, min and max ---------------------------------------------------------------
def test_count_and_the_extremes_are_kept_as_samples_arrive():
    from pycangui.core.signals import SignalHub

    hub = SignalHub()
    for i, value in enumerate((5.0, -2.0, 9.0, 3.0)):
        hub.push("Live", "Speed", i * 0.1, value)
    s = hub.get("Live/Speed")
    assert (s.count, s.minimum, s.maximum) == (4, -2.0, 9.0)

    hub.clear()
    assert (s.count, s.minimum, s.maximum) == (0, None, None), "Clear history starts them again"
    assert s.rate() is None


def test_the_count_is_of_every_sample_not_only_those_still_held(monkeypatch):
    from pycangui.core import signals

    monkeypatch.setattr(signals, "MAX_SAMPLES", 10)
    hub = signals.SignalHub()
    for i in range(100):
        hub.push("Live", "Speed", i * 0.01, float(i))
    s = hub.get("Live/Speed")
    assert len(s.times) < 100, "the history was trimmed"
    assert (s.count, s.minimum, s.maximum) == (100, 0.0, 99.0), "and the statistics were not"


def test_the_rate_is_of_the_last_second_of_samples():
    from pycangui.core.signals import SignalHub

    hub = SignalHub()
    for i in range(50):  # 10 a second for five seconds...
        hub.push("Live", "Speed", i * 0.1, 0.0)
    assert hub.get("Live/Speed").rate() == pytest.approx(10.0)
    for i in range(100):  # ...then 100 a second for one
        hub.push("Live", "Speed", 5.0 + i * 0.01, 0.0)
    assert hub.get("Live/Speed").rate() == pytest.approx(100.0, rel=0.05)


def test_an_imported_series_has_its_statistics_too():
    from pycangui.core.signals import SignalHub

    hub = SignalHub()
    key = hub.set_series("drive.mf4", "Speed", [0.0, 0.5, 1.0], [3.0, 7.0, 5.0])
    s = hub.get(key)
    assert (s.count, s.minimum, s.maximum) == (3, 3.0, 7.0)
    assert hub.stored() == (1, 3)


def test_the_statistics_columns_are_there_when_asked_for_and_not_before(app):
    from pycangui.core.signals import SignalHub
    from pycangui.ui.signals_view import (
        COUNT,
        DEFAULT_COLUMNS,
        MAXIMUM,
        MINIMUM,
        OPTIONAL,
        RATE,
        UNIT,
        SignalsView,
    )

    hub = SignalHub()
    for i in range(30):
        hub.push("Live", "Speed", i * 0.1, float(i))
    view = SignalsView(hub)
    view.show()
    assert view.columns() == list(DEFAULT_COLUMNS) == ["Unit"], "the unit, and no statistics"
    header = view.tree.header()
    across = sorted(OPTIONAL.values(), key=header.visualIndex)
    assert across[:3] == [MINIMUM, MAXIMUM, UNIT], "min and max beside the value, then the unit"
    assert header.visualIndex(MINIMUM) == 2, "straight after Value"

    changed = []
    view.columns_changed.connect(changed.append)
    columns = next(a.menu() for a in view.menu_for(None, 0).actions() if a.menu() is not None)
    by_name = {a.text(): a for a in columns.actions()}
    assert set(by_name) == set(OPTIONAL)
    by_name["Count"].trigger()
    by_name["Rate"].trigger()
    assert view.columns() == ["Unit", "Count", "Rate"] and changed[-1] == view.columns()
    by_name["Unit"].trigger()
    assert view.columns() == ["Count", "Rate"] and view.tree.isColumnHidden(UNIT)

    item = view._items["Live/Speed"]
    assert item.text(COUNT) and item.text(RATE), "shown columns are filled in"
    assert item.text(MINIMUM) == item.text(MAXIMUM) == "", "hidden ones are not worked out"
    view.close()


def test_the_columns_shown_come_back_with_the_pane(app, tmp_path, monkeypatch):
    from pycangui.core.context import Context
    from pycangui.core.signals import SignalHub
    from pycangui.ui.scope_view import ScopeView

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    ctx = Context(log=print)
    hub = SignalHub()
    first = ScopeView(hub, lambda: 0.0, ctx, key="scope")
    first.signals_view._toggle_column("Min", True)
    again = ScopeView(hub, lambda: 0.0, ctx, key="scope")
    assert again.signals_view.columns() == ["Min", "Unit"]
    other = ScopeView(hub, lambda: 0.0, ctx, key="scope 2")
    assert other.signals_view.columns() == ["Unit"], "each pane keeps its own"

    again.signals_view._toggle_column("Min", False)
    again.signals_view._toggle_column("Unit", False)
    bare = ScopeView(hub, lambda: 0.0, ctx, key="scope")
    assert bare.signals_view.columns() == [], "none at all is remembered too, not put back"


# --- how much is kept, and saying so ---------------------------------------------------------
def test_the_history_keeps_as_many_samples_as_it_is_set_to():
    from pycangui.core.signals import SignalHub

    hub = SignalHub()
    hub.set_limit(100)
    for i in range(1000):
        hub.push("Live", "Speed", i * 0.01, float(i))
    s = hub.get("Live/Speed")
    assert 100 <= len(s.times) <= 150, "the newest, trimmed back from half as much again"
    assert s.times[-1] == pytest.approx(9.99) and s.values[-1] == 999.0
    assert hub.limit_reached(), "and it says the start has gone"

    hub.set_limit(1000)
    for i in range(1000, 1500):
        hub.push("Live", "Speed", i * 0.01, float(i))
    assert len(s.times) > 500, "raised, it keeps what arrives from then on"

    hub.set_limit(50)
    assert len(s.times) == len(s.values) == 50, "lowered, it takes effect at once"


def test_a_trace_within_the_limit_has_not_reached_it():
    from pycangui.core.signals import SignalHub

    hub = SignalHub()
    for i in range(10):
        hub.push("Live", "Speed", i * 0.1, 0.0)
    assert not hub.limit_reached()
    hub.set_series("drive.mf4", "Speed", [0.0, 1.0], [1.0, 2.0])
    assert not hub.limit_reached(), "a file is kept whole"


def test_samples_are_packed_rather_than_a_list_of_objects():
    """A list of floats is about four times the memory, which is what decides
    how long a trace a machine can hold."""
    from pycangui.core.signals import BYTES_PER_SAMPLE, SignalHub

    hub = SignalHub()
    for i in range(1000):
        hub.push("Live", "Speed", i * 0.01, float(i))
    s = hub.get("Live/Speed")
    held = s.times.itemsize * len(s.times) + s.values.itemsize * len(s.values)
    assert held == BYTES_PER_SAMPLE * 1000
    times, values = s.window(5.0)
    assert len(times) == len(values) == 500 and values[0] == 500.0


def test_the_status_bar_says_how_much_is_held_and_when_the_limit_is_reached(
    app, tmp_path, monkeypatch
):
    from PySide6.QtCore import QSettings

    from pycangui.core.signals import LIMITS, stored_text
    from pycangui.ui.main_window import HISTORY_KEY, MainWindow

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    window = MainWindow()
    try:
        assert window.signals.limit == LIMITS[0], "the default, until it is chosen"
        for i in range(20):
            window.signals.push("Live", "Speed", i * 0.1, 0.0)
        window._update_status()
        said = window.bus_status.summary.text()
        assert stored_text(1, 20, LIMITS[0], False) in said

        chosen = next(a for a in window.history_menu.actions() if a.data() == LIMITS[2])
        chosen.trigger()
        assert window.signals.limit == LIMITS[2]
        assert window.ctx.settings.get(HISTORY_KEY) == LIMITS[2], "kept with the workspace"

        window.signals.set_limit(5)
        window._update_status()
        assert stored_text(1, 5, 5, True) in window.bus_status.summary.text()
    finally:
        window.close()

    again = MainWindow()
    try:
        assert again.signals.limit == LIMITS[2], "and it comes back"
    finally:
        again.close()


def test_the_readout_is_short_whatever_the_size():
    from pycangui.core.signals import stored_text

    amounts = {stored_text(2, samples) for samples in (5, 50_000, 5_000_000)}
    assert len(amounts) == 3, "small, thousands and millions each read differently"
    assert stored_text(1, 5, 200_000, True) != stored_text(1, 5, 200_000, False)
