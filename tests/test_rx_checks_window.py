# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Checking received counters and checksums, as the window does it: from the
trace's right-click, marked in the trace, counted in Signals and Plot, and
said in the Event Log."""

import time

import can
import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QDialog

from pycangui.core import tx_fields as tx
from pycangui.core.bus import Frame
from pycangui.core.rx_checks import SETTING, Check
from pycangui.ui import latest_model, trace_model
from pycangui.ui import main_window as main_window_module
from pycangui.ui.main_window import MainWindow

COUNTER = tx.Counter(at=tx.Placement(0, tx.LOW))
CHECKSUM = tx.Checksum(at=tx.Placement(7), algorithm="sum8")
PAYLOAD = b"\x00\x11\x22\x33\x44\x55\x66\x00"


def made(n: int) -> bytes:
    return tx.apply(PAYLOAD, COUNTER, CHECKSUM, sent=n)


def frame(data: bytes, t: float = 0.0) -> Frame:
    return Frame(t, "CAN1", 0x100, False, False, True, bytes(data))


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    made_window = MainWindow()
    posted = []
    made_window.events.posted.connect(lambda text, level: posted.append(level))
    made_window.posted = posted
    yield made_window
    made_window.close()


def wait_until(app, predicate, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_a_failing_frame_is_marked_counted_and_said_once(app, window):
    window.rx_checks.set(Check(0x100, counter=COUNTER, checksum=CHECKSUM))
    good, bad = frame(made(0)), bytearray(made(1))
    bad[7] ^= 0x55
    frames = [good, frame(bad, 0.1), frame(made(1), 0.2)]  # the last a repeated count
    window._check_frames(frames)

    assert not good.fault and frames[1].fault and frames[2].fault
    series = window.signals.get("Check 100/Checksum faults")
    assert series is not None and series.latest == 1
    assert window.signals.get("Check 100/Counter faults").latest == 1
    assert window.posted.count("warning") == 1, "when it starts, not a line a frame"

    window._check_frames([frame(made(2), 0.3)])
    assert "good" in window.posted, "and when it is right again"


def test_the_trace_shows_each_frame_right_or_wrong(app, window):
    """The check's column, in CAN Transmit's words, green or red; nothing on a
    frame nobody checks."""
    window.rx_checks.set(Check(0x100, checksum=CHECKSUM))
    bad = bytearray(made(0))
    bad[7] ^= 0xFF
    other = Frame(0.0, "CAN1", 0x200, False, False, True, bytes(8))
    window.channels.frames.emit([frame(made(0)), frame(bad, 0.1), other])
    app.processEvents()
    model = window.trace.model
    good, wrong, unchecked = (model.index(r, trace_model.CHECK_COLUMN) for r in range(3))
    assert model.data(good, Qt.DisplayRole) == tx.describe(None, CHECKSUM)
    assert model.data(good, Qt.BackgroundRole) == trace_model.GOOD_COLOUR
    assert model.data(wrong, Qt.BackgroundRole) == trace_model.FAULT_COLOUR
    assert model.data(wrong, Qt.ToolTipRole) == model.frame(1).fault
    assert not model.data(unchecked, Qt.DisplayRole)
    assert model.data(unchecked, Qt.BackgroundRole) is None


def test_latest_per_id_shows_the_newest_and_counts_the_failures(app, window):
    window.rx_checks.set(Check(0x100, checksum=CHECKSUM))
    bad = bytearray(made(0))
    bad[7] ^= 0xFF
    latest = window.trace.latest
    column = latest_model.CHECK_COLUMN
    window.channels.frames.emit([frame(bad), frame(bad, 0.1)])
    app.processEvents()
    index = latest.index(0, column)
    assert latest.data(index, Qt.BackgroundRole) == trace_model.FAULT_COLOUR
    assert latest.data(index, Qt.UserRole) == 2

    window.channels.frames.emit([frame(made(0), 0.2)])
    app.processEvents()
    assert latest.data(index, Qt.BackgroundRole) == trace_model.GOOD_COLOUR
    assert latest.data(index, Qt.UserRole) == 2, "the failures so far stay counted"
    assert tx.describe(None, CHECKSUM) in latest.data(index, Qt.DisplayRole)


def test_the_trace_asks_for_a_check_on_the_frame_chosen(app, window):
    trace = window.trace
    asked = []
    trace.check_requested.disconnect()
    trace.check_requested.connect(asked.append)
    trace.on_frames([frame(made(0))])
    trace.table.setCurrentIndex(trace.table.model().index(0, 0))
    action = next(a for a in trace.table.actions() if "Check" in a.text())
    action.trigger()
    assert len(asked) == 1 and asked[0].can_id == 0x100


def test_the_dialog_sets_the_check_and_the_workspace_keeps_it(app, window, monkeypatch):
    class Dialog:
        def __init__(self, *_args, **kwargs):
            assert kwargs.get("receiving"), "worded for a message a device sends"

        def exec(self):
            return QDialog.Accepted

        def counter(self):
            return COUNTER

        def checksum(self):
            return CHECKSUM

    monkeypatch.setattr(main_window_module, "TxFieldsDialog", Dialog)
    window.edit_rx_check(frame(made(0)))
    assert (0x100, False) in window.rx_checks.checks
    kept = window.ctx.settings.get(SETTING)
    assert kept and kept[0]["id"] == 0x100

    class Neither(Dialog):
        def counter(self):
            return None

        def checksum(self):
            return None

    monkeypatch.setattr(main_window_module, "TxFieldsDialog", Neither)
    window.edit_rx_check(frame(made(0)))
    assert window.rx_checks.checks == {} and window.ctx.settings.get(SETTING) == []


def test_frames_from_a_device_on_the_bus_are_checked(app, window):
    """End to end: a device on a virtual bus whose counter skips once."""
    window._connect_active("virtual", "vrxcheck", 500000, False)
    window.rx_checks.set(Check(0x100, counter=COUNTER, checksum=CHECKSUM))
    device = can.Bus(interface="virtual", channel="vrxcheck")
    try:
        for n in (0, 1, 2, 4, 5):  # 3 never comes
            device.send(can.Message(arbitration_id=0x100, data=made(n), is_extended_id=False))
        assert wait_until(
            app,
            lambda: (
                (t := window.rx_checks.tallies.get((0x100, False)))
                and t.run == 0
                and t.counter_faults == 1
            ),
        )
        assert window.rx_checks.tallies[(0x100, False)].checksum_faults == 0
    finally:
        device.shutdown()
        window._disconnect_active()
