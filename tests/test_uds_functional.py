# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Functional requests, to every ECU at once, and a baud rate change the
channel follows -- against the demo ECU on a virtual bus."""

import time

import can
import pytest
from PySide6.QtCore import QCoreApplication

from pycangui.core.bus import BusManager
from pycangui.core.context import Context
from pycangui.core.hooks import Hooks
from pycangui.uds import UdsConfig, functional
from pycangui.uds.manager import UdsManager


def wait_until(pred, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not pred():
        QCoreApplication.processEvents()
        if time.monotonic() > deadline and not pred():
            raise AssertionError("timed out")
        time.sleep(0.005)


# --- the frame itself -----------------------------------------------------------------
def test_a_request_goes_as_one_frame_padded_or_not():
    assert functional.single_frame(b"\x3e\x80", 0xAA) == bytes([2, 0x3E, 0x80]) + b"\xaa" * 5
    assert functional.single_frame(b"\x3e\x80", None) == bytes([2, 0x3E, 0x80])
    long = bytes(range(20))
    assert functional.single_frame(long, None, fd=True)[:2] == bytes([0, 20]), "FD escape"
    with pytest.raises(ValueError):
        functional.single_frame(long, None)  # more than one classic frame


def test_a_single_frame_is_read_back_and_anything_else_is_not():
    assert functional.payload_of(bytes([3, 0x7F, 0x28, 0x22, 0, 0])) == bytes([0x7F, 0x28, 0x22])
    assert functional.payload_of(bytes([0x10, 0x14, 1, 2])) is None, "a first frame"


def test_the_suppress_bit_goes_only_where_there_is_a_sub_function():
    assert functional.suppressed(bytes([0x28, 0x01, 0x01])) == bytes([0x28, 0x81, 0x01])
    assert functional.suppressed(bytes([0x14, 0xFF, 0xFF, 0xFF])) == bytes([0x14, 0xFF, 0xFF, 0xFF])


def test_answers_are_recognised_from_any_ecu_on_a_j1939_bus():
    config = UdsConfig(fixed=True, tester_address=0xF9)
    heard = functional.answers_to(config)
    assert heard(0x18DAF971, True) and heard(0x18DAF972, True), "every ECU, to this tester"
    assert not heard(0x18DAF871, True), "not an answer to another tester"
    typed = functional.answers_to(UdsConfig())
    assert typed(0x7E8, False) and typed(0x7EA, False), "the OBD answers, with 7DF"


# --- against the demo ECU -------------------------------------------------------------
@pytest.fixture
def ecu(app, tmp_path, monkeypatch, demo_device):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    ctx = Context(log=print)
    bus = BusManager()
    bus.connect_bus("virtual", "vcan_functional", 500_000, False)
    demo = demo_device(bus, kinds=["uds_server"], channel="CAN")
    manager = UdsManager(bus, Hooks(ctx), ctx)
    results: list[str] = []
    manager.result.connect(results.append)
    manager.open(UdsConfig(p2_timeout_s=0.3, p2_star_timeout_s=1.0, functional=[]))
    yield manager, demo["uds_server"].state, bus, results
    manager.shutdown()
    bus.disconnect_bus()


def extended_session(manager, state):
    manager.config.functional = []
    manager.change_session(3)
    wait_until(lambda: state.session == 3)


def test_communication_control_reaches_the_ecu_functionally(ecu):
    manager, state, _bus, _results = ecu
    extended_session(manager, state)
    manager.config.functional = ["comm"]
    manager.communication_control(1, 1)
    # The ECU did it, told on the functional address.
    wait_until(lambda: state.communication == (1, 1))


def test_a_functional_baud_rate_change_moves_the_ecu_and_the_channel(ecu):
    manager, state, bus, _results = ecu
    extended_session(manager, state)
    manager.config.functional = ["link", "tester"]
    manager.set_tester_present(True)
    moved, resumed = [], []
    manager.rate_moved.connect(lambda now, own: moved.append((now, own)))
    manager.tester_present_resumed.connect(lambda: resumed.append(1))

    manager.change_bitrate(250_000)
    wait_until(lambda: moved)
    # The ECU acts on the request on a thread of its own, so this is waited
    # for rather than asserted the instant the channel has followed.
    wait_until(lambda: state.bitrate == 250_000)
    assert bus.bitrate == 250_000 and bus.is_connected, "the channel followed"
    assert manager.is_open, "and the session with it"
    assert resumed and manager._tp_timer.isActive(), "tester present carried on"
    assert moved[-1] == (250_000, 500_000), "and the way back is known"

    manager.back_to_own_rate()
    wait_until(lambda: len(moved) == 2)
    assert bus.bitrate == 500_000 and manager.is_open
    assert state.session == 1, "the session ended, which is what undoes LinkControl"
    assert moved[-1] == (500_000, 0), "home again"


def test_a_refused_verify_changes_nothing(ecu):
    manager, state, bus, results = ecu
    manager.config.functional = ["link"]
    moved = []
    manager.rate_moved.connect(lambda *a: moved.append(a))
    manager.change_bitrate(250_000)  # the demo will not in its default session
    wait_until(lambda: results[-1:] and "LinkControl" in results[-1])
    QCoreApplication.processEvents()
    assert state.bitrate is None and bus.bitrate == 500_000 and not moved


def test_a_channel_whose_rate_pycangui_does_not_set_sends_nothing(ecu, monkeypatch):
    manager, _state, bus, results = ecu
    sent: list[can.Message] = []
    monkeypatch.setattr(BusManager, "sets_bitrate", property(lambda _self: False))
    monkeypatch.setattr(bus.bus, "send", lambda msg, *_a, **_k: sent.append(msg))
    manager.config.functional = ["link"]
    before = len(results)
    manager.change_bitrate(250_000)
    assert sent == [] and len(results) == before + 1, "said why, sent nothing"


# --- the pane ----------------------------------------------------------------------
@pytest.fixture
def view(app, tmp_path, monkeypatch):
    from pycangui.ui.uds_view import UdsView

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    ctx = Context(log=print)
    manager = UdsManager(BusManager(), Hooks(ctx), ctx)
    yield UdsView(manager, ctx)
    manager.shutdown()


def test_each_service_can_be_ticked_to_go_to_every_ecu(view):
    view.functional_actions["reset"].setChecked(True)
    view.functional_actions["tester"].setChecked(False)
    assert view.manager.config.goes_to_all("reset"), "from the next request on"
    assert not view.manager.config.goes_to_all("tester")
    saved = UdsConfig.from_dict(view.ctx.settings.get("uds.config"))
    assert saved.goes_to_all("reset") and not saved.goes_to_all("tester"), "and remembered"


def test_a_reset_asks_first_only_when_it_goes_to_every_ecu(view, monkeypatch):
    asked, reset = [], []
    monkeypatch.setattr(view.confirm, "ask", lambda *a: asked.append(1) or True)
    monkeypatch.setattr(view.manager, "ecu_reset", reset.append)
    view.functional_actions["reset"].setChecked(False)
    view._reset()
    assert reset and not asked
    view.functional_actions["reset"].setChecked(True)
    view._reset()
    assert len(reset) == 2 and asked


def test_the_way_back_is_offered_only_while_the_rate_is_away(view):
    # Whether it is offered, not whether its tab is the one in front.
    view.manager.rate_moved.emit(250_000, 500_000)
    assert not view.rate_back.isHidden()
    view.manager.rate_moved.emit(500_000, 0)
    assert view.rate_back.isHidden()


def test_tester_present_is_ticked_again_after_following_the_rate(view):
    view.tp.setChecked(False)
    view.manager.tester_present_resumed.emit()
    assert view.tp.isChecked()


def test_checking_a_rate_asks_every_ecu_and_moves_nobody(ecu):
    manager, state, bus, results = ecu
    extended_session(manager, state)
    manager.config.functional = ["link"]
    moved = []
    manager.rate_moved.connect(lambda *a: moved.append(a))
    before = len(results)
    manager.check_bitrate(250_000)
    wait_until(lambda: len(results) > before)
    QCoreApplication.processEvents()
    assert state.bitrate is None and bus.bitrate == 500_000 and not moved
