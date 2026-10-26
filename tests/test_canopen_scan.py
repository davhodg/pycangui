# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Scanning for CANopen nodes that send no heartbeat: an SDO read of 0x1000 to
each node id, started by hand, with progress and Stop."""

import logging
import time

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QMessageBox

from pycangui.canopen.manager import FOUND_BY_SCAN, CanopenManager
from pycangui.core.bus import BusManager
from pycangui.core.events import WARNING


def wait_until(pred, timeout=8.0):
    deadline = time.monotonic() + timeout
    while not pred():
        QCoreApplication.processEvents()
        if time.monotonic() > deadline:
            raise AssertionError("timed out")
        time.sleep(0.005)


@pytest.fixture
def stack(app, tmp_path, monkeypatch, demo_device):
    """The demo's CANopen device, node 5, on a virtual bus, and a manager on it."""
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    bus = BusManager()
    manager = CanopenManager(bus)
    bus.connect_bus("virtual", "vcan_scan", 500000, False)
    demo_device(bus, kinds=["canopen_device"])
    wait_until(lambda: 5 in manager.last_heartbeat)
    yield manager
    manager.shutdown()
    bus.disconnect_bus()


def scanned(manager):
    """Start a scan, wait for its end, and give what it said: (found, asked, stopped)."""
    finished = []
    manager.scan_finished.connect(lambda *args: finished.append(args))
    assert manager.scan()
    wait_until(lambda: finished, timeout=30)
    return finished[0]


def test_a_node_that_sends_no_heartbeat_is_found(stack, caplog):
    """Node 5 is made to look silent; ids 6 and 7 have nobody, and their
    timeouts are not error lines."""
    manager = stack
    manager.set_node_ids({5, 6, 7})
    seen = []
    manager.node_seen.connect(lambda node_id, state: seen.append((node_id, state)))
    manager.last_heartbeat.clear()  # as if its heartbeat were off
    with caplog.at_level(logging.WARNING, logger="canopen"):
        found, asked, stopped = scanned(manager)
    assert (found, asked, stopped) == ([5], 3, False)
    assert (5, FOUND_BY_SCAN) in seen and all(n == 5 for n, _ in seen)
    assert manager.node(5) is not None and manager.node(6) is None
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_a_scan_asks_only_the_chosen_ids_not_heard_from(stack):
    manager = stack
    manager.set_node_ids({4, 5, 6})
    assert manager.scan_ids() == [4, 6], "5's heartbeat is heard"


def test_a_scan_is_stopped_part_way(stack):
    manager = stack
    manager.set_node_ids(range(1, 128))
    finished = []
    manager.scan_finished.connect(lambda *args: finished.append(args))
    assert manager.scan()
    assert not manager.scan(), "one at a time"
    manager.stop_scan()
    wait_until(lambda: finished, timeout=30)
    _found, asked, stopped = finished[0]
    assert stopped and asked == 126 and not manager.scanning


def test_no_scan_without_a_bus(app):
    manager = CanopenManager(BusManager())
    said = []
    manager.message.connect(lambda text, level: said.append(level))
    assert not manager.scan()
    assert said == [WARNING], "said why, once"


# --- the pane ------------------------------------------------------------------------------------
@pytest.fixture
def window(app, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings

    from pycangui.ui.main_window import MainWindow

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    made = MainWindow()
    yield made
    made.close()


def test_scan_is_in_the_add_open_menu(window):
    view = window.canopen_view
    texts = [a.text() for a in view.add_menu_btn.menu().actions()]
    assert any(t.startswith("Scan") for t in texts)


def test_the_pane_asks_first_then_shows_a_bar_with_stop(window, monkeypatch):
    from pycangui.ui import canopen_view

    view = window.canopen_view
    manager = view.manager
    boxes, started = [], []
    window._connect_active("virtual", "vscanpane", 500000, False)
    monkeypatch.setattr(manager, "scan_ids", lambda: [1, 2, 3])
    monkeypatch.setattr(manager, "scan", lambda: started.append(True) or True)
    answers = [QMessageBox.Cancel, QMessageBox.Yes]
    monkeypatch.setattr(canopen_view.messages, "question", lambda *a, **k: answers.pop(0))
    monkeypatch.setattr(canopen_view.messages, "information", lambda *a, **k: boxes.append(a[2]))

    view.scan_for_nodes()
    assert not started, "Cancel scans nothing"
    view.scan_for_nodes()
    assert started and view._scan_progress is not None
    assert view._scan_progress.labelText(), "a bar, with Stop"

    stopped = []
    monkeypatch.setattr(manager, "stop_scan", lambda: stopped.append(True))
    view._on_scan_progress(2, 1, 3)
    assert view._scan_progress.value() == 1
    view._on_scan_finished([], 3, False)
    assert view._scan_progress is None and len(boxes) == 1, "the bar goes, and a box says"
    window._disconnect_active()


def test_stop_all_ends_a_scan(window, monkeypatch):
    stopped = []
    monkeypatch.setattr(window.canopen.__class__, "stop_scan", lambda self: stopped.append(1))
    window.ctx.pause.stop_all()
    assert stopped
