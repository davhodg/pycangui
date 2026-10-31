# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Ticked signals sent over UDP multicast for pyscopegui."""

import json
import socket
import struct
import time

import pytest
from PySide6.QtCore import QCoreApplication, Qt

from pycangui.core import signal_share
from pycangui.core.signal_share import GROUP, PORT, SignalSender
from pycangui.core.signals import SignalHub


class Recorder:
    """Stands in for the socket: what would have gone out."""

    def __init__(self):
        self.sent = []

    def sendto(self, data, address):
        assert address == (GROUP, PORT)
        assert len(data) <= signal_share.DATAGRAM_BYTES
        self.sent.append(json.loads(data))

    def setsockopt(self, *a):
        pass

    def close(self):
        pass

    def kinds(self, kind):
        return [m for m in self.sent if m["k"] == kind]


@pytest.fixture
def hub(app):
    return SignalHub()


@pytest.fixture
def sender(hub, monkeypatch):
    out = SignalSender(
        hub, bus_now=lambda: 100.0, limits=lambda key: (0, 50) if "Speed" in key else None
    )
    recorder = Recorder()
    monkeypatch.setattr(out, "_open", lambda: setattr(out, "_socket", recorder))
    out.recorder = recorder
    yield out
    out.set_enabled(False)


def flush():
    QCoreApplication.processEvents()


def test_nothing_goes_out_until_it_is_switched_on(hub, sender):
    sender.set_sent("DBC Motor/Speed", True)
    hub.push_many("DBC Motor", 1.0, {"Speed": 12.0})
    flush()
    assert sender.recorder.sent == []


def test_switched_on_it_describes_what_is_ticked(hub, sender):
    hub.push_many("DBC Motor", 1.0, {"Speed": 12.0, "Fault": 0}, {"Speed": "rpm"})
    hub.set_choices("DBC Motor/Fault", {0: "OK", 3: "Overheat"})
    sender.set_sent("DBC Motor/Speed", True)
    sender.set_sent("DBC Motor/Fault", True)
    sender.set_enabled(True)
    described = sender.recorder.kinds("description")[-1]
    signals = {s["name"]: s for s in described["signals"]}
    assert signals["Speed"]["unit"] == "rpm"
    assert (signals["Speed"]["min"], signals["Speed"]["max"]) == (0, 50)
    assert "min" not in signals["Fault"], "no limits sent where the database gives none"
    assert signals["Fault"]["names"] == {"0": "OK", "3": "Overheat"}
    assert signals["Speed"]["n"] != signals["Fault"]["n"]
    assert described["run"] == sender.run and len(described["clock"]) == 2


def test_values_of_one_frame_share_one_time_on_the_monotonic_clock(hub, sender):
    hub.push_many("DBC Motor", 0.0, {"Speed": 1.0})
    sender.set_sent("DBC Motor/Speed", True)
    sender.set_sent("DBC Motor/Torque", True)
    hub.push_many("DBC Motor", 0.0, {"Torque": 1.0})
    sender.set_enabled(True)
    numbers = {s["name"]: s["n"] for s in sender.recorder.kinds("description")[-1]["signals"]}
    before = time.monotonic()
    hub.push_many("DBC Motor", 99.5, {"Speed": 12.5, "Torque": 3, "Other": 7})
    flush()
    (values,) = sender.recorder.kinds("values")
    (frame,) = values["frames"]
    at, pairs = frame
    # The hub's time 99.5 is half a second before its "now" of 100.
    assert before - 0.6 < at < time.monotonic() - 0.4
    assert sorted(pairs) == sorted([[numbers["Speed"], 12.5], [numbers["Torque"], 3]])
    assert values["gen"] == sender.generation


def test_a_tick_changed_says_so_at_once_with_a_new_count(hub, sender):
    hub.push_many("DBC Motor", 0.0, {"Speed": 1.0})
    sender.set_enabled(True)
    was = sender.generation
    sender.set_sent("DBC Motor/Speed", True)
    described = sender.recorder.kinds("description")[-1]
    assert described["gen"] > was and [s["name"] for s in described["signals"]] == ["Speed"]
    sender.set_sent("DBC Motor/Speed", False)
    assert sender.recorder.kinds("description")[-1]["signals"] == []


