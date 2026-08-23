"""Recording to a log file and replaying it, in every format python-can offers."""

import time

import can
import pytest

from pycangui.core.bus import BusManager, Frame
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


@pytest.mark.parametrize("suffix", [".blf", ".asc", ".csv", ".log"])
def test_record_then_replay_round_trip(app, bus, tmp_path, suffix):
    path = tmp_path / f"capture{suffix}"
    recorder = Recorder(bus)
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

    # replay it back offline: the frames come out as they went in
    played: list[Frame] = []
    player = Player(path, bus, transmit=False, speed=20.0)
    player.frames.connect(played.extend)
    done: list[str] = []
    player.finished_playing.connect(done.append)
    player.start()
    wait_until(app, lambda: done, 10)
    assert done[0] == "end"
    assert [(f.can_id, f.data) for f in played] == sent
    assert played[2].extended is True and played[0].extended is False
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
    player = Player(path, bus, transmit=True, speed=10.0)
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


def test_recorder_needs_a_bus_and_reports_bad_paths(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    Context(log=print)
    offline = BusManager()
    recorder = Recorder(offline)
    errors: list[str] = []
    recorder.error.connect(errors.append)
    assert recorder.start(tmp_path / "x.blf") is False
    assert "not connected" in errors[0]

    offline.connect_bus("virtual", "vcan_log2", 500000, False)
    assert recorder.start(tmp_path / "x.unknownformat") is False
    assert any("cannot write" in e for e in errors)
    offline.disconnect_bus()


def test_replay_of_a_missing_file_reports_the_error(app, bus, tmp_path):
    player = Player(tmp_path / "nope.blf", bus, transmit=False)
    done: list[str] = []
    player.finished_playing.connect(done.append)
    player.start()
    wait_until(app, lambda: done, 5)
    assert done[0] not in ("end", "stopped")  # a readable error, not a crash
    player.wait(1000)
