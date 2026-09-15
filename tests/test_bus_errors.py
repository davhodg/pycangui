# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Error frames and controller state: telling a broken bus from a quiet one."""

import time

import can
import pytest

from pycangui.core.bus import BusManager
from pycangui.core.classify import ERROR_GROUP, GROUPS
from pycangui.core.context import Context
from pycangui.core.hooks import Hooks
from pycangui.ui.trace_view import TraceView


@pytest.fixture
def bus(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    manager = BusManager()
    manager.connect_bus("virtual", "vcan_err", 500000, False)
    yield manager
    manager.disconnect_bus()


def pump(app, bus, times=4):
    for _ in range(times):
        bus._drain()
        app.processEvents()
        time.sleep(0.01)


# --- error frames are frames -------------------------------------------------------
def test_an_error_frame_reaches_the_trace_in_its_own_group(app, bus, tmp_path, monkeypatch):
    """They belong in the trace, under a group the Filter menu can hide."""
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    view = TraceView(Hooks(Context(log=print)), Context(log=print))
    seen = []
    bus.frames.connect(seen.extend)
    bus.frames.connect(view.on_frames)

    bus._collector.on_message_received(can.Message(arbitration_id=0x123, data=b"\x01"))
    bus._collector.on_message_received(can.Message(is_error_frame=True, arbitration_id=0x20))
    pump(app, bus)

    assert [f.error for f in seen] == [False, True]
    assert seen[1].group == ERROR_GROUP and seen[1].kind == "Bus error"
    assert seen[0].group != ERROR_GROUP, "ordinary traffic is untouched"
    view.deleteLater()


def test_the_filter_menu_offers_the_error_group():
    assert ERROR_GROUP in GROUPS, "otherwise there is no way to hide them"


def test_error_frames_are_not_decoded_as_messages(app, bus, tmp_path, monkeypatch):
    """An error frame's id is error flags, so no decoder should touch it."""
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    view = TraceView(Hooks(Context(log=print)), Context(log=print))
    view.classifiers.append(lambda _f: pytest.fail("a decoder was offered an error frame"))
    seen = []
    bus.frames.connect(seen.extend)
    bus.frames.connect(view.on_frames)

    # 0x080 would otherwise classify as a CANopen EMCY / SYNC id.
    bus._collector.on_message_received(can.Message(is_error_frame=True, arbitration_id=0x080))
    pump(app, bus)
    assert seen and seen[0].kind == "Bus error"
    view.deleteLater()


# --- the log gets the condition, not each frame -------------------------------------
def test_the_log_says_when_error_frames_start_and_stop(app, bus):
    notes = []
    bus.note.connect(notes.append)

    for _ in range(50):
        bus._collector.on_message_received(can.Message(is_error_frame=True, arbitration_id=1))
    bus._drain()
    bus._update_load()
    assert len(notes) == 1, f"50 error frames must not make 50 log lines: {notes}"

    # More of the same is not more news.
    for _ in range(50):
        bus._collector.on_message_received(can.Message(is_error_frame=True, arbitration_id=1))
    bus._drain()
    bus._update_load()
    assert len(notes) == 1, "a continuing fault must not repeat itself in the log"

    bus._errors_at -= bus.ERROR_QUIET_S + 1  # pretend the quiet period has passed
    bus._update_load()
    assert len(notes) == 2
    assert "100" in notes[1], "the total belongs in the all-clear"


# --- controller state ---------------------------------------------------------------
def test_a_state_change_is_reported(app, bus, monkeypatch):
    """Bus-off is the case that looks exactly like an idle bus otherwise."""
    notes = []
    bus.note.connect(notes.append)
    monkeypatch.setattr(type(bus), "_read_state", lambda _self: "ERROR")
    bus._report_health()
    assert notes

    bus._report_health()
    assert len(notes) == 1, "the state is reported on change, not on every tick"


def test_returning_to_active_is_reported_too(app, bus, monkeypatch):
    notes = []
    bus.note.connect(notes.append)
    monkeypatch.setattr(type(bus), "_read_state", lambda _self: "ERROR")
    bus._report_health()
    monkeypatch.setattr(type(bus), "_read_state", lambda _self: "ACTIVE")
    bus._report_health()
    assert len(notes) == 2


def test_a_backend_with_no_state_never_appears_to_change(app, bus, monkeypatch):
    notes = []
    bus.note.connect(notes.append)
    monkeypatch.setattr(type(bus), "_read_state", lambda _self: "")
    for _ in range(5):
        bus._report_health()
    assert notes == []


# --- a connected but silent bus ------------------------------------------------------
def test_a_silent_bus_says_so_once(app, bus):
    notes = []
    bus.note.connect(notes.append)
    bus._connected_at = time.monotonic() - bus.QUIET_WARNING_S - 1
    bus._seen_a_frame = False

    bus._drain()
    assert notes

    bus._drain()
    assert len(notes) == 1, "said once, not every 20 ms"


def test_a_bus_with_traffic_says_nothing(app, bus):
    notes = []
    bus.note.connect(notes.append)
    bus._connected_at = time.monotonic() - bus.QUIET_WARNING_S - 1
    bus._collector.on_message_received(can.Message(arbitration_id=0x1, data=b"\x00"))
    bus._drain()
    bus._drain()
    assert notes == []


def test_the_advice_for_a_quiet_virtual_bus_is_not_about_wiring(app, bus):
    """A loopback has no bitrate, wiring or termination to get wrong."""
    notes = []
    bus.note.connect(notes.append)
    bus._connected_at = time.monotonic() - bus.QUIET_WARNING_S - 1
    bus._seen_a_frame = False
    bus._drain()

    assert notes, "a silent bus must still say something"
    assert "loopback" in notes[0] and "Demo device" in notes[0]
    assert "termination" not in notes[0], "meaningless for a virtual channel"


def test_the_advice_for_a_quiet_real_bus_is_about_wiring(app, bus):
    bus.interface = "ixxat"  # pretend the adapter is real
    notes = []
    bus.note.connect(notes.append)
    bus._connected_at = time.monotonic() - bus.QUIET_WARNING_S - 1
    bus._seen_a_frame = False
    bus._drain()

    assert notes and "bitrate" in notes[0] and "termination" in notes[0]
    assert "loopback" not in notes[0]
