"""Frames get from the bus to the trace, through the whole window.

Every other test builds one piece at a time, which is how a change to the
connect bar's channel widget could break the demo device -- its only caller
asked the widget for its text, and a combo box has none -- without a single
test noticing.  This drives the window the way a person does.
"""

import time

import pytest
from PySide6.QtCore import QSettings

from pycangui.ui.main_window import MainWindow


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    win = MainWindow()
    yield win
    win._demo_action.setChecked(False)
    win.close()


def run_for(app, seconds, until=None):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        if until is not None and until():
            return
        time.sleep(0.01)


def test_the_demo_device_puts_traffic_in_the_trace(app, window):
    """Connect, start the demo, see frames -- the path a first run takes."""
    window.connect_bar.button.setChecked(True)
    app.processEvents()
    assert window.channels.active_bus().is_connected, window.log.toPlainText()

    window._demo_action.setChecked(True)
    assert window._demo is not None, f"the demo failed to start: {window.log.toPlainText()}"
    # Bus load is only recomputed on its own 500 ms timer, so wait for that
    # too rather than racing it.
    bus = window.channels.active_bus()
    run_for(app, 4.0, until=lambda: window.trace.model.rowCount() > 10 and bus.load_percent > 0)

    captured = window.trace.model.rowCount()
    shown = window.trace.table.model().rowCount()
    assert captured > 10, f"no traffic reached the trace: {window.log.toPlainText()}"
    assert shown == captured, "and none of it is being filtered out"
    assert window._frame_count == captured
    assert bus.load_percent > 0, "bus load must move too"


def test_the_demo_is_decoded_not_just_listed(app, window):
    """The trace labels CANopen ids, so a heartbeat is not just an id."""
    window.connect_bar.button.setChecked(True)
    app.processEvents()
    window._demo_action.setChecked(True)
    run_for(app, 3.0, until=lambda: window.trace.model.rowCount() > 10)

    kinds = {window.trace.model.data(window.trace.model.index(row, 4)) for row in range(20)}
    assert any("Heartbeat" in str(k) or "PDO" in str(k) for k in kinds), kinds


def test_the_demo_refuses_a_non_virtual_interface_without_crashing(app, window):
    window.connect_bar.interface.setCurrentText("socketcan")
    app.processEvents()
    window._demo_action.setChecked(True)
    assert window._demo is None
    assert not window._demo_action.isChecked(), "and it unticks itself"
    assert "virtual" in window.log.toPlainText()
