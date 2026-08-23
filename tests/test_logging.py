"""Recording to a log file and replaying it, in every format python-can offers."""

import time

import can
import pytest

from pycangui.core.bus import BusManager, Frame
from pycangui.core.channels import Channels
from pycangui.core.context import Context
from pycangui.core.logging import Player, Recorder


def wait_until(app, pred, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not pred():
        app.processEvents()
        if time.monotonic() > deadline:
            raise AssertionError("timed out")
        time.sleep(0.005)


@pytest.fixture
def bus(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    manager = BusManager()
    manager.connect_bus("virtual", "vcan_log", 500000, False)
    yield manager
    manager.disconnect_bus()


@pytest.fixture
def channels(app, tmp_path, monkeypatch):
    """One Channels, with its default channel connected to a virtual bus."""
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    group = Channels()
    group.active_bus().connect_bus("virtual", "vcan_rec", 500000, False)
    yield group
    group.shutdown()


@pytest.mark.parametrize("suffix", [".blf", ".asc", ".csv", ".log"])
def test_record_then_replay_round_trip(app, channels, tmp_path, suffix):
    path = tmp_path / f"capture{suffix}"
    bus = channels.active_bus()
    recorder = Recorder(channels)
    states: list[tuple] = []
    recorder.state.connect(lambda on, p: states.append((on, p)))
    recorder.error.connect(lambda text: pytest.fail(text))

    assert recorder.start(path)
    assert recorder.is_recording and states[0][0] is True
    sent = [(0x123, b"\x01\x02\x03"), (0x7FF, b"\xaa" * 8), (0x18DAF110, b"\x10\x20")]
    for can_id, data in sent:
        bus.send(can_id, data, extended=can_id > 0x7FF)
        time.sleep(0.01)
    wait_until(app, lambda: True, 0.1)
    recorder.stop()
    assert not recorder.is_recording and states[-1][0] is False
    assert path.is_file() and path.stat().st_size > 0

    # Replay it onto the same virtual bus: the frames come back round as they
    # went in, because a virtual bus loops its own traffic back to us.  That
    # loopback is the whole reason replay needs no separate offline mode.
    received: list[Frame] = []
    bus.frames.connect(received.extend)
    player = Player(path, bus, speed=20.0)
    done: list[str] = []
    player.finished_playing.connect(done.append)
    player.start()
    wait_until(app, lambda: done and len(received) >= len(sent), 10)
    assert done[0] == "end"
    assert [(f.can_id, bytes(f.data)) for f in received[: len(sent)]] == sent
    assert received[2].extended is True and received[0].extended is False
    player.wait(1000)


def test_replay_transmits_onto_the_bus(app, bus, tmp_path):
    path = tmp_path / "one.blf"
    with can.BLFWriter(str(path)) as writer:
        for i in range(4):
            writer.on_message_received(
                can.Message(arbitration_id=0x321, data=[i], timestamp=i * 0.01)
            )

    received: list[Frame] = []
    bus.frames.connect(received.extend)
    player = Player(path, bus, speed=10.0)
    done: list[str] = []
    player.finished_playing.connect(done.append)
    player.start()
    wait_until(app, lambda: done and len([f for f in received if f.can_id == 0x321]) >= 4, 10)
    assert [bytes(f.data) for f in received if f.can_id == 0x321] == [
        b"\x00",
        b"\x01",
        b"\x02",
        b"\x03",
    ]
    player.wait(1000)


def test_recording_survives_switching_the_selected_channel(app, channels, tmp_path):
    """The bug this guards against: recording used to follow the selection.

    The recorder was handed the ActiveBus facade, which emits ``disconnected``
    as the selection leaves a connected channel, and the recorder stopped on
    ``disconnected``.  Selecting another channel to drive a protocol pane
    therefore ended the recording silently, leaving a truncated file.
    """
    path = tmp_path / "switch.blf"
    first = channels.active_bus()
    recorder = Recorder(channels)
    recorder.error.connect(lambda text: pytest.fail(text))
    assert recorder.start(path)

    second = channels.add("CAN 2")
    second.connect_bus("virtual", "vcan_rec2", 500000, False)
    channels.set_active("CAN 2")
    app.processEvents()

    assert recorder.is_recording, "selecting another channel must not stop the recording"
    first.send(0x100, b"\x01")  # the channel we are no longer looking at
    second.send(0x200, b"\x02")
    time.sleep(0.05)
    wait_until(app, lambda: True, 0.1)
    assert set(recorder.channels_recorded) == {"CAN 1", "CAN 2"}
    recorder.stop()

    with can.LogReader(str(path)) as reader:
        ids = [m.arbitration_id for m in reader]
    assert 0x100 in ids, "frames on the deselected channel must still be recorded"
    assert 0x200 in ids, "a channel connected mid recording joins the file"


def test_recording_continues_when_a_channel_drops(app, channels, tmp_path):
    path = tmp_path / "drop.blf"
    bus = channels.active_bus()
    recorder = Recorder(channels)
    notes: list[str] = []
    recorder.note.connect(notes.append)
    assert recorder.start(path)

    bus.disconnect_bus()
    app.processEvents()
    assert recorder.is_recording, "a channel dropping must not truncate the file"
    assert any("disconnected" in n for n in notes), "and it should say so"
    recorder.stop()
    assert path.is_file()


def test_recorder_needs_a_bus_and_reports_bad_paths(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    Context(log=print)
    group = Channels()
    recorder = Recorder(group)
    errors: list[str] = []
    recorder.error.connect(errors.append)
    assert recorder.start(tmp_path / "x.blf") is False
    assert "connect a channel" in errors[0]

    group.active_bus().connect_bus("virtual", "vcan_log2", 500000, False)
    assert recorder.start(tmp_path / "x.unknownformat") is False
    assert any("cannot write" in e for e in errors)
    group.shutdown()


def test_replay_of_a_missing_file_reports_the_error(app, bus, tmp_path):
    player = Player(tmp_path / "nope.blf", bus)
    done: list[str] = []
    player.finished_playing.connect(done.append)
    player.start()
    wait_until(app, lambda: done, 5)
    assert done[0] not in ("end", "stopped")  # a readable error, not a crash
    player.wait(1000)
