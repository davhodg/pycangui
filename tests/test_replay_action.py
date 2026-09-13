# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""The Replay toolbar button: which channel it plays onto, and when it asks."""

import time

import can
import pytest
from PySide6.QtWidgets import QMessageBox, QToolBar

from pycangui.core.channels import Channels
from pycangui.core.context import Context
from pycangui.ui import replay_action as ra


@pytest.fixture
def setup(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    ctx = Context(log=lambda _text: None)
    channels = Channels()
    channels.active_bus().connect_bus("virtual", "vcan_replay", 500000, False)
    toolbar = QToolBar()
    action = ra.ReplayAction(toolbar, channels, ctx)
    path = tmp_path / "log.blf"
    with can.BLFWriter(str(path)) as writer:
        for i in range(6):
            writer.on_message_received(
                can.Message(arbitration_id=0x400 + i, data=[i], timestamp=i * 0.2)
            )
    action._remember(path)
    yield action, channels, path
    action.stop()
    channels.shutdown()
    toolbar.deleteLater()


def drain(app, pred, timeout=8.0):
    deadline = time.monotonic() + timeout
    while not pred() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)


def test_replays_onto_a_virtual_channel_without_asking(app, setup, monkeypatch):
    """Virtual is the no-hardware case, so it must not put a dialog in the way."""
    action, channels, _ = setup
    monkeypatch.setattr(QMessageBox, "exec", lambda _box: pytest.fail("should not have asked"))
    received = []
    channels.active_bus().frames.connect(received.extend)

    action.action.setChecked(True)
    # Both conditions: the player thread finishes a moment before the last
    # frame has been drained off the bus and delivered.
    drain(app, lambda: action.player is None and len(received) >= 6)
    assert [f.can_id for f in received] == [0x400 + i for i in range(6)]
    assert not action.action.isChecked(), "the button releases itself when the log ends"


def test_the_channel_is_pinned_when_the_replay_starts(app, setup):
    """Selecting another channel mid replay must not redirect the frames.

    The player is given a channel rather than the ActiveBus facade, which
    follows the selection; without that, switching channel to look at a
    protocol pane moved a running replay onto a different bus.
    """
    action, channels, _ = setup
    first = channels.active_bus()
    action.loop_action.setChecked(True)  # keep it running while we interfere
    action.action.setChecked(True)
    drain(app, lambda: action.player is not None, 2)
    assert action.player is not None

    second = channels.add("CAN 2")
    second.connect_bus("virtual", "vcan_replay2", 500000, False)
    channels.set_active("CAN 2")
    app.processEvents()

    assert action.player._bus is first, "the replay must stay on the channel it started on"
    assert action._playing_on == first.channel_name
    action.stop()


def test_replaying_onto_a_real_bus_asks_first(app, setup, monkeypatch):
    action, channels, _ = setup
    bus = channels.active_bus()
    bus.interface = "socketcan"  # pretend it is real hardware
    asked = []

    monkeypatch.setattr(
        QMessageBox, "exec", lambda box: (asked.append(box.text()), QMessageBox.Cancel)[1]
    )
    action.action.setChecked(True)
    assert action.player is None, "cancelling must not transmit onto a real bus"
    assert not action.action.isChecked()
    assert asked, "it has to ask before putting frames on a real bus"

    monkeypatch.setattr(QMessageBox, "exec", lambda _box: QMessageBox.Yes)
    action.action.setChecked(True)
    drain(app, lambda: action.player is None)
    assert action.confirm.agreed(f"replay:{bus.channel_name}:{bus.description}")

    # Having said yes once, the same channel is not asked about again.
    monkeypatch.setattr(QMessageBox, "exec", lambda _box: pytest.fail("should only ask once"))
    action.action.setChecked(True)
    drain(app, lambda: action.player is None)


def test_offers_a_virtual_channel_when_nothing_is_connected(app, setup, monkeypatch):
    action, channels, _ = setup
    channels.active_bus().disconnect_bus()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)

    action.action.setChecked(True)
    drain(app, lambda: action.player is None)
    bus = channels.get(ra.VIRTUAL_CHANNEL)
    assert bus is not None and bus.is_connected
    assert bus.interface == "virtual"
