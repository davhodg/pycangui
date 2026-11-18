# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""A custom pane over an XCP slave: its measurements, read by A2L name.

Against the demo slave, through the real XCP manager, because the point is
that nothing above the source needed to change for a key that is a name. Every
read gets exactly one answer, a refusal included: a pane's polling waits on
them, and a read that never answered would stop it.
"""

import struct
import time

import pytest
from PySide6.QtCore import QCoreApplication, QSettings, Qt
from PySide6.QtTest import QTest

from pycangui import resources
from pycangui.core import workspaces
from pycangui.core.bus import BusManager
from pycangui.core.context import Context
from pycangui.core.hooks import Hooks
from pycangui.core.signals import SignalHub
from pycangui.custom_panes import model
from pycangui.custom_panes.model import XCP, CustomPane, Field
from pycangui.custom_panes.xcp_source import XcpSource
from pycangui.xcp.manager import XcpManager

#: The demo slave's identifiers, as its pane is told them.
CMD_ID, RES_ID = 0x7A0, 0x7A1


def settle(app, times=10):
    for _ in range(times):
        app.processEvents()
        time.sleep(0.005)


def wait_until(pred, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not pred():
        QCoreApplication.processEvents()
        if time.monotonic() > deadline and not pred():
            raise AssertionError("timed out")
        time.sleep(0.005)


@pytest.fixture
def stack(app, tmp_path, monkeypatch, demo_device):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    ctx = Context(log=print)
    bus = BusManager()
    manager = XcpManager(bus, Hooks(ctx), SignalHub(), ctx)
    bus.connect_bus("virtual", "vcan_xcp_source", 500000, False)
    demo = demo_device(bus, kinds=["xcp_slave"])
    source = XcpSource(manager)
    heard = []
    source.value.connect(lambda *answer: heard.append(answer))
    yield manager, source, heard, demo
    manager.shutdown()
    bus.disconnect_bus()


def connect(manager):
    manager.load_a2l(str(resources.path("demo.a2l")))
    manager.set_ids(CMD_ID, RES_ID, False)
    manager.connect_slave()
    wait_until(lambda: manager.is_connected)


def unlock_calibration(manager):
    """The demo slave's key: its seed with every bit turned over."""
    hook = "def compute_key(resource, seed, *, ctx):\n"
    hook += "    return bytes(b ^ 0xFF for b in seed)\n"
    (workspaces.hooks_dir() / "xcp.py").write_text(hook)
    manager._hooks.reload()
    manager.unlock(0x01)
    wait_until(lambda: manager.info.protection & 0x01 == 0)


def written_to(source, heard, name, value):
    before = len(heard)
    source.write(name, value)
    wait_until(lambda: len(heard) > before)
    assert len(heard) == before + 1, "one answer to one write"
    return heard[-1]


def answer_to(source, heard, name):
    before = len(heard)
    source.request(name)
    wait_until(lambda: len(heard) > before)
    assert len(heard) == before + 1, "one answer to one read"
    return heard[-1]


# --- the source -------------------------------------------------------------------------
def test_a_measurement_is_read_by_its_name(stack):
    manager, source, heard, _demo = stack
    connect(manager)
    name, value, error = answer_to(source, heard, "BatteryVoltage")
    assert name == "BatteryVoltage" and error is None
    assert value == pytest.approx(13.2), "physical, converted by the A2L"


def test_what_a_field_shows_comes_from_the_a2l(stack):
    manager, source, _heard, _demo = stack
    connect(manager)
    param = manager.a2l.find("CoolantTemp")
    shown = source.display("CoolantTemp")
    assert shown.unit == param.unit
    assert (shown.low, shown.high) == (param.lower, param.upper)
    assert not shown.scaled, "the value arrives physical; nothing to scale again"


