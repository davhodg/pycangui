"""CAN FD: the data rate, and ISO-TP frames longer than eight bytes.

Eight bytes a frame is what makes UDS over a classic bus slow -- a flow
control round for every seven bytes of payload.  CAN FD's whole value here is
CAN_DL, and it is worth nothing unless the channel underneath actually opened
as FD, which is not the same as having ticked the box.
"""

import time

import can
import pytest
from PySide6.QtCore import QSettings

from pycangui.core.bus import BusManager
from pycangui.core.context import Context
from pycangui.core.hooks import Hooks
from pycangui.uds import CAN_DL, UdsConfig
from pycangui.uds.manager import UdsManager
from pycangui.uds.transport import CanIsoTpTransport
from pycangui.ui.connect_bar import DATA_BITRATES
from pycangui.ui.uds_view import UdsView


class Grab(can.Listener):
    """A real can.Listener: the Notifier calls the listener, not a duck."""

    def __init__(self) -> None:
        self.seen: list[can.Message] = []

    def on_message_received(self, message: can.Message) -> None:
        self.seen.append(message)


def wait_for(app, pred, timeout=3.0):
    """The isotp stack runs on the notifier thread, so this needs real time."""
    deadline = time.monotonic() + timeout
    while not pred() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)


@pytest.fixture
def stack(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    ctx = Context(log=print)
    bus = BusManager()
    manager = UdsManager(bus, Hooks(ctx), ctx)
    view = UdsView(manager, ctx)
    yield bus, manager, view
    manager.shutdown()
    bus.disconnect_bus()


# --- the rate ---------------------------------------------------------------------------
def test_ten_megabit_is_offered():
    assert DATA_BITRATES[-1] == 10_000_000
    assert 2_000_000 in DATA_BITRATES, "and the usual one is still there"


# --- what the channel opened as ---------------------------------------------------------
def test_a_channel_knows_whether_it_really_opened_as_fd(app):
    """Ticking FD is not the same as getting it: a backend that takes no fd
    keyword opens classic, and the stacks above have to know which."""
    bus = BusManager()
    bus.connect_bus("virtual", "vcan_fd_a", 500_000, True)
    assert bus.fd, "the virtual bus carries whatever it is handed"
    bus.disconnect_bus()
    assert not bus.fd, "and forgets when it lets go"

    bus.connect_bus("virtual", "vcan_fd_b", 500_000, False)
    assert not bus.fd
    bus.disconnect_bus()


# --- the pane ---------------------------------------------------------------------------
def test_the_lengths_are_only_offered_on_an_fd_channel(stack):
    bus, _manager, view = stack
    assert not view.can_dl.isEnabled(), "nothing is connected yet"
    assert view.can_dl.currentText() == "8"

    bus.connect_bus("virtual", "vcan_fd_ui", 500_000, True)
    assert view.can_dl.isEnabled() and view.brs.isEnabled()
    view.can_dl.setCurrentText("64")

    bus.disconnect_bus()
    assert not view.can_dl.isEnabled()
    assert view.can_dl.currentText() == "8", "a classic channel has no other length"


def test_only_the_lengths_can_fd_actually_has_are_offered(stack):
    _bus, _manager, view = stack
    offered = [view.can_dl.itemData(i) for i in range(view.can_dl.count())]
    assert offered == list(CAN_DL) == [8, 12, 16, 20, 24, 32, 48, 64]


def test_the_choice_reaches_the_config(stack):
    bus, _manager, view = stack
    bus.connect_bus("virtual", "vcan_fd_cfg", 500_000, True)
    view.can_dl.setCurrentText("32")
    view.brs.setChecked(True)

    config = view._config()
    assert config.can_fd is True
    assert config.tx_data_length == 32
    assert config.bitrate_switch is True


def test_a_classic_channel_reports_itself_as_classic(stack):
    bus, _manager, view = stack
    bus.connect_bus("virtual", "vcan_classic", 500_000, False)
    config = view._config()
    assert config.can_fd is False
    assert config.tx_data_length == 8


# --- the transport ----------------------------------------------------------------------
def test_the_transport_sends_long_frames(app):
    """The point of all of it: a message that took several frames takes one."""
    bus = BusManager()
    bus.connect_bus("virtual", "vcan_isotp_fd", 500_000, True)
    grab = Grab()
    bus.add_listener(grab)
    seen = grab.seen

    config = UdsConfig(can_fd=True, tx_data_length=64, bitrate_switch=True, padding=None)
    transport = CanIsoTpTransport(bus, config)
    transport.open()
    transport.send(bytes(range(40)))  # far more than a classic frame holds
    wait_for(app, lambda: seen)
    transport.close()
    bus.disconnect_bus()

    assert seen, "nothing went out"
    first = seen[0]
    assert first.is_fd, "an FD frame, not a classic one"
    assert len(first.data) > 8, f"still eight bytes: {len(first.data)}"
    assert first.bitrate_switch, "BRS was asked for"


def test_a_classic_transport_still_sends_eight(app):
    bus = BusManager()
    bus.connect_bus("virtual", "vcan_isotp_classic", 500_000, False)
    grab = Grab()
    bus.add_listener(grab)
    seen = grab.seen

    # Even asked for 64: a classic channel cannot carry it, and the transport
    # is told the channel's truth rather than the pane's wish.
    transport = CanIsoTpTransport(bus, UdsConfig(can_fd=False, tx_data_length=64))
    transport.open()
    transport.send(bytes(range(40)))
    wait_for(app, lambda: seen)
    transport.close()
    bus.disconnect_bus()

    assert seen and len(seen[0].data) == 8
    assert not seen[0].is_fd
