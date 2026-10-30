# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""A wait after boot-up before pycangui's own reads of a node.

Some devices send their boot-up before their application answers SDO, and
the reads pycangui makes unasked -- Identify, the PDO configuration read
again -- then time out. CANopen settings can hold them back for a while.
"""

import time

import pytest
from PySide6.QtCore import QCoreApplication

from pycangui.canopen.manager import BOOT_UP, CanopenManager
from pycangui.core.bus import BusManager
from pycangui.ui import canopen_settings

NODE = 5


def wait_until(pred, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not pred():
        QCoreApplication.processEvents()
        if time.monotonic() > deadline and not pred():
            raise AssertionError("timed out")
        time.sleep(0.005)


@pytest.fixture
def manager(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    bus = BusManager()
    manager = CanopenManager(bus)
    bus.connect_bus("virtual", "vcan_boot_settle", 500000, False)
    yield manager
    bus.disconnect_bus()
    manager.shutdown()


@pytest.fixture
def asked(manager, monkeypatch):
    out = []
    monkeypatch.setattr(manager, "identify", out.append)
    return out


def boot_up_then_identify(manager):
    manager._on_state(NODE, BOOT_UP)  # the manager hears the boot-up before the pane does
    manager.identify_unasked(NODE)


def test_no_wait_unless_one_is_set(manager, asked):
    boot_up_then_identify(manager)
    assert asked == [NODE]


def test_identify_waits_after_a_boot_up(manager, asked):
    manager.boot_settle_ms = 150
    began = time.monotonic()
    boot_up_then_identify(manager)
    QCoreApplication.processEvents()
    assert asked == [], "held while the node settles"
    wait_until(lambda: asked == [NODE])
    assert time.monotonic() - began >= 0.14


def test_a_second_boot_up_starts_the_wait_again(manager, asked):
    manager.boot_settle_ms = 200
    boot_up_then_identify(manager)
    time.sleep(0.12)
    manager._on_state(NODE, BOOT_UP)
    began = time.monotonic()
    wait_until(lambda: asked == [NODE])
    assert time.monotonic() - began >= 0.18


def test_paused_at_the_end_of_the_wait_it_goes_on_waiting(manager, asked):
    manager.boot_settle_ms = 50
    boot_up_then_identify(manager)
    manager.set_paused(True)
    time.sleep(0.12)
    QCoreApplication.processEvents()
    assert asked == [], "the pause still holds it"
    manager.set_paused(False)
    assert asked == [NODE]


def test_a_node_removed_while_it_settles_is_not_asked(manager, asked):
    manager.boot_settle_ms = 50
    boot_up_then_identify(manager)
    manager.remove_node(NODE)
    time.sleep(0.12)
    QCoreApplication.processEvents()
    assert asked == []


def test_the_setting_is_kept_and_applied(app, tmp_path, monkeypatch):
    from pycangui.core.context import Context

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    ctx = Context()
    assert canopen_settings.load(ctx).boot_settle_ms == 0
    chosen = canopen_settings.CanopenSettings(boot_settle_ms=250)
    canopen_settings.save(ctx, chosen)
    assert canopen_settings.load(ctx).boot_settle_ms == 250
    assert canopen_settings.changes(canopen_settings.CanopenSettings(), chosen)

    class Manager:
        def __getattr__(self, name):
            return lambda *a, **k: None

    target = Manager()
    canopen_settings.apply(target, chosen)
    assert target.boot_settle_ms == 250


def test_the_dialog_gives_the_wait_chosen(app):
    dialog = canopen_settings.CanopenSettingsDialog(None, canopen_settings.CanopenSettings())
    assert dialog.boot_settle.value() == 0
    dialog.boot_settle.setValue(300)
    assert dialog.settings().boot_settle_ms == 300