def test_the_list_to_pick_from_is_single_values_and_tables(stack):
    manager, source, _heard, _demo = stack
    assert source.objects() == [], "no A2L, nothing to pick"
    connect(manager)
    access = {key: how for key, _name, how in source.objects()}
    assert {"EngineSpeed", "BatteryVoltage", "CoolantTemp"} <= set(access)
    assert access["EngineSpeed"] == "ro", "a measurement is read"
    assert access["SpeedLimit"] == "rw", "a characteristic is written too"
    for key, how in access.items():
        param = manager.a2l.find(key)
        assert param.is_table or not (param.is_array or param.is_text), "one value, or a table"
        assert param.is_table == (how == "table")


@pytest.mark.parametrize(
    "setup, name",
    [
        ("nothing", "EngineSpeed"),
        ("a2l", "EngineSpeed"),
        ("connected", "NoSuchThing"),
    ],
    ids=["no-a2l", "not-connected", "unknown-name"],
)
def test_a_read_that_cannot_happen_is_still_answered(stack, setup, name):
    manager, source, heard, _demo = stack
    if setup == "a2l":
        manager.load_a2l(str(resources.path("demo.a2l")))
    elif setup == "connected":
        connect(manager)
    got, value, error = answer_to(source, heard, name)
    assert got == name and value is None and error


def test_a_characteristic_is_written_and_read_back(stack):
    manager, source, heard, demo = stack
    connect(manager)
    unlock_calibration(manager)
    name, value, error = written_to(source, heard, "SpeedLimit", 7000)
    assert (name, error) == ("SpeedLimit", None)
    assert value == 7000, "what the slave holds afterwards"
    assert struct.unpack_from("<H", demo["xcp_slave"].state.memory, 0x2000)[0] == 7000


def test_a_locked_slave_says_so_against_the_field(stack):
    manager, source, heard, demo = stack
    connect(manager)
    _name, value, error = written_to(source, heard, "SpeedLimit", 7000)
    assert value is None and error
    assert struct.unpack_from("<H", demo["xcp_slave"].state.memory, 0x2000)[0] != 7000


@pytest.mark.parametrize(
    "name, value",
    [("EngineSpeed", 1000), ("NoSuchThing", 1), ("SpeedLimit", "fast")],
    ids=["a-measurement", "unknown", "not-a-number"],
)
def test_a_write_that_cannot_happen_is_still_answered(stack, name, value):
    manager, source, heard, _demo = stack
    connect(manager)
    unlock_calibration(manager)
    got, read, error = written_to(source, heard, name, value)
    assert got == name and read is None and error


def test_another_a2l_tells_the_pane(stack):
    manager, source, _heard, _demo = stack
    told = []
    source.changed.connect(lambda: told.append(True))
    manager.load_a2l(str(resources.path("demo.a2l")))
    assert told


# --- on a pane ---------------------------------------------------------------------------
@pytest.fixture
def window(app, tmp_path, monkeypatch, demo_device):
    from pycangui.ui.main_window import MainWindow

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    win = MainWindow()
    bus = win.channels.active_bus()
    bus.connect_bus("virtual", "vcan_xcp_pane", 500000, False)
    demo_device(bus, kinds=["xcp_slave"])
    connect(win.xcp)
    yield win
    win.close()


def xcp_pane(window):
    model.save(
        "engine",
        CustomPane(
            title="Engine",
            protocol=XCP,
            fields=[
                Field(name="EngineSpeed", kind="value"),
                Field(name="BatteryVoltage"),
                Field(name="SpeedLimit", kind="number"),
            ],
        ),
    )
    window.open_custom_pane("engine")
    return window._custom_pane_view("engine")


def test_an_xcp_pane_reads_from_the_slave_as_it_opens(app, window):
    view = xcp_pane(window)
    assert isinstance(view.source, XcpSource)
    voltage = view._widgets[1]
    wait_until(lambda: voltage._raw is not None)
    assert voltage._raw == pytest.approx(13.2)


