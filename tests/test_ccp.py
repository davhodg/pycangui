# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""CCP through the same pane as XCP, against the simulated CCP slave."""

import struct
import time

import pytest
from PySide6.QtCore import QCoreApplication, QSettings

from pycangui import resources
from pycangui.ccp import RESOURCE_CAL
from pycangui.core import workspaces
from pycangui.core.bus import BusManager, Frame
from pycangui.core.context import Context
from pycangui.core.hooks import Hooks
from pycangui.core.signals import SignalHub
from pycangui.nodes import ccp_slave
from pycangui.xcp.manager import XcpManager

KEY_HOOK = "def compute_key(resource, seed, *, ctx):\n    return bytes(b ^ 0xFF for b in seed)\n"


def wait_until(pred, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not pred():
        QCoreApplication.processEvents()
        if time.monotonic() > deadline:
            raise AssertionError("timed out")
        time.sleep(0.005)


@pytest.fixture
def stack(app, tmp_path, monkeypatch, demo_device):
    """The calibration manager on the CCP engine, and a CCP slave to talk to."""
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    ctx = Context(log=print)
    hooks = Hooks(ctx)
    hub = SignalHub()
    bus = BusManager()
    manager = XcpManager(bus, hooks, hub, ctx)
    manager.set_component("ccp-builtin")
    bus.connect_bus("virtual", "vcan_ccp", 500000, False)
    demo = demo_device(bus, kinds=["ccp_slave"])
    yield bus, manager, demo, hub, ctx
    manager.shutdown()
    bus.disconnect_bus()


def test_the_same_pane_reads_and_writes_over_ccp(stack):
    """The whole point of a second engine: nothing above it changed."""
    _bus, manager, demo, _hub, _ctx = stack
    lines: list[str] = []
    manager.result.connect(lines.append)

    def last_after(n):
        wait_until(lambda: len(lines) > n)
        return lines[-1]

    manager.load_a2l(str(resources.path("demo.a2l")))
    manager.set_ids(ccp_slave.COMMAND_ID, ccp_slave.RESPONSE_ID, False)
    manager.set_station(ccp_slave.STATION)
    n = len(lines)
    manager.connect_slave()
    assert "CCP connected" in last_after(n), "and it says which protocol, not XCP"
    assert manager.is_connected
    n = len(lines)

    # The A2L is the same file the XCP slave uses; CCP is big-endian, which
    # the engine reports and the manager decodes with.
    manager.read("BatteryVoltage")
    assert last_after(n) == "BatteryVoltage = 13.2 V"
    n = len(lines)

    (workspaces.hooks_dir() / "xcp.py").write_text(KEY_HOOK)
    manager._hooks.reload()
    manager.unlock(RESOURCE_CAL)
    assert last_after(n) == "CCP resource 0x01 unlocked"
    n = len(lines)

    manager.write("SpeedLimit", "7000")
    assert last_after(n) == "SpeedLimit <- 7000"
    memory = demo["ccp_slave"].state.memory
    assert struct.unpack_from(">H", memory, ccp_slave.SPEED_LIMIT)[0] == 7000

    manager.disconnect_slave()
    wait_until(lambda: not manager.is_connected)


def test_writing_is_refused_until_the_key_is_given(stack):
    """A seed exists so that reading a controller and rewriting its
    constants are not the same permission."""
    _bus, manager, demo, _hub, _ctx = stack
    lines: list[str] = []
    manager.result.connect(lines.append)
    manager.load_a2l(str(resources.path("demo.a2l")))
    manager.set_ids(ccp_slave.COMMAND_ID, ccp_slave.RESPONSE_ID, False)
    manager.set_station(ccp_slave.STATION)
    manager.connect_slave()
    # The connected line arrives a moment after the flag, so wait for the
    # line as well: otherwise the next thing counted is that one.
    wait_until(lambda: any("connected" in line for line in lines))
    was = struct.unpack_from(">H", demo["ccp_slave"].state.memory, ccp_slave.SPEED_LIMIT)[0]

    n = len(lines)
    manager.write("SpeedLimit", "7000")
    wait_until(lambda: len(lines) > n)

    assert "locked" in lines[-1].lower(), "and it says why rather than failing silently"
    now = struct.unpack_from(">H", demo["ccp_slave"].state.memory, ccp_slave.SPEED_LIMIT)[0]
    assert now == was, "nothing was written"


def test_another_station_on_the_same_ids_is_not_this_one(stack):
    """What the station address is for: several controllers, one pair of ids."""
    _bus, manager, _demo, _hub, _ctx = stack
    manager.set_ids(ccp_slave.COMMAND_ID, ccp_slave.RESPONSE_ID, False)
    manager.set_station(ccp_slave.STATION + 1)
    lines: list[str] = []
    manager.result.connect(lines.append)

    manager.connect_slave()
    wait_until(lambda: lines)

    assert not manager.is_connected, "the slave at station 1 did not answer for station 2"


def test_the_trace_calls_ccp_frames_ccp(stack):
    _bus, manager, _demo, _hub, _ctx = stack
    frame = Frame(0.0, "CAN", ccp_slave.COMMAND_ID, False, False, True, b"\x01")
    assert manager.classify(frame) is None, "nothing until the ids are given"

    manager.set_ids(ccp_slave.COMMAND_ID, ccp_slave.RESPONSE_ID, False)

    assert manager.classify(frame) == "CCP cmd"


def test_the_pane_asks_for_a_station_only_where_one_is_needed(app, tmp_path, monkeypatch):
    from pycangui.ui.main_window import MainWindow

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    window = MainWindow()
    view = window.xcp_view

    assert view.station.isHidden(), "XCP has no station address"

    window.xcp.set_component("ccp-builtin")
    view._engine_changed()

    assert not view.station.isHidden(), "CCP does, and cannot connect without one"
    window.close()
