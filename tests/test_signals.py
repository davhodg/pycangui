# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""DBC decoding, the signal hub, and the Signals/Plot panes."""

import struct

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