def test_write_all_sets_a_characteristic_on_the_slave(app, window, demo_device):
    view = xcp_pane(window)
    limit = view._widgets[2]
    wait_until(lambda: limit._raw is not None)
    unlock_calibration(window.xcp)
    limit.edit.clear()
    QTest.keyClicks(limit.edit, "7200")  # typed, so it is held as a change
    assert view.unsent == 1
    view.write_all()
    wait_until(lambda: limit._raw == 7200)
    assert view.unsent == 0


def test_a_value_beyond_the_a2l_s_limits_is_never_sent(app, window):
    view = xcp_pane(window)
    limit = view._widgets[2]
    wait_until(lambda: limit._raw is not None)
    before = limit._raw
    sent = []
    window.xcp.answered.connect(lambda *a: sent.append(a))
    limit.edit.setText("9000")  # the A2L allows 0 to 8000
    QTest.keyClick(limit.edit, Qt.Key_Return)
    settle(app)
    assert limit._raw == before and not [a for a in sent if a[0] == "SpeedLimit"]


def test_an_xcp_pane_polls_into_signals(app, window):
    view = xcp_pane(window)
    view.poll.setChecked(True)
    wait_until(lambda: view.poller.achieved > 0)
    group = view.source.label
    wait_until(lambda: any(key.startswith(group) for key in window.signals.keys()))
    view.poll.setChecked(False)


def test_an_xcp_pane_is_offered_the_slave_and_nothing_else(app, window):
    view = xcp_pane(window)
    offered = [view.sources.itemData(i) for i in range(view.sources.count())]
    assert len(offered) == 1 and offered[0] is not None


def test_a_new_pane_can_be_made_for_xcp(app, window):
    """Chosen in the editor while the pane is empty; bound to the slave on OK."""
    from pycangui.ui.custom_pane_view import CustomPaneEditor

    window.open_custom_pane("fresh")
    view = window._custom_pane_view("fresh")
    editor = CustomPaneEditor(view, view.pane, view.source, view._source_for)
    editor.protocol.setCurrentIndex(editor.protocol.findData(XCP))
    assert not editor.node.isEnabled(), "a slave has no node number"
    editor._fields = [Field(name="EngineSpeed", kind="value")]
    editor._fill()
    assert not editor.protocol.isEnabled(), "fixed once there are fields"
    pane = editor.result_pane()
    assert pane.protocol == XCP
    editor.deleteLater()


# --- curves and maps ----------------------------------------------------------------------
def test_curves_and_maps_are_offered_as_tables(stack):
    manager, source, _heard, _demo = stack
    connect(manager)
    access = {key: how for key, _name, how in source.objects()}
    assert access["IdleCurve"] == "table" and access["IgnitionMap"] == "table"


def test_a_table_is_picked_as_a_table_field(app, window):
    from pycangui.ui.custom_pane_view import AddObjects

    picker = AddObjects(None, XcpSource(window.xcp), XCP)
    entry = next(e for e in picker._entries if e[0] == "IgnitionMap")
    assert picker._as_field(*entry).kind == "table"
    picker.deleteLater()


def test_a_table_field_opens_the_table_window_and_reads_it(app, window):
    model.save(
        "tables",
        CustomPane(title="Tables", protocol=XCP, fields=[Field(name="IgnitionMap", kind="table")]),
    )
    window.open_custom_pane("tables")
    view = window._custom_pane_view("tables")
    assert view.poller.objects == [], "a table is read by its window, not polled here"
    view._widgets[0].open.click()
    table = window.xcp_view._tables.get("IgnitionMap")
    assert table is not None and table.isVisible()
    wait_until(lambda: any(cell.text() for cell in table._cells.values()))
    table.close()


def test_a_table_field_belongs_on_an_xcp_pane():
    pane = CustomPane(title="t", fields=[Field(index=0x2001, kind="table")])
    assert model.problems(pane)
    xcp = CustomPane(title="t", protocol=XCP, fields=[Field(name="IgnitionMap", kind="table")])
    assert model.problems(xcp) == []
