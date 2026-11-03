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


# --- how far it goes: off, this computer, the local network ------------------------------------
@pytest.fixture
def asked(monkeypatch):
    """What pycangui asks python-can for when a channel opens."""
    from pycangui.core import udp_reach

    found = {}

    def fake(**kwargs):
        found.update(kwargs)
        raise can.CanError("stop here")

    monkeypatch.setattr(can, "Bus", fake)
    yield found
    udp_reach.set_reach(udp_reach.COMPUTER)


def test_a_channel_stays_on_this_computer_unless_told_otherwise(asked):
    """python-can's own default lets frames out onto the local network."""
    from pycangui.core import udp_reach

    assert udp_reach.reach() == udp_reach.COMPUTER
    BusManager().connect_bus("udp_multicast", GROUP, 500000, False)
    assert asked["hop_limit"] == udp_reach.LOCAL_ONLY == 0
    udp_reach.set_reach(udp_reach.NETWORK)
    BusManager().connect_bus("udp_multicast", GROUP, 500000, False)
    assert asked["hop_limit"] == udp_reach.LOCAL_NETWORK == 1


def test_off_a_channel_does_not_connect(asked, app):
    from pycangui.core import udp_reach

    udp_reach.set_reach(udp_reach.OFF)
    channel = BusManager()
    errors = []
    channel.error.connect(errors.append)
    channel.connect_bus("udp_multicast", GROUP, 500000, False)
    assert not asked, "python-can is not even asked"
    assert not channel.is_connected and errors


def test_other_interfaces_are_not_given_a_hop_limit(asked):
    BusManager().connect_bus("virtual", "vcan9", 500000, False)
    assert "hop_limit" not in asked


def test_off_does_not_stop_other_interfaces(asked):
    from pycangui.core import udp_reach

    udp_reach.set_reach(udp_reach.OFF)
    BusManager().connect_bus("virtual", "vcan9", 500000, False)
    assert asked["interface"] == "virtual"


# --- what arrives from another computer --------------------------------------------------------
class Multicast:
    """python-can's socket wrapper, handing out datagrams from given senders."""

    def __init__(self, senders):
        self.senders = list(senders)

    def recv(self, timeout):
        if not self.senders:
            return None
        return b"frame", self.senders.pop(0), 0.0


class UdpBus:
    def __init__(self, senders):
        self._multicast = Multicast(senders)


def own_address():
    """One of this computer's addresses that is not the loopback."""
    from PySide6.QtNetwork import QHostAddress, QNetworkInterface

    for address in QNetworkInterface.allAddresses():
        if address.protocol() == QHostAddress.IPv4Protocol and not address.isLoopback():
            return address.toString()
    pytest.skip("no network address on this machine")


def test_this_computer_only_drops_what_another_computer_sends():
    from pycangui.core import udp_reach

    here = own_address()
    stranger = ("192.0.2.7", 43113)  # kept for documentation: never this computer
    bus = UdpBus([(here, 43113), stranger, ("127.0.0.1", 43113), stranger, ("::1", 43113, 0, 0)])
    told = []
    udp_reach.keep_to_this_computer(bus, told.append)
    kept = [bus._multicast.recv(0) is not None for _ in range(5)]
    assert kept == [True, False, True, False, True]
    assert told == ["192.0.2.7"], "each other computer said once"


def test_an_ipv6_address_with_its_zone_is_still_this_computer():
    from PySide6.QtNetwork import QHostAddress, QNetworkInterface

    from pycangui.core import udp_reach

    link_local = next(
        (
            a.toString()
            for a in QNetworkInterface.allAddresses()
            if a.protocol() == QHostAddress.IPv6Protocol and a.isLinkLocal()
        ),
        None,
    )
    if link_local is None:
        pytest.skip("no IPv6 link-local address on this machine")
    bare = link_local.split("%")[0]
    bus = UdpBus([(f"{bare}%99", 43113, 0, 99)])
    udp_reach.keep_to_this_computer(bus, lambda host: None)
    assert bus._multicast.recv(0) is not None


def test_a_python_can_that_hides_the_sender_does_not_open_as_this_computer_only():
    from pycangui.core import udp_reach

    with pytest.raises(RuntimeError):
        udp_reach.keep_to_this_computer(object(), lambda host: None)


@pytest.fixture
def opened(monkeypatch):
    """udp_multicast channels opened on a virtual bus, and which got the filter."""
    from pycangui.core import udp_reach

    real_bus = can.Bus
    found = {"buses": [], "filtered": []}

    def bus(**kwargs):
        found["buses"].append(real_bus(interface="virtual", channel="udp-reach-test"))
        return found["buses"][-1]

    monkeypatch.setattr(can, "Bus", bus)
    monkeypatch.setattr(
        udp_reach, "keep_to_this_computer", lambda b, dropped: found["filtered"].append(b)
    )
    yield found
    udp_reach.set_reach(udp_reach.COMPUTER)


