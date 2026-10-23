# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Cyclic messages: known, whose, and stopped -- one by one, failures said.

Where the adapter keeps the time (IXXAT's scheduler, socketcan's BCM), a task
that is not stopped is a message that goes on after the channel has closed,
with nothing in pycangui to show it. So every cyclic message started through
the bus manager is listed with its owner, and stopping them is done one at a
time: python-can's own stop_all leaves the rest undefined once one raises.
"""

import pytest

from pycangui.core.bus import TIMED_HERE, BusManager


@pytest.fixture
def bus(app):
    made = BusManager("CAN 1")
    made.connect_bus("virtual", "vcan_cyclic_tasks", 500_000, False)
    yield made
    made.disconnect_bus()


def test_what_is_repeating_is_known_and_whose(bus):
    task = bus.send_periodic(0x123, b"\x01\x02", 0.05, owner="CAN Transmit: Speed")
    (entry,) = bus.cyclic()
    assert (entry.owner, entry.can_id, entry.data, entry.period_s) == (
        "CAN Transmit: Speed",
        0x123,
        b"\x01\x02",
        0.05,
    )
    assert entry.timed_by == TIMED_HERE, "a thread here, on the virtual bus"
    task.stop()
    assert bus.cyclic() == [], "forgotten once stopped"


def test_one_started_behind_its_back_is_known_too(bus):
    """The console, a script or a library talking to python-can directly."""
    import can

    raw = bus.bus.send_periodic(can.Message(arbitration_id=0x80, data=b""), 0.05)
    assert bus.untracked_tasks() == [raw]
    raw.stop()


def test_one_that_will_not_stop_is_said_and_the_rest_still_stop(bus):
    errors = []
    bus.error.connect(errors.append)
    stubborn = bus.send_periodic(0x100, b"\x00", 0.05)
    other = bus.send_periodic(0x200, b"\x00", 0.05)
    real_stop = stubborn.stop

    def refuse(*_a, **_k):
        raise OSError("driver said no")

    stubborn.stop = refuse
    try:
        bus.disconnect_bus()
        assert not bus.is_connected, "the channel closes all the same"
        assert other.stopped, "the one after it was still stopped"
        assert len(errors) == 1 and "0x100" in errors[0] and "driver said no" in errors[0]
    finally:
        real_stop()


def test_a_transmit_row_that_will_not_stop_says_so(app, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings, Qt

    from pycangui.ui.main_window import MainWindow
    from pycangui.ui.tx_view import COL_CYCLIC

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    window = MainWindow()
    try:
        window.channels.active_bus().connect_bus("virtual", "vcan_cyclic_row", 500_000, False)
        tx = window._transmit_panes()[0]
        row = tx.add_message({"kind": "raw", "id": "123", "data": "01", "period": 50})
        tx.item(row).setCheckState(COL_CYCLIC, Qt.Checked)
        (entry,) = window.channels.active_bus().cyclic()
        assert entry.owner.startswith("CAN Transmit")
        task = tx._tasks[row]
        real_stop = task.stop
        task.stop = lambda *_a, **_k: (_ for _ in ()).throw(OSError("driver said no"))
        tx.item(row).setCheckState(COL_CYCLIC, Qt.Unchecked)
        app.processEvents()
        assert "could not be stopped" in window.log.toPlainText()
        real_stop()
    finally:
        window.close()
