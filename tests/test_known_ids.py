# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""What pycangui will put a name to, as Help > Known CAN ids lists it."""

from PySide6.QtCore import QSettings

from pycangui import resources
from pycangui.canopen.manager import CanopenManager
from pycangui.core import known_ids
from pycangui.core.bus import BusManager
from pycangui.core.dbc import DbcDecoder
from pycangui.ui.help_menu import KnownIdsDialog
from pycangui.ui.main_window import MainWindow


def test_nothing_is_known_before_anything_is_loaded(app, tmp_path, monkeypatch):
    """A fresh workspace knows the UDS addresses and nothing else: no
    database, no node, no XCP identifiers."""
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    window = MainWindow()

    entries = known_ids.collect(window.dbc, window.canopen, window.uds, window.xcp)

    assert {e.source for e in entries} == {"UDS"}, "only what has addresses to start with"
    window.close()


def test_a_loaded_database_lists_its_messages(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    dbc = DbcDecoder()
    dbc.load(resources.path("demo.dbc"))

    entries = known_ids.from_databases(dbc)

    assert entries, "the demo database has messages in it"
    assert all(e.source == "DBC" for e in entries)
    assert any(e.name == "DriveStatus" for e in entries)


def test_the_canopen_entries_appear_with_the_first_node(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    bus = BusManager()
    manager = CanopenManager(bus)
    bus.connect_bus("virtual", "vcan_known", 500000, False)
    assert known_ids.from_canopen(manager) == []

    manager.add_node(5)
    entries = known_ids.from_canopen(manager)

    names = {e.name for e in entries}
    assert "Heartbeat n5" in names and "SYNC" in names
    assert all(e.source == "CANopen" for e in entries)
    manager.shutdown()
    bus.disconnect_bus()


def test_an_address_nobody_set_is_not_listed(app, tmp_path, monkeypatch):
    from pycangui.uds import NO_ID

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    window = MainWindow()
    assert len(known_ids.from_uds(window.uds)) == 3, "request, response and functional"

    window.uds.config.functional_id = NO_ID
    assert len(known_ids.from_uds(window.uds)) == 2
    window.close()


def test_xcp_is_listed_once_its_identifiers_are_given(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    window = MainWindow()
    assert known_ids.from_xcp(window.xcp) == [], "the boxes start empty"

    window.xcp.set_ids(0x7A0, 0x7A1, False)
    entries = known_ids.from_xcp(window.xcp)

    assert [e.can_id for e in entries] == [0x7A0, 0x7A1]
    assert all(e.source == "XCP" for e in entries)
    window.close()


def test_an_id_two_sources_claim_is_pointed_out():
    """The first one wins in the trace, so the second never appears."""
    entries = [
        known_ids.Known(0x185, False, "DriveStatus", "DBC"),
        known_ids.Known(0x185, False, "TxPDO1 n5", "CANopen"),
        known_ids.Known(0x200, False, "PumpCommand", "DBC"),
    ]
    assert known_ids.clashes(entries) == {0x185}


def test_the_id_is_shown_as_the_trace_writes_it():
    assert known_ids.Known(0x185, False, "x", "y").shown == "185"
    assert known_ids.Known(0x18DAF110, True, "x", "y").shown == "18DAF110"


def test_the_dialog_lists_every_entry(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    window = MainWindow()
    entries = known_ids.collect(window.dbc, window.canopen, window.uds, window.xcp)

    dialog = KnownIdsDialog(window, entries)

    assert dialog.table.rowCount() == len(entries)
    assert dialog.table.item(0, 2).text() == entries[0].source
    dialog.close()
    window.close()


def test_the_dialog_says_so_when_there_is_nothing(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    window = MainWindow()

    dialog = KnownIdsDialog(window, [])

    assert dialog.table.rowCount() == 0, "an empty table rather than a dialog that will not open"
    dialog.close()
    window.close()


def test_the_list_is_in_numerical_order_whatever_named_each_one(app, tmp_path, monkeypatch):
    """Somebody reading this has an id in front of them, not a source."""
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    window = MainWindow()
    window.dbc.load(resources.path("demo.dbc"))
    window.xcp.set_ids(0x7A0, 0x7A1, False)

    entries = known_ids.collect(window.dbc, window.canopen, window.uds, window.xcp)

    ids = [e.can_id for e in entries]
    assert ids == sorted(ids)
    assert len({e.source for e in entries}) > 1, "and they are mixed together, not grouped"
    window.close()


def test_29_bit_ids_come_after_the_11_bit_ones():
    entries = [
        known_ids.Known(0x18DAF110, True, "wide", "DBC"),
        known_ids.Known(0x700, False, "narrow", "CANopen"),
    ]
    ordered = sorted(entries, key=lambda k: (k.extended, k.can_id, k.source))
    assert [e.name for e in ordered] == ["narrow", "wide"], "0x700 is not larger than 0x18DAF110"


def test_copy_puts_the_list_somewhere_it_can_be_pasted(app, tmp_path, monkeypatch):
    """The columns line up on screen and nowhere else, so the list is only
    useful outside the window if it leaves in a shape that travels."""
    from PySide6.QtGui import QGuiApplication

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    window = MainWindow()
    entries = known_ids.collect(window.dbc, window.canopen, window.uds, window.xcp)
    dialog = KnownIdsDialog(window, entries)

    dialog.copy_to_clipboard()

    text = QGuiApplication.clipboard().text()
    lines = text.splitlines()
    assert lines[0].split("\t") == list(dialog.COLUMNS), "a heading row, as a spreadsheet wants"
    assert len(lines) == len(entries) + 1
    assert all(len(line.split("\t")) == 3 for line in lines), "one row per id, tab separated"
    dialog.close()
    window.close()
