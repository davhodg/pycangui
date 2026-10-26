# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Selecting another channel stops what was repeating on the one left behind.

The protocol panes work through the selected channel, and are told the old
one disconnected when the selection moves. It has not: it is still open, so
a cyclic message or SYNC started on it went on with nothing ticked anywhere
for it, and Stop all cyclic could not reach it.
"""

import time

import can
import pytest
from PySide6.QtCore import QSettings, Qt

from pycangui.ui.main_window import MainWindow
from pycangui.ui.tx_view import COL_CYCLIC

CHANNEL = "vcan_channel_switch"


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    win = MainWindow()
    win.channels.active_bus().connect_bus("virtual", CHANNEL, 500_000, False)
    app.processEvents()
    yield win
    win.close()


@pytest.fixture
def listener():
    bus = can.Bus(interface="virtual", channel=CHANNEL)
    yield bus
    bus.shutdown()


def still_sending(app, listener, can_id: int) -> bool:
    """Whether frames of this id are still arriving, after what was on its way."""
    app.processEvents()
    time.sleep(0.1)
    while listener.recv(timeout=0) is not None:
        pass
    end = time.monotonic() + 0.3
    while time.monotonic() < end:
        app.processEvents()
        msg = listener.recv(timeout=0.02)
        if msg is not None and msg.arbitration_id == can_id:
            return True
    return False


def select_another(app, window) -> None:
    window.channels.add("CAN 2")
    window.channels.set_active("CAN 2")
    app.processEvents()


def test_cyclic_messages_stop_on_the_channel_left_behind(app, window, listener):
    tx = window._transmit_panes()[0]
    row = tx.add_message({"kind": "raw", "id": "123", "data": "01 02", "period": 20})
    tx.item(row).setCheckState(COL_CYCLIC, Qt.Checked)
    assert still_sending(app, listener, 0x123)

    select_another(app, window)
    assert window.channels.get("CAN 1").is_connected, "the old channel is still open"
    assert not still_sending(app, listener, 0x123)
    assert tx.item(row).checkState(COL_CYCLIC) == Qt.Unchecked
    assert "cyclic message(s) stopped" in window.log.toPlainText()


def test_sync_stops_on_the_channel_left_behind(app, window, listener):
    window.canopen_view.sync_btn.setChecked(True)
    assert still_sending(app, listener, 0x080)

    select_another(app, window)
    assert not still_sending(app, listener, 0x080)
    assert not window.canopen_view.sync_btn.isChecked()
