# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""A custom pane over an XCP slave: its measurements, read by A2L name.

Against the demo slave, through the real XCP manager, because the point is
that nothing above the source needed to change for a key that is a name. Every
read gets exactly one answer, a refusal included: a pane's polling waits on
them, and a read that never answered would stop it.
"""

import time

import pytest
from PySide6.QtCore import QCoreApplication, QSettings

from pycangui import resources
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
    demo_device(bus, kinds=["xcp_slave"])
    source = XcpSource(manager)
    heard = []
    source.value.connect(lambda *answer: heard.append(answer))
    yield manager, source, heard
    manager.shutdown()
    bus.disconnect_bus()


def connect(manager):
    manager.load_a2l(str(resources.path("demo.a2l")))
    manager.set_ids(CMD_ID, RES_ID, False)
    manager.connect_slave()
    wait_until(lambda: manager.is_connected)


def answer_to(source, heard, name):
    before = len(heard)
    source.request(name)
    wait_until(lambda: len(heard) > before)
    assert len(heard) == before + 1, "one answer to one read"
    return heard[-1]


# --- the source -------------------------------------------------------------------------
def test_a_measurement_is_read_by_its_name(stack):
    manager, source, heard = stack
    connect(manager)
    name, value, error = answer_to(source, heard, "BatteryVoltage")
    assert name == "BatteryVoltage" and error is None
    assert value == pytest.approx(13.2), "physical, converted by the A2L"


def test_what_a_field_shows_comes_from_the_a2l(stack):
    manager, source, _heard = stack
    connect(manager)
    param = manager.a2l.find("CoolantTemp")
    shown = source.display("CoolantTemp")
    assert shown.unit == param.unit
    assert (shown.low, shown.high) == (param.lower, param.upper)
    assert not shown.scaled, "the value arrives physical; nothing to scale again"


def test_the_list_to_pick_from_is_the_measurements(stack):
    manager, source, _heard = stack
    assert source.objects() == [], "no A2L, nothing to pick"
    connect(manager)
    names = {key for key, _name, _access in source.objects()}
    assert {"EngineSpeed", "BatteryVoltage", "CoolantTemp"} <= names
    assert "SpeedLimit" not in names, "a characteristic, for when writing is offered"
    assert all(access == "ro" for _key, _name, access in source.objects())


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
    manager, source, heard = stack
    if setup == "a2l":
        manager.load_a2l(str(resources.path("demo.a2l")))
    elif setup == "connected":
        connect(manager)
    got, value, error = answer_to(source, heard, name)
    assert got == name and value is None and error


def test_nothing_is_written_yet(stack):
    manager, source, heard = stack
    connect(manager)
    assert not source.writable
    before = len(heard)
    source.write("SpeedLimit", 7000)
    wait_until(lambda: len(heard) > before)
    assert heard[-1][1] is None and heard[-1][2]
    assert answer_to(source, heard, "EngineSpeed")[2] is None, "and the slave still answers"


def test_another_a2l_tells_the_pane(stack):
    manager, source, _heard = stack
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
            fields=[Field(name="EngineSpeed", kind="value"), Field(name="BatteryVoltage")],
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
    assert not voltage.writable, "read only, for now"


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
