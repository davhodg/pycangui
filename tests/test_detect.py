"""Finding real adapters, naming them unambiguously, and typing the channel right."""

import can
import pytest

from pycangui.core import detect
from pycangui.core.bus import BusManager

# What python-can reports with two IXXAT dongles attached, each with two
# channels: the channel numbers repeat, and only the hardware id separates them.
TWO_DONGLES = [
    {"interface": "ixxat", "channel": 0, "unique_hardware_id": "HW-A"},
    {"interface": "ixxat", "channel": 1, "unique_hardware_id": "HW-A"},
    {"interface": "ixxat", "channel": 0, "unique_hardware_id": "HW-B"},
    {"interface": "ixxat", "channel": 1, "unique_hardware_id": "HW-B"},
]


# --- the type of a channel ----------------------------------------------------------
@pytest.mark.parametrize(
    ("interface", "typed", "expected"),
    [
        ("ixxat", "0", 0),  # declares channel: int -- a string does not open
        ("kvaser", "1", 1),
        ("socketcan", "0", "0"),  # declares str: must stay a string
        ("socketcan", "can0", "can0"),
        ("pcan", "PCAN_USBBUS1", "PCAN_USBBUS1"),
        ("ixxat", "not a number", "not a number"),  # never mangled into an int
        ("nosuchinterface", "0", "0"),  # nothing known, so nothing assumed
    ],
)
def test_coerce_channel(interface, typed, expected):
    result = detect.coerce_channel(interface, typed)
    assert result == expected and type(result) is type(expected)


def test_a_detected_channel_is_left_alone():
    """Detection already returns the right type; do not second-guess it."""
    assert detect.coerce_channel("ixxat", 3) == 3


def test_the_backends_really_do_disagree():
    """The reason coercion exists, asserted against python-can itself."""
    assert "int" in detect.channel_annotation("ixxat")
    assert "int" not in detect.channel_annotation("socketcan")


# --- which adapter -------------------------------------------------------------------
def test_two_dongles_are_told_apart(monkeypatch):
    monkeypatch.setattr(can, "detect_available_configs", lambda *_a, **_k: TWO_DONGLES)
    found = detect.detect_channels("ixxat")
    assert len(found) == 4

    labels = [c.label for c in found]
    assert len(set(labels)) == 4, f"every entry must be distinguishable: {labels}"
    assert "HW-A" in labels[0] and "HW-B" in labels[2]

    # Channel 0 appears twice, so the channel alone cannot identify a device.
    channel_zero = [c for c in found if c.value == 0]
    assert len(channel_zero) == 2
    assert {c.extra["unique_hardware_id"] for c in channel_zero} == {"HW-A", "HW-B"}


def test_detection_that_fails_is_not_an_error(monkeypatch):
    """A missing vendor DLL raises from inside the backend; that is normal."""

    def explode(*_a, **_k):
        raise OSError("vcinpl.dll not found")

    monkeypatch.setattr(can, "detect_available_configs", explode)
    assert detect.detect_channels("ixxat") == []


# --- connecting ----------------------------------------------------------------------
@pytest.fixture
def captured(monkeypatch):
    """Capture the kwargs can.Bus is called with, then refuse to build one."""
    seen = {}

    def fake_bus(**kwargs):
        seen.update(kwargs)
        raise OSError("no hardware here")

    monkeypatch.setattr(can, "Bus", fake_bus)
    return seen


def test_connect_passes_the_hardware_id_through(app, captured):
    bus = BusManager()
    bus.connect_bus("ixxat", "0", 500000, False, {"unique_hardware_id": "HW-B"})
    assert captured["interface"] == "ixxat"
    assert captured["channel"] == 0, "the int the backend declares, not the text box's string"
    assert captured["unique_hardware_id"] == "HW-B", "otherwise the driver picks a dongle for you"
    assert captured["bitrate"] == 500000


def test_our_bitrate_wins_over_a_detected_one(app, captured):
    """Bitrate is the user's choice; an adapter does not get to override it."""
    bus = BusManager()
    bus.connect_bus("ixxat", "1", 250000, False, {"bitrate": 999, "unique_hardware_id": "HW-A"})
    assert captured["bitrate"] == 250000
    assert captured["unique_hardware_id"] == "HW-A"


def test_a_failed_connect_reports_and_stays_disconnected(app, captured):
    bus = BusManager()
    errors = []
    bus.error.connect(errors.append)
    bus.connect_bus("ixxat", "0", 500000, False)
    assert not bus.is_connected
    assert errors and "no hardware here" in errors[0]


def test_the_description_says_which_adapter(app, monkeypatch):
    """Two dongles on two channels must not both read "ixxat:0"."""
    bus = BusManager()
    real_bus = can.Bus  # grab it before patching, or the stand-in calls itself
    monkeypatch.setattr(can, "Bus", lambda **_k: real_bus(interface="virtual", channel="descr"))
    bus.connect_bus("ixxat", "0", 500000, False, {"unique_hardware_id": "HW-B"})
    assert "HW-B" in bus.description
    bus.disconnect_bus()


# --- the connect bar -----------------------------------------------------------------
@pytest.fixture
def bar(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    from pycangui.core.channels import Channels
    from pycangui.core.context import Context
    from pycangui.ui.connect_bar import ConnectBar

    channels = Channels()
    widget = ConnectBar(channels, Context(log=print))
    yield widget
    widget.shutdown()
    channels.shutdown()
    widget.deleteLater()


def settle(app, pred, timeout=5.0):
    import time

    deadline = time.monotonic() + timeout
    while not pred() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)


def test_detect_fills_the_channel_list(app, bar, monkeypatch):
    """The complaint this fixes: the channel was a text box you had to guess at."""
    monkeypatch.setattr(can, "detect_available_configs", lambda *_a, **_k: TWO_DONGLES)
    bar.interface.setCurrentText("ixxat")
    bar.detect_channels()
    settle(app, lambda: bar.channel.count() >= 4)

    labels = [bar.channel.itemText(i) for i in range(bar.channel.count())]
    assert len(labels) == 4 and len(set(labels)) == 4
    assert all("HW-A" in x or "HW-B" in x for x in labels)


def test_picking_the_second_dongle_connects_to_that_one(app, bar, monkeypatch):
    monkeypatch.setattr(can, "detect_available_configs", lambda *_a, **_k: TWO_DONGLES)
    bar.interface.setCurrentText("ixxat")
    bar.detect_channels()
    settle(app, lambda: bar.channel.count() >= 4)

    requests = []
    bar.connect_requested.connect(lambda *a: requests.append(a))
    bar.channel.setCurrentIndex(2)  # channel 0 of HW-B, not HW-A
    assert bar.current_channel() == "0"
    assert bar.current_extra() == {"unique_hardware_id": "HW-B"}

    bar.button.setChecked(True)
    assert requests, "connecting must emit a request"
    interface, channel, _bitrate, _fd, extra = requests[0]
    assert (interface, channel) == ("ixxat", "0")
    assert extra == {"unique_hardware_id": "HW-B"}, "the chosen dongle must reach can.Bus"


def test_a_typed_channel_still_works(app, bar, monkeypatch):
    """Not every backend can enumerate; typing one in must remain possible."""
    monkeypatch.setattr(can, "detect_available_configs", lambda *_a, **_k: [])
    bar.interface.setCurrentText("slcan")
    bar.channel.setCurrentText("COM3")
    bar.detect_channels()
    settle(app, lambda: not bar._detecting)

    assert bar.current_channel() == "COM3", "detection finding nothing must not erase it"
    assert bar.current_extra() == {}
