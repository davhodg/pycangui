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


@pytest.mark.skipif(
    not detect.channel_annotation("ixxat"),
    reason="the ixxat backend cannot be imported on this platform",
)
def test_the_backends_really_do_disagree():
    """The reason coercion exists, asserted against python-can itself.

    Only runs where the backend imports: ixxat is Windows-only, so on Linux
    there is no signature to read -- which is what INT_CHANNEL_BACKENDS covers.
    """
    assert "int" in detect.channel_annotation("ixxat")
    assert "int" not in detect.channel_annotation("socketcan")


@pytest.mark.parametrize(
    ("annotation", "expected"), [("<class 'int'>", 7), ("<class 'str'>", "7"), ("", "7")]
)
def test_coercion_follows_the_declared_type(monkeypatch, annotation, expected):
    """The rule itself, independent of which backends this platform can load."""
    monkeypatch.setattr(detect, "channel_annotation", lambda _i: annotation)
    result = detect.coerce_channel("madeup", "7")
    assert result == expected and type(result) is type(expected)


def test_an_unimportable_backend_falls_back_to_the_table(monkeypatch):
    """On Linux the ixxat module will not load, so its signature cannot be read.

    Connecting could not work there either, but the rule must not silently
    invert just because introspection came back empty.
    """
    monkeypatch.setattr(detect, "channel_annotation", lambda _i: "")
    assert detect.coerce_channel("ixxat", "0") == 0
    assert detect.coerce_channel("socketcan", "0") == "0"


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
    # The box is seeded with suggestions at once, so a count is no longer
    # proof that detection has come back.
    settle(app, lambda: not bar._detecting)

    labels = [bar.channel.itemText(i) for i in range(bar.channel.count())]
    assert len(labels) == 4 and len(set(labels)) == 4
    assert all("HW-A" in x or "HW-B" in x for x in labels)


def test_picking_the_second_dongle_connects_to_that_one(app, bar, monkeypatch):
    monkeypatch.setattr(can, "detect_available_configs", lambda *_a, **_k: TWO_DONGLES)
    bar.interface.setCurrentText("ixxat")
    bar.detect_channels()
    # The box is seeded with suggestions at once, so a count is no longer
    # proof that detection has come back.
    settle(app, lambda: not bar._detecting)

    requests = []
    bar.connect_requested.connect(lambda *a: requests.append(a))
    bar.channel.setCurrentIndex(2)  # channel 0 of HW-B, not HW-A
    assert bar.current_channel() == "0"
    assert bar.current_extra() == {"unique_hardware_id": "HW-B"}

    bar.button.setChecked(True)
    assert requests, "connecting must emit a request"
    interface, channel, _bitrate, _fd, _data_bitrate, extra = requests[0]
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


def test_the_box_holds_only_what_was_last_used(app, bar):
    """Nothing is offered until the list is opened.

    Filling it in advance meant showing invented names as though they had been
    found -- an IXXAT was offered channels 0 to 3 whether or not any existed.
    """
    labels = [bar.channel.itemText(i) for i in range(bar.channel.count())]
    assert labels == ["vcan0"], f"just the remembered channel: {labels}"
    assert bar.current_channel() == "vcan0", "and it is still what Connect would use"
    assert bar.channel.isEnabled()


def test_opening_the_list_is_what_fills_it(app, bar):
    bar._on_channel_expanded()
    labels = [bar.channel.itemText(i) for i in range(bar.channel.count())]
    assert any("demo" in x.lower() for x in labels), labels
    assert any("empty" in x for x in labels), labels


def test_changing_interface_empties_the_list_until_it_is_opened(app, bar, monkeypatch):
    monkeypatch.setattr(can, "detect_available_configs", lambda *_a, **_k: [])
    bar.interface.setCurrentText("pcan")
    app.processEvents()
    assert bar.channel.count() == 0, "a PEAK has nothing to do with the last channel"

    bar._on_channel_expanded()
    labels = [bar.channel.itemText(i) for i in range(bar.channel.count())]
    assert "PCAN_USBBUS1" in labels, f"PCAN declares that default itself: {labels}"
    assert "vcan0" not in labels, "the virtual channel must not follow you to a PEAK"


def test_a_backend_with_no_channel_greys_the_box_out(app, bar, monkeypatch):
    """No python-can backend needs this today; the rule should still hold."""
    monkeypatch.setattr(
        "pycangui.ui.connect_bar.takes_a_channel",
        lambda _i: False,
    )
    bar._set_channel_enabled("madeup")
    assert not bar.channel.isEnabled()
    assert bar.channel.currentText() == ""
    assert "does not use a channel" in bar.channel.toolTip()