def test_this_computer_only_filters_a_channel_and_the_network_does_not(opened, app):
    from pycangui.core import udp_reach

    channel = BusManager()
    channel.connect_bus("udp_multicast", GROUP, 500000, False)
    assert channel.is_connected and opened["filtered"] == [opened["buses"][0]]
    udp_reach.set_reach(udp_reach.NETWORK)
    channel.reopen()
    assert channel.is_connected and len(opened["buses"]) == 2
    assert opened["filtered"] == [opened["buses"][0]], "the network's frames are let in"
    channel.disconnect_bus()


def test_a_channel_that_cannot_keep_to_this_computer_is_closed_again(monkeypatch, app):
    closed = []
    hidden = type("Hidden", (), {"shutdown": lambda self: closed.append(True)})()
    monkeypatch.setattr(can, "Bus", lambda **kwargs: hidden)
    channel = BusManager()
    errors = []
    channel.error.connect(errors.append)
    channel.connect_bus("udp_multicast", GROUP, 500000, False)
    assert closed and errors and not channel.is_connected


# --- the setting ------------------------------------------------------------------------------
@pytest.fixture
def window(app, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings

    from pycangui.core import udp_reach
    from pycangui.ui.main_window import MainWindow

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    made = []

    def make():
        made.append(MainWindow())
        return made[-1]

    yield make
    for win in made:
        win.close()
    QSettings().clear()
    udp_reach.set_reach(udp_reach.COMPUTER)


def test_the_choice_is_kept_with_the_computer_and_applied(window):
    from PySide6.QtCore import QSettings

    from pycangui.core import udp_reach
    from pycangui.ui.main_window import UDP_REACH_KEY

    win = window()
    assert win.udp_choices[udp_reach.COMPUTER].isChecked()
    assert udp_reach.reach() == udp_reach.COMPUTER
    win.udp_choices[udp_reach.NETWORK].trigger()
    assert QSettings().value(UDP_REACH_KEY) == udp_reach.NETWORK
    assert udp_reach.hop_limit() == udp_reach.LOCAL_NETWORK
    assert win.sender.hop_limit == udp_reach.LOCAL_NETWORK, "the signals go as far"
    win.close()
    udp_reach.set_reach(udp_reach.COMPUTER)
    again = window()
    assert again.udp_choices[udp_reach.NETWORK].isChecked()
    assert udp_reach.reach() == udp_reach.NETWORK


@pytest.mark.parametrize(
    ("old", "now"), [(True, "network"), (False, "computer"), (None, "computer")]
)
def test_the_old_on_or_off_is_carried_over(window, old, now):
    from PySide6.QtCore import QSettings

    from pycangui.core import udp_reach
    from pycangui.ui.main_window import OLD_UDP_NETWORK_KEY

    if old is not None:
        QSettings().setValue(OLD_UDP_NETWORK_KEY, old)
    window()
    assert udp_reach.reach() == now


def test_off_greys_sending_and_on_again_puts_it_back(window, monkeypatch):
    from PySide6.QtCore import QSettings

    from pycangui.core import udp_reach
    from pycangui.ui.main_window import SEND_SIGNALS_KEY

    win = window()
    monkeypatch.setattr(win.sender, "_open", lambda: None)
    win.send_signals.setChecked(True)
    assert win.sender.enabled
    win.udp_choices[udp_reach.OFF].trigger()
    assert not win.send_signals.isEnabled() and not win.send_signals.isChecked()
    assert not win.sender.enabled
    assert QSettings().value(SEND_SIGNALS_KEY, type=bool), "still wanted, for later"
    win.udp_choices[udp_reach.COMPUTER].trigger()
    assert win.send_signals.isEnabled() and win.send_signals.isChecked()
    assert win.sender.enabled


def test_off_at_start_leaves_sending_greyed(window):
    from PySide6.QtCore import QSettings

    from pycangui.core import udp_reach
    from pycangui.ui.main_window import SEND_SIGNALS_KEY, UDP_REACH_KEY

    QSettings().setValue(UDP_REACH_KEY, udp_reach.OFF)
    QSettings().setValue(SEND_SIGNALS_KEY, True)
    win = window()
    assert not win.send_signals.isEnabled() and not win.sender.enabled


def test_a_connected_channel_follows_the_choice_at_once(window, monkeypatch):
    from pycangui.core import udp_reach

    win = window()
    channel = win.channels.get(win.channels.names()[0])
    calls = []
    monkeypatch.setattr(channel, "bus", object())  # connected, as far as anyone asks
    monkeypatch.setattr(channel, "interface", "udp_multicast")
    monkeypatch.setattr(channel, "reopen", lambda: calls.append("reopen"))
    monkeypatch.setattr(channel, "disconnect_bus", lambda: calls.append("disconnect"))
    win.udp_choices[udp_reach.NETWORK].trigger()
    win.udp_choices[udp_reach.OFF].trigger()
    assert calls == ["reopen", "disconnect"]
