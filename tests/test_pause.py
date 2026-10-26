# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Pausing pycangui's own traffic, and stopping all of it.

A flash wants the bus to itself: a node rebooting into its bootloader was
identified, and its PDOs read, over SDO half way through. A pause holds back
what pycangui sends by itself and lets it go again afterwards; Stop all ends
everything that repeats for good. What is done by hand is never held.
"""

import time

import pytest
from PySide6.QtCore import QCoreApplication, QSettings

from pycangui.canopen.manager import CanopenManager
from pycangui.core.bus import BusManager
from pycangui.core.pause import BY_HAND, TrafficPause, described


# --- who holds it -------------------------------------------------------------------------
def test_it_lifts_only_when_nobody_holds_it():
    pause = TrafficPause()
    said = []
    pause.changed.connect(said.append)
    pause.hold("UDS transfer")
    pause.set_by_hand(True)
    pause.hold("UDS transfer")
    assert pause.holders == ["UDS transfer", BY_HAND] and said == [True]
    pause.release("UDS transfer")
    assert pause.paused, "the flash ending does not undo a pause set by hand"
    pause.set_by_hand(False)
    assert not pause.paused and said == [True, False]
    pause.release("nobody")
    assert said == [True, False]


def test_who_holds_it_is_said_in_words():
    assert described(["UDS transfer"]) == "UDS transfer"
    assert described(["a", "b", "c"]) == "a, b and c"


# --- CANopen ----------------------------------------------------------------------------------
@pytest.fixture
def manager(app):
    bus = BusManager()
    manager = CanopenManager(bus)
    bus.connect_bus("virtual", "vcan_pause", 500000, False)
    yield manager
    bus.disconnect_bus()
    manager.shutdown()


def test_canopens_unasked_reads_wait_for_the_pause_to_lift(manager, monkeypatch):
    identified, read = [], []
    monkeypatch.setattr(manager, "identify", identified.append)
    monkeypatch.setattr(manager, "read_pdo_config", read.append)
    manager.set_paused(True)
    manager.identify_unasked(5)
    manager.node_seen.emit(5, "INITIALISING")  # a boot-up, as a node rebooting into its loader
    manager.refresh_pdo_config(6)
    QCoreApplication.processEvents()
    assert identified == [] and read == []
    manager.set_paused(False)
    assert identified == [5] and read == [6], "each once, when it lifts"


def test_sync_and_time_are_held_and_go_on_as_they_were(manager):
    manager.start_sync(0.05, 4)
    manager.start_time(10)
    manager.set_paused(True)
    assert not manager.sync_running and not manager.time_running
    manager.set_paused(False)
    assert manager.sync_running and manager.time_running
    assert manager._sync_settings == (0.05, 4), "with the counter it had"
    manager.stop_sync()
    manager.stop_time()


def test_a_producer_stopped_by_hand_during_a_pause_stays_stopped(manager):
    manager.start_sync(0.05)
    manager.set_paused(True)
    manager.stop_sync()
    manager.set_paused(False)
    assert not manager.sync_running


# --- XCP and CCP ------------------------------------------------------------------------------
def test_measurement_polling_waits_while_paused(app, tmp_path, monkeypatch):
    from pycangui.core.context import Context
    from pycangui.core.hooks import Hooks
    from pycangui.core.signals import SignalHub
    from pycangui.xcp.manager import XcpManager

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    ctx = Context(log=print)
    xcp = XcpManager(BusManager(), Hooks(ctx), SignalHub(), ctx)
    asked = []
    monkeypatch.setattr(xcp, "_submit", lambda label, fn: asked.append(label))
    xcp._connected = True
    xcp._polled = {"Speed": object()}
    ctx.pause.set_by_hand(True)
    xcp._poll()
    assert asked == [] and xcp.paused
    ctx.pause.set_by_hand(False)
    xcp._poll()
    assert asked == ["poll Speed"]


# --- the window -------------------------------------------------------------------------------
@pytest.fixture
def window(app, tmp_path, monkeypatch):
    from pycangui.ui.main_window import MainWindow

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    win = MainWindow()
    yield win
    win.close()


def test_a_uds_transfer_holds_the_pause_while_it_runs(window):
    window.uds.transferring.emit(True)
    assert window.ctx.pause.holders == ["UDS transfer"]
    assert window.canopen.paused and window.xcp.paused
    assert "paused" in window.pause_status.text() and "UDS transfer" in window.pause_status.text()
    action = window.pause_status.pause_action
    assert not action.isEnabled() and "UDS transfer" in action.toolTip(), "the flash gives it back"
    window.uds.transferring.emit(False)
    assert not window.ctx.pause.paused and action.isEnabled()
    assert "running" in window.pause_status.text()


def test_by_hand_from_the_status_bar_or_tools_menu(window):
    status = window.pause_status
    shown = [a.text() for a in status.menu().actions()]
    assert shown == ["Pause automatic traffic", "Stop all repeating traffic"]
    tools = window.settings_menu.parent()
    assert status.pause_action in tools.actions() and status.stop_action in tools.actions()
    status.pause_action.trigger()
    assert window.ctx.pause.holders == [BY_HAND]
    assert "Automatic traffic paused" in window.log.toPlainText()
    status.pause_action.trigger()
    assert not window.ctx.pause.paused


def test_stop_all_reaches_cyclic_transmit_and_the_canopen_producers(window, monkeypatch):
    from pycangui.ui.tx_view import TxView

    stopped = []
    monkeypatch.setattr(TxView, "stop_all", lambda self: stopped.append(self))
    window.canopen_view.sync_btn.blockSignals(True)
    window.canopen_view.sync_btn.setChecked(True)
    window.canopen_view.sync_btn.blockSignals(False)
    window.pause_status.stop_action.trigger()
    assert stopped, "every transmit pane"
    assert not window.canopen_view.sync_btn.isChecked()
    assert "Stopped all repeating traffic" in window.log.toPlainText()
    assert not window.ctx.pause.paused, "for good, not a pause"


def wait_until(pred, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not pred():
        QCoreApplication.processEvents()
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.005)
