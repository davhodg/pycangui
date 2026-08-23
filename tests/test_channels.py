"""Several channels at once, and the active-channel facade."""

import time

import pytest

from pycangui.core.bus import Frame
from pycangui.core.channels import DEFAULT_CHANNEL, ActiveBus, Channels


def wait_until(app, pred, timeout=4.0):
    deadline = time.monotonic() + timeout
    while not pred():
        app.processEvents()
        if time.monotonic() > deadline:
            raise AssertionError("timed out")
        time.sleep(0.005)


@pytest.fixture
def channels(app):
    c = Channels()
    yield c
    c.shutdown()


def test_starts_with_one_channel(channels):
    assert channels.names() == [DEFAULT_CHANNEL]
    assert channels.active == DEFAULT_CHANNEL
    assert not channels.any_connected


def test_add_remove_and_rename(channels):
    added: list[str] = []
    removed: list[str] = []
    channels.channel_added.connect(added.append)
    channels.channel_removed.connect(removed.append)

    channels.add("CAN 2")
    assert channels.names() == [DEFAULT_CHANNEL, "CAN 2"] and added == ["CAN 2"]
    assert channels.add("CAN 2") is channels.get("CAN 2")  # adding twice is harmless

    channels.rename("CAN 2", "Drive bus")
    assert channels.names() == [DEFAULT_CHANNEL, "Drive bus"]
    assert channels.get("Drive bus").channel_name == "Drive bus"

    channels.set_active("Drive bus")
    channels.remove("Drive bus")
    assert channels.names() == [DEFAULT_CHANNEL] and removed[-1] == "Drive bus"
    assert channels.active == DEFAULT_CHANNEL  # the selection moved somewhere valid


def test_frames_from_every_channel_are_merged_on_one_clock(app, channels):
    merged: list[Frame] = []
    channels.frames.connect(merged.extend)
    one = channels.get(DEFAULT_CHANNEL)
    two = channels.add("CAN 2")
    one.connect_bus("virtual", "vch1", 500000, False)
    two.connect_bus("virtual", "vch2", 500000, False)

    one.send(0x100, b"\x01")
    two.send(0x200, b"\x02")
    wait_until(app, lambda: len(merged) >= 2)

    by_id = {f.can_id: f for f in merged}
    assert by_id[0x100].channel == DEFAULT_CHANNEL
    assert by_id[0x200].channel == "CAN 2"
    # one clock: the two frames are milliseconds apart, not seconds
    assert abs(by_id[0x100].timestamp - by_id[0x200].timestamp) < 1.0
    assert channels.any_connected


def test_active_bus_follows_the_selection(app, channels):
    bus = ActiveBus(channels)
    connected: list[str] = []
    disconnected: list[int] = []
    frames: list[Frame] = []
    bus.connected.connect(connected.append)
    bus.disconnected.connect(lambda: disconnected.append(1))
    bus.frames.connect(frames.extend)

    one = channels.get(DEFAULT_CHANNEL)
    two = channels.add("CAN 2")
    one.connect_bus("virtual", "vch3", 500000, False)
    wait_until(app, lambda: connected)
    assert bus.is_connected and bus.channel_name == DEFAULT_CHANNEL

    one.send(0x111, b"\x01")
    wait_until(app, lambda: frames)
    assert frames[0].can_id == 0x111

    # switching to a disconnected channel looks like a disconnect to the stacks
    channels.set_active("CAN 2")
    assert disconnected  # they tear down
    assert not bus.is_connected and bus.channel_name == "CAN 2"

    # and traffic on the channel we left no longer reaches them
    count = len(frames)
    one.send(0x222, b"\x02")
    wait_until(app, lambda: True, 0.2)
    assert len(frames) == count

    # switching to a connected channel looks like a connect
    two.connect_bus("virtual", "vch4", 500000, False)
    wait_until(app, lambda: bus.is_connected)
    n = len(connected)
    channels.set_active(DEFAULT_CHANNEL)
    channels.set_active("CAN 2")
    assert len(connected) > n


def test_active_bus_without_a_channel_reports_rather_than_crashing(app, channels):
    bus = ActiveBus(channels)
    errors: list[str] = []
    bus.error.connect(errors.append)
    channels.remove(DEFAULT_CHANNEL)  # the only one: nothing is selected now
    assert channels.active == ""
    assert bus.bus is None and bus.notifier is None and not bus.is_connected
    assert bus.now() == 0.0
    bus.send(0x123, b"\x01")
    assert bus.send_periodic(0x123, b"\x01", 0.1) is None
    assert errors == ["No channel selected", "No channel selected"]


def test_sending_on_one_channel_does_not_reach_the_other(app, channels):
    merged: list[Frame] = []
    channels.frames.connect(merged.extend)
    one = channels.get(DEFAULT_CHANNEL)
    two = channels.add("CAN 2")
    one.connect_bus("virtual", "vch5", 500000, False)
    two.connect_bus("virtual", "vch6", 500000, False)  # a different virtual bus

    one.send(0x321, b"\xaa")
    wait_until(app, lambda: merged)
    wait_until(app, lambda: True, 0.2)
    assert [f.channel for f in merged if f.can_id == 0x321] == [DEFAULT_CHANNEL]
