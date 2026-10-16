# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Sharing frames with other python-can programs over UDP multicast.

The bus refuses to be asked for what was sent on it, and delivers it back all
the same, marked as received: pycangui has to tell its own frames from the
others itself.
"""

import time

import can
import pytest

from pycangui.core.bus import NO_ECHO, BusManager, _Collector

GROUP = "239.74.163.9"


def message(can_id, data=b"\x01"):
    return can.Message(arbitration_id=can_id, data=data, is_extended_id=False, is_rx=True)


def test_a_frame_sent_here_coming_back_is_shown_as_sent():
    collector = _Collector("udp", time.monotonic())
    collector.expect(message(0x321))
    collector.on_message_received(message(0x321))
    collector.on_message_received(message(0x123))
    assert [(f.can_id, f.rx) for f in collector.drain()] == [(0x321, False), (0x123, True)]


def test_another_programs_frame_with_the_same_id_is_still_received():
    """Only the frame sent, byte for byte: the same id with other data is not it."""
    collector = _Collector("udp", time.monotonic())
    collector.expect(message(0x321, b"\x01"))
    collector.on_message_received(message(0x321, b"\x02"))
    assert [f.rx for f in collector.drain()] == [True]


def test_a_frame_that_never_comes_back_is_not_waited_for_for_ever(monkeypatch):
    from pycangui.core import bus

    collector = _Collector("udp", time.monotonic())
    collector.expect(message(0x321))
    later = time.monotonic() + bus.ECHO_WAIT_S + 1
    monkeypatch.setattr(bus.time, "monotonic", lambda: later)
    collector.on_message_received(message(0x321))
    assert [f.rx for f in collector.drain()] == [True], "too late to be the echo"


def test_the_bus_is_not_asked_for_what_it_cannot_give(monkeypatch):
    """It raises rather than open: every connect failed."""
    asked = {}

    def bus(**kwargs):
        asked.update(kwargs)
        raise can.CanError("stop here")

    monkeypatch.setattr(can, "Bus", bus)
    BusManager().connect_bus("udp_multicast", GROUP, 500000, False)
    assert "udp_multicast" in NO_ECHO and "receive_own_messages" not in asked


def test_pycangui_and_another_program_share_frames(app):
    """Both ways, on this machine: what the other sends is received, what
    pycangui sends -- once and cyclically -- is heard there and shown here
    as sent, once each."""
    pytest.importorskip("msgpack")
    ours = BusManager()
    ours.connect_bus("udp_multicast", GROUP, 500000, False)
    if ours.bus is None:
        pytest.skip("no multicast on this machine")
    other = can.Bus(interface="udp_multicast", channel=GROUP, hop_limit=0)  # this computer only
    try:
        other.send(can.Message(arbitration_id=0x123, data=b"\x01", is_extended_id=False))
        ours.send(0x321, b"\x09")
        seen = []
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and len(seen) < 2:
            seen.extend(ours._collector.drain())
            time.sleep(0.01)
        heard = set()
        while (msg := other.recv(0.3)) is not None:
            heard.add(msg.arbitration_id)
    finally:
        other.shutdown()
        ours.disconnect_bus()
    assert sorted((f.can_id, f.rx) for f in seen) == [(0x123, True), (0x321, False)]
    assert 0x321 in heard


# --- choosing the group ------------------------------------------------------------------------
def test_the_port_list_offers_python_cans_groups_and_pyscopeguis():
    """A program left at python-can's defaults, and pyscopegui, each found by
    choosing rather than by typing an address."""
    from pycangui.core.detect import channel_suggestions

    offered = {c.text: c.label for c in channel_suggestions("udp_multicast")}
    assert "239.74.163.2" in offered
    assert "ff15:7079:7468:6f6e:6465:6d6f:6d63:6173" in offered, "what a program naming none joins"
    assert "pyscopegui" in offered["239.74.163.10"]


def test_the_bitrate_is_greyed_where_there_is_no_wire(app, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings

    from pycangui.ui.main_window import MainWindow

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    window = MainWindow()
    try:
        bar = window.connect_bar
        bar.interface.setCurrentText("udp_multicast")
        assert not bar.bitrate.isEnabled() and not bar.data_bitrate.isEnabled()
        bar.interface.setCurrentText("virtual")
        assert bar.bitrate.isEnabled()
    finally:
        window.close()


# --- this computer only, unless chosen ---------------------------------------------------------
@pytest.fixture
def asked(monkeypatch):
    """What pycangui asks python-can for when a channel opens."""
    from pycangui.core import bus

    found = {}

    def fake(**kwargs):
        found.update(kwargs)
        raise can.CanError("stop here")

    monkeypatch.setattr(can, "Bus", fake)
    yield found
    bus.share_udp_on_network(False)


def test_a_channel_stays_on_this_computer_unless_told_otherwise(asked):
    """python-can's own default lets frames out onto the local network."""
    from pycangui.core import bus

    BusManager().connect_bus("udp_multicast", GROUP, 500000, False)
    assert asked["hop_limit"] == bus.LOCAL_ONLY == 0
    bus.share_udp_on_network(True)
    BusManager().connect_bus("udp_multicast", GROUP, 500000, False)
    assert asked["hop_limit"] == bus.LOCAL_NETWORK == 1


def test_other_interfaces_are_not_given_a_hop_limit(asked):
    BusManager().connect_bus("virtual", "vcan9", 500000, False)
    assert "hop_limit" not in asked


def test_the_setting_is_kept_with_the_computer_and_applied(app, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings

    from pycangui.core import bus
    from pycangui.ui.main_window import UDP_NETWORK_KEY, MainWindow

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    window = MainWindow()
    try:
        assert not window.udp_network.isChecked() and bus.udp_hop_limit() == bus.LOCAL_ONLY
        window.udp_network.setChecked(True)
        assert QSettings().value(UDP_NETWORK_KEY, type=bool) is True
        assert bus.udp_hop_limit() == bus.LOCAL_NETWORK
    finally:
        window.close()
        bus.share_udp_on_network(False)
