# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Frames get from the bus to the trace, through the whole window.

Every other test builds one piece at a time, which is how a change to the
connect bar's channel widget could break the demo device -- its only caller
asked the widget for its text, and a combo box has none -- without a single
test noticing. This drives the window the way a person does.
"""

import time

import pytest
from PySide6.QtCore import QSettings

from pycangui.canopen import PdoConfig
from pycangui.canopen.manager import CanopenManager
from pycangui.core.bus import BusManager, Frame
from pycangui.core.detect import DEMO_CHANNEL, channels_for
from pycangui.ui.main_window import MainWindow


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    win = MainWindow()
    yield win
    win.close()


def run_for(app, seconds, until=None):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        if until is not None and until():
            return
        time.sleep(0.01)


def connect_to(app, window, channel):
    window.connect_bar.channel.setCurrentIndex(
        window.connect_bar.channel.findData({"channel": channel})
    )
    if window.connect_bar.current_channel() != channel:  # findData is exact-match only
        window.connect_bar.channel.setCurrentText(channel)
    window.connect_bar.button.setChecked(True)
    app.processEvents()


def test_connecting_to_the_demo_channel_starts_the_demo(app, window):
    """Selecting the channel is the switch: there is no separate on/off.

    Connecting to the virtual bus and finding it empty, with nothing to say
    why, was the whole problem with a menu item somewhere else.
    """
    connect_to(app, window, DEMO_CHANNEL)
    assert window.channels.active_bus().is_connected, window.log.toPlainText()
    assert window._demo, f"the demo did not start: {window.log.toPlainText()}"

    bus = window.channels.active_bus()
    run_for(app, 4.0, until=lambda: window.trace.model.rowCount() > 10 and bus.load_percent > 0)
    captured = window.trace.model.rowCount()
    assert captured > 10, f"no traffic reached the trace: {window.log.toPlainText()}"
    assert window.trace.table.model().rowCount() == captured, "and none of it is filtered out"
    assert window._frame_count == captured
    assert bus.load_percent > 0, "bus load must move too"


def test_an_empty_virtual_channel_stays_empty(app, window):
    """vcan1 says "empty" in the list, and means it."""
    connect_to(app, window, "vcan1")
    assert window.channels.active_bus().is_connected
    assert not window._demo, "only the demo channel runs the demo"
    run_for(app, 1.0)
    assert window.trace.model.rowCount() == 0


def test_disconnecting_stops_the_demo(app, window):
    connect_to(app, window, DEMO_CHANNEL)
    assert window._demo
    window.connect_bar.button.setChecked(False)
    app.processEvents()
    assert not window._demo, "nothing should be left running on a bus nobody is on"


def test_the_channel_list_says_what_each_one_carries(app, window):
    window.connect_bar._on_channel_expanded()  # opening the list is what fills it
    labels = [
        window.connect_bar.channel.itemText(i) for i in range(window.connect_bar.channel.count())
    ]
    assert any(DEMO_CHANNEL in x and "demo" in x.lower() for x in labels), labels
    assert any("empty" in x for x in labels), labels
    # No random throwaway names from the backend's own detection.
    assert not any("channel-" in x for x in labels), labels


def test_the_virtual_channels_are_a_known_set():
    labels = [c.label for c in channels_for("virtual")]
    assert labels[0].startswith(DEMO_CHANNEL)
    assert all("channel-" not in x for x in labels)


def test_the_demo_is_decoded_not_just_listed(app, window):
    """The trace labels CANopen ids, so a heartbeat is not just an id."""
    connect_to(app, window, DEMO_CHANNEL)
    run_for(app, 3.0, until=lambda: window.trace.model.rowCount() > 10)

    kinds = {window.trace.model.data(window.trace.model.index(row, 4)) for row in range(20)}
    assert any("Heartbeat" in str(k) or "PDO" in str(k) for k in kinds), kinds


# --- who gets to say what a frame is ---------------------------------------------------
def test_canopen_names_nothing_until_a_node_is_known(app, tmp_path, monkeypatch):
    """The predefined connection set claims 0x180 to 0x67F. Read that way on
    a bus with no CANopen on it, an ordinary message becomes 'RxPDO1 n5'."""
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    bus = BusManager()
    manager = CanopenManager(bus)  # before the bus connects: it learns by signal
    bus.connect_bus("virtual", "vcan_labels", 500000, False)
    pdo = Frame(0.0, "CAN", 0x185, False, False, True, b"\x00")

    assert manager.classify(pdo) is None, "nobody has said there is a node here"

    manager.add_node(5)
    assert manager.classify(pdo) == "TxPDO1 n5"
    manager.shutdown()
    bus.disconnect_bus()


def test_the_broadcast_ids_come_with_the_first_node(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    bus = BusManager()
    manager = CanopenManager(bus)  # before the bus connects: it learns by signal
    bus.connect_bus("virtual", "vcan_labels", 500000, False)
    sync = Frame(0.0, "CAN", 0x080, False, False, True, b"")

    assert manager.classify(sync) is None
    manager.add_node(3)
    assert manager.classify(sync) == "SYNC", "a bus with a node on it has a SYNC id"
    manager.shutdown()
    bus.disconnect_bus()


def test_a_node_on_its_own_sdo_channel_is_named_there(app, tmp_path, monkeypatch):
    """The channel somebody configured is where that node's SDOs are, and
    0x600 plus the node id is then somebody else's id."""
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    bus = BusManager()
    manager = CanopenManager(bus)  # before the bus connects: it learns by signal
    bus.connect_bus("virtual", "vcan_labels", 500000, False)
    manager.add_node(4)
    manager.set_sdo_channels({4: (0x640, 0x5C0)})

    moved = Frame(0.0, "CAN", 0x640, False, False, True, b"")
    assert manager.classify(moved) == "SDO-R n4"
    assert manager.classify(Frame(0.1, "CAN", 0x5C0, False, False, True, b"")) == "SDO-T n4"
    manager.shutdown()
    bus.disconnect_bus()


