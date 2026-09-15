# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Controller state in the status bar, and the way back from bus off.

The adapters disagree about how to say any of this, so each mapping is tested
on its own, then the judging that combines them, then the recovery, which
takes a different route on each kind of adapter.
"""

import ctypes
import subprocess

import can
import pytest
from PySide6.QtCore import QSettings

from pycangui.core import bus_health as h
from pycangui.core.bus import BusManager
from pycangui.ui.bus_status import COLOURS, DOT_SIZE
from pycangui.ui.main_window import MainWindow


@pytest.fixture
def bus(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    manager = BusManager()
    manager.connect_bus("virtual", "vcan_health", 500000, False)
    yield manager
    manager.disconnect_bus()


def error_frame(bus, can_id, data=b""):
    bus._collector.on_message_received(
        can.Message(is_error_frame=True, arbitration_id=can_id, data=data)
    )
    bus._drain()


def quiet(bus):
    """Let the error-frame quiet period pass and judge again.

    Counted first: until a tick takes the new frames, their time is not set.
    """
    bus._update_load()
    bus._errors_at -= bus.ERROR_QUIET_S + 1
    bus._update_load()


# --- what each adapter says -----------------------------------------------------------
@pytest.mark.parametrize(
    ("can_id", "data", "expected"),
    [
        (0x040, b"", h.BUS_OFF),
        (0x004, bytes([0, 0x20]), h.PASSIVE),
        (0x004, bytes([0, 0x10]), h.PASSIVE),
        (0x004, bytes([0, 0x08]), h.WARNING),
        (0x004, bytes([0, 0x04]), h.WARNING),
        (0x004, bytes([0, 0x40]), h.OK),
        (0x100, b"", h.OK),
        (0x002, bytes(8), None),  # lost arbitration: about the wire, not the controller
        (0x004, b"", None),  # a controller problem with no detail to read
    ],
)
def test_socketcan_error_frames(can_id, data, expected):
    assert h.from_socketcan_error(can_id, data) == expected


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (0x00000, h.OK),
        (0x00004, h.WARNING),
        (0x00008, h.WARNING),
        (0x40000, h.PASSIVE),
        (0x00010, h.BUS_OFF),
        (0x00018, h.BUS_OFF),
        (0x00020, h.OK),  # receive queue empty is not a bus error
    ],
)
def test_pcan_status(code, expected):
    assert h.from_pcan_status(code) == expected


def test_passive_is_listen_only_except_where_it_is_not():
    assert h.from_state("ixxat", "ERROR") == h.BUS_OFF
    assert h.from_state("pcan", "PASSIVE") is None, "listen-only is a setting, not a fault"
    assert h.from_state("ixxat", "PASSIVE") is None
    assert h.from_state("etas", "PASSIVE") == h.PASSIVE
    assert h.from_state("kvaser", "ACTIVE") is None


# --- judging --------------------------------------------------------------------------
def test_connected_is_green_and_disconnected_is_down(bus):
    assert bus.health == h.OK
    bus.disconnect_bus()
    assert bus.health == h.DOWN


def test_error_frames_alone_are_amber_until_they_stop(bus):
    error_frame(bus, 0x20)
    bus._update_load()
    assert bus.health == h.WARNING
    quiet(bus)
    assert bus.health == h.OK


def test_warning_is_not_logged_twice(bus):
    """The error frames already put a line in the log."""
    notes = []
    bus.note.connect(notes.append)
    error_frame(bus, 0x20)
    bus._update_load()
    assert len(notes) == 1


def test_socketcan_bus_off_lasts_until_it_restarts(bus):
    """socketcan says bus off once; the silence after it is not recovery."""
    bus.interface = "socketcan"
    error_frame(bus, 0x040)
    quiet(bus)
    assert bus.health == h.BUS_OFF
    error_frame(bus, 0x100)
    quiet(bus)
    assert bus.health == h.OK


def test_another_adapters_error_identifiers_mean_nothing(bus):
    bus.interface = "kvaser"
    error_frame(bus, 0x040)
    quiet(bus)
    assert bus.health == h.OK


def test_pcan_is_asked(bus, monkeypatch):
    bus.interface = "pcan"
    monkeypatch.setattr(bus.bus, "status", lambda: ctypes.c_uint(0x10), raising=False)
    bus._update_load()
    assert bus.health == h.BUS_OFF


def test_a_failing_status_query_is_not_a_crash(bus, monkeypatch):
    bus.interface = "pcan"

    def broken():
        raise OSError("driver gone")

    monkeypatch.setattr(bus.bus, "status", broken, raising=False)
    bus._update_load()
    assert bus.health == h.OK


def test_bus_off_is_logged_with_the_way_back(bus, monkeypatch):
    notes = []
    bus.note.connect(notes.append)
    monkeypatch.setattr(type(bus), "_read_state", lambda _self: "ERROR")
    bus._update_load()
    assert len(notes) == 1


# --- recovering -----------------------------------------------------------------------
def bus_off(bus, monkeypatch):
    original = BusManager._read_state
    monkeypatch.setattr(BusManager, "_read_state", lambda _self: "ERROR")
    bus._update_load()
    assert bus.health == h.BUS_OFF
    monkeypatch.setattr(BusManager, "_read_state", original)


def test_without_a_reset_the_channel_is_reopened(bus, monkeypatch):
    bus_off(bus, monkeypatch)
    closed, notes = [], []
    bus.disconnected.connect(lambda: closed.append(True))
    bus.note.connect(notes.append)

    assert bus.recover()
    assert closed == [True], "the only restart a virtual bus has"
    assert bus.is_connected and bus.description.startswith("virtual:vcan_health")
    assert bus.health == h.OK
    assert notes


def test_the_adapters_own_reset_is_used_where_there_is_one(bus, monkeypatch):
    bus_off(bus, monkeypatch)
    calls, closed = [], []
    monkeypatch.setattr(bus.bus, "reset", lambda: calls.append(1) or True, raising=False)
    bus.disconnected.connect(lambda: closed.append(True))
    assert bus.recover()
    assert calls == [1] and closed == [], "no reconnect when a reset did it"


def test_a_reset_that_fails_falls_back_to_reopening(bus, monkeypatch):
    bus_off(bus, monkeypatch)
    closed = []
    monkeypatch.setattr(bus.bus, "reset", lambda: False, raising=False)
    bus.disconnected.connect(lambda: closed.append(True))
    assert bus.recover()
    assert closed == [True] and bus.is_connected


def test_socketcan_is_restarted_by_the_kernel(bus, monkeypatch):
    bus.interface, bus.channel = "socketcan", "can0"
    ran = []

    def run(args, **_kwargs):
        ran.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr("pycangui.core.bus.subprocess.run", run)
    assert bus.recover()
    assert ran == [["ip", "link", "set", "can0", "type", "can", "restart"]]


@pytest.mark.parametrize(
    "outcome",
    [
        subprocess.CompletedProcess([], 2, "", "RTNETLINK answers: Operation not permitted"),
        OSError("no such file: ip"),
    ],
)
def test_a_socketcan_restart_that_is_refused_says_what_to_run(bus, monkeypatch, outcome):
    bus.interface, bus.channel = "socketcan", "can0"
    warnings = []
    bus.warning.connect(warnings.append)

    def run(*_args, **_kwargs):
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr("pycangui.core.bus.subprocess.run", run)
    assert not bus.recover()
    assert "sudo ip link set can0 type can restart" in warnings[0]
    assert "restart-ms" in warnings[0]


def test_nothing_to_recover_when_not_connected(bus):
    bus.disconnect_bus()
    warnings = []
    bus.warning.connect(warnings.append)
    assert not bus.recover()
    assert warnings and "not connected" in warnings[0]


# --- the status bar -------------------------------------------------------------------
def dot_colour(indicator):
    image = indicator.icon().pixmap(DOT_SIZE, DOT_SIZE).toImage()
    return image.pixelColor(image.width() // 2, image.height() // 2).name()


def test_the_status_bar_colours_each_channel_and_recovers_it(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    window = MainWindow()
    name = window.channels.active
    bus = window.channels.get(name)
    bus.connect_bus("virtual", "vcan_status", 500000, False)
    window._update_status()
    indicator = window.bus_status.indicators[name]
    assert indicator.health == h.OK and dot_colour(indicator) == COLOURS[h.OK]

    bus_off(bus, monkeypatch)
    window._update_status()
    assert dot_colour(indicator) == COLOURS[h.BUS_OFF]

    assert indicator.recover_action.isEnabled()
    indicator.recover_action.trigger()
    window._update_status()
    assert bus.is_connected and dot_colour(indicator) == COLOURS[h.OK]

    bus.disconnect_bus()
    window._update_status()
    assert indicator.text().endswith(": down") and dot_colour(indicator) == COLOURS[h.DOWN]
    assert not indicator.recover_action.isEnabled()
    window.close()


def test_the_status_bar_follows_the_channels(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    window = MainWindow()
    window.channels.add("CAN 9")
    window._update_status()
    assert "CAN 9" in window.bus_status.indicators
    assert list(window.bus_status.indicators) == window.channels.names()
    window.channels.remove("CAN 9")
    window._update_status()
    assert "CAN 9" not in window.bus_status.indicators
    window.close()
