"""Transmit rows whose data is built from a DBC message or a CANopen RPDO."""

import struct
import time

import pytest
from PySide6.QtCore import Qt

from pycangui import resources
from pycangui.canopen.manager import CanopenManager
from pycangui.core.bus import BusManager, Frame
from pycangui.core.context import Context
from pycangui.core.dbc import DbcDecoder
from pycangui.ui.tx_view import COL_CYCLIC, COL_DATA, COL_ID, COL_NAME, TxView


def wait_until(app, pred, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not pred():
        app.processEvents()
        if time.monotonic() > deadline:
            raise AssertionError("timed out")
        time.sleep(0.005)


@pytest.fixture
def stack(app, tmp_path, monkeypatch, demo_device):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    ctx = Context(log=print)
    bus = BusManager()
    canopen = CanopenManager(bus)
    dbc = DbcDecoder()
    dbc.load(resources.path("demo.dbc"))
    view = TxView(bus, ctx, dbc, canopen)
    bus.connect_bus("virtual", "vcan_txsrc", 500000, False)
    demo = demo_device(bus, kinds=["canopen_device"])
    yield app, bus, view, canopen, demo, ctx
    canopen.shutdown()
    bus.disconnect_bus()


def test_dbc_row_encodes_from_signals(stack):
    app, bus, view, _canopen, _demo, ctx = stack
    received: list[Frame] = []
    bus.frames.connect(received.extend)

    row = view.add_message({"kind": "dbc", "message": "PumpCommand", "period": 50})
    item = view.item(row)
    assert item.text(COL_ID) == "123"
    names = [item.child(i).text(COL_NAME) for i in range(item.childCount())]
    assert names == ["PumpEnable", "PumpSpeedDemand"]
    assert item.text(COL_DATA) == "00 00 00 00"

    # editing a signal in physical units re-encodes the frame (0.1 rpm/bit)
    item.child(0).setText(COL_DATA, "1")
    item.child(1).setText(COL_DATA, "250")
    assert item.text(COL_DATA) == "01 C4 09 00"  # 250 rpm at 0.1 rpm/bit = 2500

    view.send_row(row)
    # The one that was sent, not whichever arrived last: the demo device is on
    # this bus too, so the tail of the list is anybody's.
    ours = lambda: [f for f in received if f.can_id == 0x123]  # noqa: E731
    wait_until(app, ours)
    assert ours()[-1].data == bytes.fromhex("01C40900")

    # the round trip decodes back to what was typed
    _msg, values = view.dbc.decode(ours()[-1])
    assert values == {"PumpEnable": 1, "PumpSpeedDemand": pytest.approx(250)}

    saved = ctx.settings.get("tx.messages")[0]
    assert saved["kind"] == "dbc" and saved["message"] == "PumpCommand"
    assert saved["signals"]["PumpSpeedDemand"] == "250"


def test_rpdo_row_drives_the_demo_node(stack):
    app, _bus, view, canopen, demo, _ctx = stack
    # loading the EDS is enough: the mapping comes from the file, no bus traffic
    canopen.load_eds(5, str(resources.path("demo.eds")))
    wait_until(app, lambda: canopen.rpdos(5))

    number, name, variables = canopen.rpdos(5)[0]
    assert "Speed demand" in " ".join(variables)

    row = view.add_message({"kind": "rpdo", "node": 5, "pdo": number, "period": 100})
    item = view.item(row)
    assert item.text(COL_NAME) == f"node 5 {name}"
    assert item.text(COL_ID) == "205"  # 0x200 + node 5, from the node's mapping

    item.child(0).setText(COL_DATA, "-250")
    assert item.text(COL_DATA) == "06 FF"
    demo["canopen_device"].state.device.nmt.state = "OPERATIONAL"
    view.item(row).setCheckState(COL_CYCLIC, Qt.Checked)  # transmit it cyclically
    wait_until(
        app,
        lambda: (
            struct.unpack("<h", demo["canopen_device"].state.device.get_data(0x2001, 0))[0] == -250
        ),
        timeout=3,
    )
    view.stop_all()
    assert view._tasks == {}