def test_nothing_on_an_extended_id_is_called_canopen(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    bus = BusManager()
    manager = CanopenManager(bus)  # before the bus connects: it learns by signal
    bus.connect_bus("virtual", "vcan_labels", 500000, False)
    manager.add_node(5)
    j1939 = Frame(0.0, "CAN", 0x18FEF105, True, False, True, b"")
    assert manager.classify(j1939) is None
    manager.shutdown()
    bus.disconnect_bus()


def test_a_pdo_the_file_gives_no_value_for_is_not_a_frame_at_zero(app, tmp_path, monkeypatch):
    """An EDS can declare the communication object and leave the value out,
    which reads as zero -- and zero is the NMT id, not this node's PDO."""
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    bus = BusManager()
    manager = CanopenManager(bus)
    bus.connect_bus("virtual", "vcan_labels", 500000, False)
    manager.add_node(5)
    monkeypatch.setattr(
        manager,
        "pdo_configs",
        lambda _node: [
            PdoConfig(node_id=5, direction="TPDO", number=1, name="", cob_id=0, enabled=True),
            PdoConfig(node_id=5, direction="TPDO", number=2, name="", cob_id=0x285, enabled=False),
            PdoConfig(node_id=5, direction="TPDO", number=3, name="", cob_id=0x385, enabled=True),
        ],
    )
    manager.forget_labels()

    nmt = Frame(0.0, "CAN", 0x000, False, False, True, b"")
    assert manager.classify(nmt) == "NMT", "not the node's PDO"
    disabled = Frame(0.1, "CAN", 0x285, False, False, True, b"")
    assert manager.classify(disabled) == "TxPDO2 n5", "the predefined place still stands"
    real = Frame(0.2, "CAN", 0x385, False, False, True, b"")
    assert manager.classify(real) == "TPDO3 n5", "a configured one is named where it is"
    manager.shutdown()
    bus.disconnect_bus()


def test_a_pdo_somewhere_else_is_named_where_it_actually_is(app, tmp_path, monkeypatch):
    """A DCF carries the configured identifier, which is the point of one."""
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    bus = BusManager()
    manager = CanopenManager(bus)
    bus.connect_bus("virtual", "vcan_labels", 500000, False)
    manager.add_node(5)
    monkeypatch.setattr(
        manager,
        "pdo_configs",
        lambda _node: [
            PdoConfig(node_id=5, direction="TPDO", number=1, name="", cob_id=0x2A5, enabled=True)
        ],
    )
    manager.forget_labels()

    moved = Frame(0.0, "CAN", 0x2A5, False, False, True, b"")
    assert manager.classify(moved) == "TPDO1 n5"
    manager.shutdown()
    bus.disconnect_bus()