def test_the_declared_default_is_used_where_we_have_no_opinion():
    """Read from the backend's signature, so it cannot drift out of date."""
    assert detect.channel_default("pcan") == "PCAN_USBBUS1"
    assert detect.channel_default("nixnet") == "CAN1"
    assert [c.label for c in detect.channel_suggestions("nixnet")] == ["CAN1"]


# --- labels stay readable ------------------------------------------------------------
class _VectorChannelConfig:
    """What the Vector backend puts in its detected configuration.

    The real one's repr runs to a dozen lines of ctypes enums; it also carries
    a name, which is the part worth showing.
    """

    name = "VN1610 Channel 1"

    def __repr__(self):
        return (
            "VectorChannelConfig(" + ", ".join(f"field_{i}=<XL_Enum: {i}>" for i in range(40)) + ")"
        )


VECTOR = {
    "interface": "vector",
    "channel": 0,
    "channel_index": 0,
    "hw_channel": 0,
    "hw_index": 0,
    "hw_type": 55,
    "serial": 20551,
    "supports_fd": True,
    "vector_channel_config": _VectorChannelConfig(),
}


def test_a_vector_label_is_a_few_words_not_a_paragraph():
    """This filled a dialog with ctypes enums and buried the question in it."""
    label = detect.describe(VECTOR)
    assert len(label) < 60, label
    assert "20551" in label, "the serial is what tells two of them apart"
    assert "VN1610 Channel 1" in label, "and the name is what a person recognises"
    assert "XL_Enum" not in label and "channel_index" not in label


def test_the_whole_configuration_still_reaches_can_bus(app, captured):
    """Shortening the label must not shorten what the adapter is opened with."""
    bus = BusManager()
    extra = {k: v for k, v in VECTOR.items() if k not in ("interface", "channel")}
    bus.connect_bus("vector", "0", 500000, False, extra)
    assert captured["serial"] == 20551
    assert captured["hw_type"] == 55, "dropped from the label, not from the call"
    assert "vector_channel_config" in captured


def test_an_object_with_no_name_is_left_out_rather_than_printed():
    class Opaque:
        def __repr__(self):
            return "x" * 500

    assert detect.readable(Opaque()) == ""
    assert "x" * 50 not in detect.describe({"channel": 0, "thing": Opaque()})


def test_a_long_string_is_left_out_too():
    assert detect.readable("y" * 500) == ""
    assert detect.readable("short") == "short"


def test_nothing_to_say_leaves_a_bare_channel():
    assert detect.describe({"interface": "socketcan", "channel": "can0"}) == "can0"


# --- CAN FD ---------------------------------------------------------------------------
def test_the_data_rate_appears_only_when_fd_is_asked_for(bar):
    """A data rate on a classic channel is a control with nothing to do."""
    assert not any(action.isVisible() for action in bar._data_widgets)
    bar.fd.setChecked(True)
    assert all(action.isVisible() for action in bar._data_widgets)
    bar.fd.setChecked(False)
    assert not any(action.isVisible() for action in bar._data_widgets)


def test_the_slow_bitrates_are_offered(bar):
    """50 and 100 kbit/s are ordinary on machinery and marine buses."""
    offered = [bar.bitrate.itemData(i) for i in range(bar.bitrate.count())]
    assert offered == [50_000, 100_000, 125_000, 250_000, 500_000, 1_000_000]
    assert bar.bitrate.currentData() == 500_000, "still the one to start on"


def test_the_data_rate_is_only_sent_when_fd_is_ticked(bar):
    requests = []
    bar.connect_requested.connect(lambda *a: requests.append(a))

    bar.button.setChecked(True)
    assert requests[-1][4] == 0, "classic: there is no data phase to have a rate"

    bar.set_connected(False)
    bar.fd.setChecked(True)
    bar.button.setChecked(True)
    assert requests[-1][3] is True
    assert requests[-1][4] == 2_000_000


def test_which_backends_can_be_told_about_fd(app):
    """pcan, kvaser and slcan take no fd keyword: FD is a timing object to
    them, and one that needs the controller's clock frequency.

    Passing fd=True to those is swallowed by their **kwargs, so the channel
    opens as classic CAN with nothing on screen to say so.
    """
    from pycangui.core.detect import takes_data_bitrate, takes_fd

    assert takes_fd("socketcan"), "declares fd, though its data rate comes from ip link"
    assert not takes_fd("pcan"), "wants a BitTimingFd instead"
    assert not takes_fd("virtual")
    assert takes_data_bitrate("ixxat") == ("ixxat" in _importable_or_known())
    assert not takes_data_bitrate("pcan")


def _importable_or_known():
    """ixxat and vector answer from their signature where the driver is
    installed, and from the fallback table where it is not."""
    from pycangui.core.detect import DATA_BITRATE_BACKENDS, bus_parameters

    return {name for name in DATA_BITRATE_BACKENDS if "data_bitrate" in bus_parameters(name)} or (
        DATA_BITRATE_BACKENDS
    )
