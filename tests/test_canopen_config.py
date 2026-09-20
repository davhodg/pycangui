# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""PDO configuration, DCF save/apply, store/restore and SYNC, against the demo node."""

import time

import pytest
from PySide6.QtCore import QCoreApplication

from pycangui import resources
from pycangui.canopen import PdoEntry
from pycangui.canopen.manager import CanopenManager
from pycangui.core.bus import BusManager


class _Empty:
    """Stands in for a config that has not arrived yet, so a wait can ask
    about its entries without checking for None first."""

    entries = ()


def wait_until(pred, timeout=8.0):
    deadline = time.monotonic() + timeout
    while not pred():
        QCoreApplication.processEvents()
        if time.monotonic() > deadline:
            raise AssertionError("timed out")
        time.sleep(0.005)


@pytest.fixture
def stack(app, tmp_path, monkeypatch, demo_device):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    bus = BusManager()
    manager = CanopenManager(bus)
    bus.connect_bus("virtual", "vcan_cfg", 500000, False)
    demo = demo_device(bus, kinds=["canopen_device"])
    manager.load_eds(5, str(resources.path("demo.eds")))
    wait_until(lambda: manager.node(5) is not None and len(manager.node(5).object_dictionary))
    yield manager, demo, tmp_path
    manager.shutdown()
    bus.disconnect_bus()


def named(manager, node_id=5):
    return {f"{c.direction}{c.number}": c for c in manager.pdo_configs(node_id)}


def test_pdo_configs_from_the_eds(stack):
    manager, _demo, _tmp = stack
    # Wait for the mapping to be *complete*, not merely present. The configs
    # are read over SDO one entry at a time, so a truthy list is a list that
    # may still be filling -- and asserting on it caught TPDO1 with two of its
    # three entries whenever the machine was busy enough.
    wait_until(lambda: len(named(manager).get("TPDO1", _Empty()).entries) == 3)
    configs = named(manager)
    assert set(configs) >= {"TPDO1", "RPDO1"}

    tpdo = configs["TPDO1"]
    assert tpdo.cob_id == 0x185 and tpdo.enabled
    assert [e.name for e in tpdo.entries] == [
        "Statusword",
        "Measurements.Motor speed",
        "Measurements.Odometer",
    ]
    assert tpdo.bits == 64
    assert tpdo.transmission_type == 255
    assert tpdo.transmission_text() == "255 (event, profile)"

    rpdo = configs["RPDO1"]
    assert rpdo.cob_id == 0x205
    assert [(e.index, e.subindex, e.bits) for e in rpdo.entries] == [(0x2001, 0, 16)]


def test_read_and_write_pdo_config(stack):
    manager, demo, _tmp = stack
    messages: list[str] = []
    manager.message.connect(lambda text, _level: messages.append(text))
    manager.read_pdo_config(5)
    wait_until(lambda: any("PDO(s) configured" in m for m in messages))

    config = next(c for c in manager.pdo_configs(5) if c.direction == "RPDO")
    config.event_timer_ms = 0
    config.transmission_type = 254
    config.entries = [PdoEntry(0x2001, 0, 16, "Speed demand")]
    n = len(messages)
    manager.write_pdo_config(config)
    wait_until(lambda: len(messages) > n)
    assert "RPDO1 written to node 5" in messages[-1]
    # the node really has the new transmission type
    assert demo["canopen_device"].state.device.get_data(0x1400, 2)[0] == 254


def test_dcf_save_and_apply(stack):
    manager, demo, tmp_path = stack
    messages: list[str] = []
    manager.message.connect(lambda text, _level: messages.append(text))

    # change something on the node, capture it, change it back, then restore it
    manager.sdo_write(5, 0x2001, 0, "-1234")
    wait_until(
        lambda: (
            demo["canopen_device"].state.device.get_data(0x2001, 0)
            == (-1234).to_bytes(2, "little", signed=True)
        )
    )

    dcf = tmp_path / "node5.dcf"
    manager.save_dcf(5, str(dcf))
    wait_until(lambda: any("DCF written" in m for m in messages), timeout=20)
    assert dcf.is_file()
    text = dcf.read_text(errors="replace")
    assert "[DeviceComissioning]" in text and "ParameterValue" in text

    manager.sdo_write(5, 0x2001, 0, "0")
    wait_until(lambda: demo["canopen_device"].state.device.get_data(0x2001, 0) == b"\x00\x00")

    n = len(messages)
    manager.apply_dcf(5, str(dcf))
    wait_until(
        lambda: any("parameters written from the DCF" in m for m in messages[n:]), timeout=20
    )
    assert demo["canopen_device"].state.device.get_data(0x2001, 0) == (-1234).to_bytes(
        2, "little", signed=True
    )


def test_store_restore_and_sync(stack):
    manager, _demo, _tmp = stack
    messages: list[str] = []
    manager.message.connect(lambda text, _level: messages.append(text))

    manager.store_parameters(5)  # the demo node has no 0x1010: expect a clean error
    wait_until(lambda: messages)
    assert "Node 5:" in messages[-1]

    assert not manager.sync_running
    manager.start_sync(0.05)
    assert manager.sync_running
    wait_until(lambda: any("SYNC started" in m for m in messages))
    manager.stop_sync()
    assert not manager.sync_running
    wait_until(lambda: any("SYNC stopped" in m for m in messages))


# --- where things sit in the pane -------------------------------------------------------
def test_the_object_dictionary_is_the_first_tab(app, tmp_path, monkeypatch):
    """The dictionary, the live PDOs and LSS all want the height, and
    sharing it left every one of them too short to read."""
    from PySide6.QtCore import QSettings

    from pycangui.ui.main_window import MainWindow

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    window = MainWindow()
    tabs = window.canopen_view.bottom_tabs

    assert tabs.tabText(0) == "Object dictionary"
    assert [tabs.tabText(i) for i in range(tabs.count())] == [
        "Object dictionary",
        "Live PDOs",
        "PDO configuration",
        "Emergencies",
        "Faults",
        "LSS",
    ]
    window.close()


def test_the_sync_rate_lives_in_the_settings(app, tmp_path, monkeypatch):
    """A rate is a fact about the bus, agreed once, not a decision to take
    each time synchronous PDOs are wanted."""
    from PySide6.QtCore import QSettings

    from pycangui.ui import canopen_settings
    from pycangui.ui.main_window import MainWindow

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    window = MainWindow()
    view = window.canopen_view

    assert not hasattr(view, "sync_period"), "no box beside the button any more"
    assert view.sync_btn.isCheckable()

    window.ctx.settings.set(canopen_settings.SYNC_KEY, 250)
    started: list[float] = []
    monkeypatch.setattr(window.canopen, "start_sync", started.append)
    view.sync_btn.setChecked(True)

    assert started == [0.25], "the period comes from the settings"
    window.close()
