# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""CANopen TIME (0x100): sent once, or on a timer, in local time or UTC."""

import struct
import time
from datetime import datetime

import can
import pytest
from PySide6.QtCore import QCoreApplication, QSettings

from pycangui.canopen.manager import CanopenManager, time_stamp
from pycangui.core.bus import BusManager

#: TIME counts from midnight on 1 January 1984.
EPOCH_1984 = 441763200


def heard(channel: str, how_many: int, start, timeout: float = 5.0) -> list[bytes]:
    with can.Bus(interface="virtual", channel=channel) as listener:
        start()
        frames: list[bytes] = []
        deadline = time.monotonic() + timeout
        while len(frames) < how_many and time.monotonic() < deadline:
            QCoreApplication.processEvents()
            message = listener.recv(0.05)
            if message is not None and message.arbitration_id == 0x100:
                frames.append(bytes(message.data))
        return frames


def decoded(frame: bytes) -> float:
    """A TIME frame back to a moment, as seconds since 1970 on the clock it was set by."""
    milliseconds, days = struct.unpack("<LH", frame)
    return EPOCH_1984 + days * 86400 + milliseconds / 1000


@pytest.fixture
def manager(app):
    bus = BusManager()
    manager = CanopenManager(bus)
    bus.connect_bus("virtual", "vcan_time", 500000, False)
    yield manager
    manager.stop_time()
    bus.disconnect_bus()
    manager.shutdown()


def test_local_time_is_utc_moved_by_the_local_offset():
    now = 1_790_000_000.0
    offset = datetime.fromtimestamp(now).astimezone().utcoffset().total_seconds()
    assert time_stamp(False, now) == now
    assert time_stamp(True, now) == now + offset


@pytest.mark.parametrize("local", [False, True])
def test_send_time_puts_the_moment_on_the_bus_once(manager, local):
    before = time_stamp(local)
    frames = heard("vcan_time", 1, lambda: manager.send_time(local))
    assert len(frames) == 1 and len(frames[0]) == 6, "milliseconds and days, six bytes"
    assert decoded(frames[0]) == pytest.approx(before, abs=2.0)
    assert not manager.time_running, "once, not repeatedly"


def test_the_producer_sends_it_again_at_its_period(manager):
    frames = heard("vcan_time", 3, lambda: manager.start_time(0.05, local=False))
    assert len(frames) == 3 and manager.time_running
    manager.stop_time()
    assert not manager.time_running


def test_the_pane_always_offers_time_at_the_period_in_the_settings(app, tmp_path, monkeypatch):
    from pycangui.ui import canopen_settings
    from pycangui.ui.main_window import MainWindow

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    window = MainWindow()
    try:
        view = window.canopen_view
        window.show()
        assert not view.time_btn.isHidden() and not view.send_time_btn.isHidden()
        assert not view.time_btn.isChecked(), "there, and not sending until it is ticked"

        chosen = canopen_settings.load(window.ctx)
        chosen.time_period_s, chosen.time_local = 5, False
        canopen_settings.save(window.ctx, chosen)

        started = []
        monkeypatch.setattr(window.canopen, "start_time", lambda *a: started.append(a))
        view.time_btn.setChecked(True)
        assert started == [(5, False)], "the period and the zone from the settings"

        sent = []
        monkeypatch.setattr(window.canopen, "send_time", lambda local: sent.append(local))
        view.send_time_btn.click()
        assert sent == [False]
    finally:
        window.close()


def test_the_settings_round_trip(app, tmp_path, monkeypatch):
    from pycangui.core.context import Context
    from pycangui.ui import canopen_settings

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    ctx = Context(log=print)
    dialog = canopen_settings.CanopenSettingsDialog(None, canopen_settings.load(ctx))
    assert dialog.time_period.isEnabled() and dialog.time_zone.isEnabled()
    dialog.time_period.setValue(30)
    dialog.time_zone.setCurrentIndex(1)
    canopen_settings.save(ctx, dialog.settings())
    again = canopen_settings.load(ctx)
    assert (again.time_period_s, again.time_local) == (30, False)