def test_a_ticked_signal_not_yet_seen_is_described_when_it_arrives(hub, sender):
    sender.set_all_sent(["DBC Motor/Speed"])
    sender.set_enabled(True)
    assert sender.recorder.kinds("description")[-1]["signals"] == []
    hub.push_many("DBC Motor", 0.0, {"Speed": 1.0})
    assert [s["name"] for s in sender.recorder.kinds("description")[-1]["signals"]] == ["Speed"]


def test_a_long_list_is_split_into_parts(hub, sender):
    names = {f"Signal_with_a_long_name_{i:03d}": float(i) for i in range(120)}
    hub.push_many("DBC Body", 0.0, names)
    for name in names:
        sender.set_sent(f"DBC Body/{name}", True)
    sender.recorder.sent.clear()
    sender.set_enabled(True)
    parts = sender.recorder.kinds("description")
    assert len(parts) > 1 and {p["parts"] for p in parts} == {len(parts)}
    assert sum(len(p["signals"]) for p in parts) == 120


def test_switched_off_nothing_more_goes(hub, sender):
    hub.push_many("DBC Motor", 0.0, {"Speed": 1.0})
    sender.set_sent("DBC Motor/Speed", True)
    sender.set_enabled(True)
    sender.set_enabled(False)
    sender.recorder.sent.clear()
    hub.push_many("DBC Motor", 1.0, {"Speed": 2.0})
    flush()
    assert sender.recorder.sent == [] and hub.taps == []


def test_a_failure_to_send_is_said_once(hub, sender):
    said = []
    sender.said.connect(lambda text, warn: said.append(warn))
    hub.push_many("DBC Motor", 0.0, {"Speed": 1.0})
    sender.set_sent("DBC Motor/Speed", True)
    sender.set_enabled(True)

    def refuse(*a):
        raise OSError("no route")

    sender.recorder.sendto = refuse
    for t in range(5):
        hub.push_many("DBC Motor", float(t), {"Speed": 2.0})
        flush()
    assert said.count(True) == 1


def test_it_arrives_on_the_group_over_loopback(hub, app):
    """The one test with a real socket; skipped where multicast is not available."""
    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        receiver.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        receiver.bind(("", PORT))
        membership = struct.pack("4s4s", socket.inet_aton(GROUP), socket.inet_aton("0.0.0.0"))
        receiver.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
        receiver.settimeout(2.0)
    except OSError as exc:
        receiver.close()
        pytest.skip(f"no multicast here: {exc}")
    sender = SignalSender(hub, bus_now=lambda: 0.0)
    try:
        sender.set_enabled(True)
        try:
            data, _ = receiver.recvfrom(65536)
        except OSError as exc:
            pytest.skip(f"multicast not delivered here: {exc}")
        assert json.loads(data)["k"] == "description"
    finally:
        sender.set_enabled(False)
        receiver.close()


# --- in the window ------------------------------------------------------------------
@pytest.fixture
def window(app, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings

    from pycangui.ui.main_window import MainWindow

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path / "home"))
    QSettings().clear()
    win = MainWindow()
    monkeypatch.setattr(win.sender, "_open", lambda: setattr(win.sender, "_socket", Recorder()))
    yield win
    win.close()
    QSettings().clear()


def test_the_setting_shows_the_send_column_and_a_tick_is_kept(window):
    from pycangui.ui.main_window import SENT_KEY
    from pycangui.ui.signals_view import SEND

    listed = window.scope.signals_view
    window.signals.push_many("DBC Motor", 0.0, {"Speed": 1.0})
    assert listed.tree.isColumnHidden(SEND)
    window.send_signals.setChecked(True)
    assert not listed.tree.isColumnHidden(SEND) and window.sender.enabled
    item = listed._items["DBC Motor/Speed"]
    item.setCheckState(SEND, Qt.Checked)
    assert window.sender.sent == ["DBC Motor/Speed"]
    assert window.ctx.settings.get(SENT_KEY) == ["DBC Motor/Speed"]
    window.send_signals.setChecked(False)
    assert listed.tree.isColumnHidden(SEND) and not window.sender.enabled
    assert window.sender.sent == ["DBC Motor/Speed"], "the ticks stay for next time"


def test_the_signals_have_a_port_of_their_own():
    """On Linux a python-can udp_multicast bus gets every group joined on its
    port, and fails on a message that is not a frame."""
    import inspect

    from can.interfaces.udp_multicast import UdpMulticastBus

    frames_port = inspect.signature(UdpMulticastBus.__init__).parameters["port"].default
    assert PORT != frames_port
