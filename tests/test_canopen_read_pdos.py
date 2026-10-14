# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Reading a node's PDO configuration from it without being asked, or not."""

import time

import pytest
from canopen.nmt import NMT_STATES
from PySide6.QtCore import QCoreApplication

from pycangui import resources
from pycangui.canopen.manager import CanopenManager
from pycangui.core.bus import BusManager
from pycangui.core.context import Context
from pycangui.ui import canopen_settings

NODE = 5


def wait_until(pred, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not pred():
        QCoreApplication.processEvents()
        if time.monotonic() > deadline and not pred():
            raise AssertionError("timed out")
        time.sleep(0.005)


def settle(manager):
    """Until the worker has got through everything queued before now."""
    done = []
    manager.background(lambda: None, lambda _r, _e: done.append(True))
    wait_until(lambda: done)


@pytest.fixture
def bus_and_manager(app, demo_device, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    bus = BusManager()
    manager = CanopenManager(bus)
    bus.connect_bus("virtual", "vcan_read_pdos", 500000, False)
    demo_device(bus, kinds=["canopen_device"])
    seen = []
    manager.node_seen.connect(lambda n, state: seen.append(n))
    wait_until(lambda: seen)
    yield manager
    bus.disconnect_bus()
    manager.shutdown()


def load_eds(manager):
    loaded = []
    manager.eds_loaded.connect(lambda n, p, name: loaded.append(n))
    manager.load_eds(NODE, str(resources.path("demo.eds")))
    wait_until(lambda: loaded)
    settle(manager)


def test_on_both_directions_are_read_from_the_node_once_its_eds_is_loaded(bus_and_manager):
    """The RPDOs used to stay the EDS's until Read PDO config was pressed --
    and CAN Transmit sent by them."""
    load_eds(bus_and_manager)
    assert bus_and_manager.read_from_node(NODE) == (True, True)
    assert NODE in bus_and_manager.heartbeat_times, "0x1017 asked as well"


def test_off_nothing_is_read_and_the_eds_is_decoded_by(bus_and_manager):
    bus_and_manager.read_pdos_when_found = False
    load_eds(bus_and_manager)
    assert bus_and_manager.read_from_node(NODE) == (False, False)
    node = bus_and_manager.node(NODE)
    enabled = [m for m in node.tpdo.map.values() if m.cob_id is not None and m.enabled]
    assert enabled and all(getattr(m, "_pycangui_decoded", False) for m in enabled)


@pytest.mark.parametrize("on", [True, False])
def test_a_boot_up_is_read_again_where_it_is_on(bus_and_manager, monkeypatch, on):
    """A node that has started again may be set up differently."""
    load_eds(bus_and_manager)
    bus_and_manager.read_pdos_when_found = on
    asked = []
    monkeypatch.setattr(bus_and_manager, "read_pdo_config", asked.append)
    bus_and_manager.node_seen.emit(NODE, NMT_STATES[0])
    bus_and_manager.node_seen.emit(NODE, NMT_STATES[5])  # an ordinary heartbeat
    QCoreApplication.processEvents()
    assert asked == ([NODE] if on else [])


def test_a_write_to_a_pdo_object_reads_the_configuration_again(bus_and_manager, monkeypatch):
    load_eds(bus_and_manager)
    asked = []
    monkeypatch.setattr(bus_and_manager, "read_pdo_config", asked.append)
    bus_and_manager.sdo_write(NODE, 0x1800, 2, "254")  # TPDO1's transmission type
    settle(bus_and_manager)
    bus_and_manager.sdo_write(NODE, 0x6040, 0, "6")  # not a PDO object
    settle(bus_and_manager)
    assert asked == [NODE]


def test_the_setting_is_kept_and_on_unless_turned_off(tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    ctx = Context(log=print)
    assert canopen_settings.load(ctx).read_pdos is True
    settings = canopen_settings.load(ctx)
    settings.read_pdos = False
    canopen_settings.save(ctx, settings)
    assert canopen_settings.load(ctx).read_pdos is False

    class Manager:
        def __getattr__(self, name):
            return lambda *a, **k: None

    manager = Manager()
    canopen_settings.apply(manager, canopen_settings.load(ctx))
    assert manager.read_pdos_when_found is False
