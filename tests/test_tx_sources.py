# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Transmit rows whose data is built from a DBC message or a CANopen RPDO."""

import struct
import time
from pathlib import Path

import pytest
from PySide6.QtCore import Qt

from pycangui import resources
from pycangui.canopen.manager import CanopenManager
from pycangui.core.bus import BusManager, Frame
from pycangui.core.context import Context
from pycangui.core.dbc import DbcDecoder
from pycangui.ui.tx_view import COL_CYCLIC, COL_DATA, COL_ID, COL_NAME, ROLE_DATABASE, TxView


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


# --- which database a row came from ---------------------------------------------------------
def test_a_dbc_row_remembers_the_file_its_message_came_from(stack):
    _app, _bus, view, _canopen, _demo, ctx = stack
    source = view.dbc.source_of("PumpCommand")
    assert source and Path(source).name == "demo.dbc"

    row = view.add_message({"kind": "dbc", "message": "PumpCommand", "period": 50})
    assert view.item(row).data(0, ROLE_DATABASE) == source
    assert ctx.settings.get("tx.messages")[0]["database"] == source


def test_the_file_is_still_known_once_the_database_is_gone(stack):
    _app, _bus, view, _canopen, _demo, ctx = stack
    row = view.add_message({"kind": "dbc", "message": "PumpCommand", "period": 50})
    source = view.item(row).data(0, ROLE_DATABASE)

    view.dbc.unload(source)
    view.refresh_sources()
    assert view.dbc.message_by_name("PumpCommand") is None
    assert view.item(0).data(0, ROLE_DATABASE) == source, "kept with the row"
    assert ctx.settings.get("tx.messages")[0]["database"] == source


def test_how_an_id_is_written_says_how_wide_it_is(stack):
    _app, _bus, view, _canopen, _demo, _ctx = stack
    widths = {}
    for typed in ("123", "7FF", "800", "18FF50E5", "00000123"):
        row = view.add_message({"kind": "raw", "id": typed, "data": "00"})
        can_id, _data, extended, _fd, _period = view._message(row)
        widths[typed] = (can_id, extended)
    assert widths == {
        "123": (0x123, False),
        "7FF": (0x7FF, False),
        "800": (0x800, True),
        "18FF50E5": (0x18FF50E5, True),
        "00000123": (0x123, True),
    }


def test_a_row_saved_with_the_ext_box_ticked_is_still_29_bit(stack):
    _app, _bus, view, _canopen, _demo, ctx = stack
    row = view.add_message({"kind": "raw", "id": "123", "ext": True, "data": "00"})
    can_id, _data, extended, _fd, _period = view._message(row)
    assert (can_id, extended) == (0x123, True)
    assert ctx.settings.get("tx.messages")[0]["ext"] is True, "for a pycangui that has the box"


def test_a_row_saved_before_files_were_kept_learns_its_own(stack):
    _app, _bus, view, _canopen, _demo, _ctx = stack
    row = view.add_message({"kind": "dbc", "message": "PumpCommand", "database": ""})
    assert Path(view.item(row).data(0, ROLE_DATABASE)).name == "demo.dbc"


def test_what_acts_on_a_message_waits_for_one_to_be_selected(stack):
    _app, _bus, view, _canopen, _demo, _ctx = stack
    row = view.add_message({"kind": "dbc", "message": "PumpCommand", "period": 50})
    view.tree.clearSelection()
    assert not any(button.isEnabled() for button in view.selection_buttons)
    view.tree.setCurrentItem(view.item(row))
    assert all(button.isEnabled() for button in view.selection_buttons)
    view.remove_selected()
    assert not any(button.isEnabled() for button in view.selection_buttons), "gone with the row"


# --- the period of a row from a database ----------------------------------------------------
def test_a_database_rows_period_is_the_senders_to_set(stack):
    from pycangui.ui.tx_view import COL_PERIOD, editable_columns

    app, _bus, view, _canopen, _demo, ctx = stack
    row = view.add_message({"kind": "dbc", "message": "PumpCommand", "period": 100})
    item = view.item(row)
    assert editable_columns(item) == (COL_PERIOD,), "and nothing else: the database has the rest"

    index = view.tree.indexFromItem(item, COL_PERIOD)
    view.tree.edit(index)
    app.processEvents()
    assert view.tree.state() == view.tree.State.EditingState, "an editor opens on the period"
    view.tree.closePersistentEditor(item, COL_PERIOD)
    view.tree.setCurrentItem(None)
    app.processEvents()

    assert not view.tree.edit(view.tree.indexFromItem(item, COL_ID)), "but not on the id"

    item.setText(COL_PERIOD, "20")
    assert ctx.settings.get("tx.messages")[0]["period"] == "20"
    assert view._message(row)[4] == pytest.approx(0.020)


def test_a_new_database_row_takes_the_databases_cycle_time(stack, tmp_path, monkeypatch):
    from pycangui.ui import tx_view
    from pycangui.ui.tx_view import COL_PERIOD

    _app, _bus, view, _canopen, _demo, _ctx = stack
    cyclic = tmp_path / "cyclic.dbc"
    cyclic.write_text(
        'VERSION ""\nNS_ :\nBS_:\nBU_:\n'
        "BO_ 768 Heater: 2 Vector__XXX\n"
        ' SG_ Level : 0|16@1+ (1,0) [0|0] "" Vector__XXX\n'
        'BA_DEF_ BO_ "GenMsgCycleTime" INT 0 65535;\n'
        'BA_DEF_DEF_ "GenMsgCycleTime" 0;\n'
        'BA_ "GenMsgCycleTime" BO_ 768 40;\n',
        encoding="utf-8",
    )
    view.dbc.load(cyclic)

    class Picked:
        def __init__(self, name):
            self.name = name

        def exec(self):
            return tx_view.QDialog.Accepted

        def chosen(self):
            return self.name

    for name, period in (("Heater", "40"), ("PumpCommand", str(tx_view.DEFAULT_PERIOD_MS))):
        monkeypatch.setattr(tx_view, "MessagePicker", lambda *a, n=name: Picked(n))
        view._add_from_dbc()
        assert view.item(view.message_count() - 1).text(COL_PERIOD) == period
