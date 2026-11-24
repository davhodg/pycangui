# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""A device: what a plugin drives, whatever protocol reaches it (plugin API 3).

A CANopen node is the one kind pycangui has of itself, so it is tested here
against the demo device on a virtual bus, and the parts that need no bus
against the manager alone.
"""

import struct
import time

import pytest

from pycangui import resources
from pycangui.canopen import PdoConfig, PdoEntry
from pycangui.canopen.manager import CanopenManager
from pycangui.core.bus import BusManager
from pycangui.core.device import Devices

DEMO_NODE = 5


def wait_for(app, pred, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not pred():
        app.processEvents()
        if time.monotonic() > deadline:
            raise AssertionError("timed out")
        time.sleep(0.005)


@pytest.fixture
def manager(app):
    manager = CanopenManager(BusManager())
    yield manager
    manager.shutdown()


@pytest.fixture
def stack(app, demo_device):
    bus = BusManager()
    manager = CanopenManager(bus)
    bus.connect_bus("virtual", "vcan_device", 500000, False)
    demo = demo_device(bus, kinds=["canopen_device"])
    yield bus, manager, demo
    bus.disconnect_bus()
    manager.shutdown()


def test_a_node_is_one_device_kept_for_its_node_id(manager):
    devices = Devices(manager)
    device = devices.device(DEMO_NODE)
    assert device is devices.device(DEMO_NODE), "the same signals every time"
    assert device.key == DEMO_NODE and device.label
    assert devices.device(0) is None and devices.device(128) is None
    assert devices.device("Node 5") is None and devices.device(True) is None


def test_the_list_is_said_to_change_only_when_it_does(manager, monkeypatch):
    devices = Devices(manager)
    said = []
    devices.changed.connect(lambda: said.append(1))
    monkeypatch.setattr(manager, "nodes", lambda: [DEMO_NODE])
    manager.node_seen.emit(DEMO_NODE, "OPERATIONAL")
    manager.node_seen.emit(DEMO_NODE, "OPERATIONAL")  # the next heartbeat
    assert len(said) == 1
    assert [device.key for device in devices.devices()] == [DEMO_NODE]


def test_its_state_is_said_when_it_changes_and_when_it_is_lost(manager):
    device = Devices(manager).device(DEMO_NODE)
    said = []
    device.state_changed.connect(lambda: said.append(device.state()))
    manager.nmt_states[DEMO_NODE] = "OPERATIONAL"
    manager.node_seen.emit(DEMO_NODE, "OPERATIONAL")
    manager.node_seen.emit(DEMO_NODE, "OPERATIONAL")
    manager.node_seen.emit(DEMO_NODE + 1, "OPERATIONAL")  # another node's
    manager.lost_nodes.add(DEMO_NODE)
    manager.node_lost.emit(DEMO_NODE)
    assert said == [("OPERATIONAL", False), ("OPERATIONAL", True)]


def test_its_state_is_set_by_nmt(manager, monkeypatch):
    sent = []
    monkeypatch.setattr(manager, "nmt", lambda node_id, command: sent.append((node_id, command)))
    device = Devices(manager).device(DEMO_NODE)
    assert "OPERATIONAL" in device.states
    device.set_state("OPERATIONAL")
    assert sent == [(DEMO_NODE, "OPERATIONAL")]


def test_what_it_receives_is_its_enabled_rpdos(manager, monkeypatch):
    def config(direction, number, enabled, *where):
        entries = [PdoEntry(index, sub, 16) for index, sub in where]
        return PdoConfig(DEMO_NODE, direction, number, "", 0x200, enabled, 255, entries=entries)

    monkeypatch.setattr(
        manager,
        "pdo_configs",
        lambda _n: [
            config("RPDO", 1, True, (0x6040, 0), (0x60FF, 0)),
            config("RPDO", 2, False, (0x607A, 0)),
            config("TPDO", 1, True, (0x6041, 0)),
        ],
    )
    device = Devices(manager).device(DEMO_NODE)
    assert device.receives() == [(0x6040, 0, "RPDO1"), (0x60FF, 0, "RPDO1")]


def test_a_mapping_or_an_eds_changing_is_said(manager):
    device = Devices(manager).device(DEMO_NODE)
    said = []
    device.changed.connect(lambda: said.append(1))
    manager.pdo_config.emit(DEMO_NODE)
    manager.eds_loaded.emit(DEMO_NODE, "drive.eds", "A drive")
    manager.eds_loaded.emit(DEMO_NODE + 1, "drive.eds", "A drive")
    assert len(said) == 2


def test_with_no_eds_it_does_not_say_what_it_has(manager):
    device = Devices(manager).device(DEMO_NODE)
    assert not device.available()
    assert device.has(0x6041) is None
    with pytest.raises(RuntimeError):
        device.read(0x1000, 0)


def test_it_is_read_and_written_by_index_and_knows_its_eds(app, stack):
    _bus, manager, _demo = stack
    device = Devices(manager).device(DEMO_NODE)
    manager.load_eds(DEMO_NODE, str(resources.path("demo.eds")))
    wait_for(app, device.available)
    wait_for(app, lambda: device.has(0x1000) is not None)
    assert device.has(0x1000) and device.has(0x1018, 1)
    assert device.has(0x1018, 0x7F) is False and device.has(0x7FFF) is False

    answers = []
    device.background(lambda: device.read(0x1000, 0), lambda *answer: answers.append(answer))
    wait_for(app, lambda: answers)
    data, error = answers[0]
    assert error is None and isinstance(data, bytes) and len(data) == 4

    period = struct.pack("<H", 750)
    done = []
    device.background(
        lambda: (device.write(0x1017, 0, period), device.read(0x1017, 0))[1],
        lambda *answer: done.append(answer),
    )
    wait_for(app, lambda: done)
    assert done[0] == (period, None)
