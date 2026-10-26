# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Save as DBC: a CAN database made from a CANopen network's configuration."""

from types import SimpleNamespace

import canopen
import cantools
import pytest
from PySide6.QtCore import QSettings

from pycangui import resources
from pycangui.canopen import to_dbc
from pycangui.canopen.display import Display

#: canopen's own DCF writer, used to make one to read, stamps it with a call
#: Python has deprecated: theirs to change, and nothing this tests.
pytestmark = pytest.mark.filterwarnings("ignore:datetime.datetime.utcnow:DeprecationWarning")


def demo_dcf(folder, heartbeat_ms=500, emcy=0x85) -> str:
    """A DCF of the demo device as node 5, with a value for every PDO object
    -- as Save DCF would write one -- its heartbeat and its emergency COB-ID."""
    od = canopen.import_od(str(resources.path("demo.eds")), 5)
    for index in list(od.keys()):
        if 0x1400 <= index < 0x1C00:
            for sub in od[index].values():
                sub.value = sub.default
    od[0x1017].value = heartbeat_ms
    if emcy is not None:
        od.add_object(canopen.objectdictionary.ODVariable("COB-ID EMCY", 0x1014))
        od[0x1014].data_type = canopen.objectdictionary.UNSIGNED32
        od[0x1014].value = emcy
    od.node_id = 5
    path = str(folder / "drive.dcf")
    canopen.export_od(od, path, "dcf")
    return path


@pytest.fixture
def drive(tmp_path):
    """The demo device, node 5, as a DCF configures it."""
    from pycangui.canopen import load_od

    od = load_od(demo_dcf(tmp_path))
    tpdos, rpdos = to_dbc.from_dictionary(od, 5)
    return to_dbc.Device(
        "Drive",
        5,
        tpdos,
        rpdos,
        heartbeat=bool(to_dbc.configured_value(od, 0x1017)),
        emcy_cob_id=to_dbc.emcy_id(to_dbc.configured_value(od, 0x1014)),
    )


def by_id(made):
    return {m.frame_id: m for m in made.database.messages}


def test_the_demo_device_comes_out_as_its_hand_written_database_says(drive):
    """demo.dbc was written by hand for the same node: the PDO it describes
    must come out with the same bits, the same types and on the same id."""
    made = to_dbc.build([drive])
    mine = by_id(made)[0x185]
    theirs = cantools.database.load_file(str(resources.path("demo.dbc"))).get_message_by_frame_id(
        0x185
    )

    def layout(message):
        return sorted((s.start, s.length, s.is_signed) for s in message.signals)

    assert layout(mine) == layout(theirs)
    assert mine.length == theirs.length
    assert mine.senders == ["Drive"]


def test_each_node_has_its_heartbeat_and_emergency_and_the_network_its_own(drive):
    ids = set(by_id(to_dbc.build([drive])))
    assert {0x705, 0x085} <= ids, "heartbeat and EMCY of node 5"
    assert {0x000, 0x080, 0x100} <= ids, "NMT, SYNC and TIME"
    assert {0x185, 0x205} <= ids, "its TPDO and its RPDO"
    assert not {0x000, 0x080, 0x100} & set(by_id(to_dbc.build([drive], network=False)))


def test_what_is_written_reads_back_the_same(drive, tmp_path):
    """The check made on every save, and the reason this came first among
    the ways of writing a database: made from nothing, it can be compared."""
    made = to_dbc.build([drive])
    path = tmp_path / "network.dbc"
    assert to_dbc.write(made, path) == []
    back = cantools.database.load_file(str(path))
    assert {m.frame_id for m in back.messages} == set(by_id(made))
    assert b"\r\r\n" not in path.read_bytes(), "each line ended once, not CR CR LF"


def test_a_heartbeat_names_its_states(drive):
    heartbeat = by_id(to_dbc.build([drive]))[0x705]
    assert heartbeat.decode(bytes([5]))["NmtState"] == "Operational"


def test_an_eds_suggests_and_so_describes_nothing():
    """Its COB-IDs are the predefined set's suggestion, its mapping what the
    device ships with: neither is how a node on a network is set up."""
    od = canopen.import_od(str(resources.path("demo.eds")), 5)
    assert to_dbc.from_dictionary(od, 5) == ([], [])


def test_heartbeat_and_emergency_are_there_only_as_configured():
    """The heartbeat's identifier is fixed and the EMCY's can be anything;
    whether there is either is the node's configuration."""
    none = to_dbc.Device("A", 1)
    ids = set(by_id(to_dbc.build([none], network=False)))
    assert ids == set()
    moved = to_dbc.Device("A", 1, heartbeat=True, emcy_cob_id=0x0F1)
    assert set(by_id(to_dbc.build([moved], network=False))) == {0x701, 0x0F1}
    assert to_dbc.emcy_id(0x80000081) is None, "bit 31: emergencies switched off"


# --- PDOs built by hand, for the cases the demo has none of -----------------------------------
def entry(index, sub, bits, name, data_type=canopen.objectdictionary.UNSIGNED16):
    od = SimpleNamespace(index=index, subindex=sub, data_type=data_type, name=name)
    return SimpleNamespace(od=od, length=bits, name=name)


def pdo(cob_id, entries, name="TxPDO1_node1", enabled=True, trans_type=254, event_timer=None):
    return SimpleNamespace(
        cob_id=cob_id,
        enabled=enabled,
        map=entries,
        name=name,
        trans_type=trans_type,
        event_timer=event_timer,
    )


def test_a_dummy_entry_leaves_a_gap_and_is_not_a_signal():
    mapped = [entry(0x2000, 1, 8, "First"), entry(0x0005, 0, 8, "pad"), entry(0x2000, 2, 8, "Last")]
    device = to_dbc.Device("A", 1, [pdo(0x181, mapped)])
    message = by_id(to_dbc.build([device], network=False))[0x181]
    assert [(s.name, s.start) for s in message.signals] == [("First", 0), ("Last", 16)]
    assert message.length == 3


def test_scaling_unit_and_named_values_come_from_the_display():
    signed = canopen.objectdictionary.INTEGER16
    shown = Display(name="Current", unit="A", factor=0.1, choices={0: "Off"}, low=-100, high=100)
    device = to_dbc.Device(
        "A", 1, [pdo(0x181, [entry(0x2001, 0, 16, "I", signed)])], display=lambda i, s: shown
    )
    signal = by_id(to_dbc.build([device], network=False))[0x181].signals[0]
    assert (signal.name, signal.unit, signal.scale, signal.is_signed) == ("Current", "A", 0.1, True)
    assert (signal.minimum, signal.maximum) == (-10, 10), "the limits as physical values"
    assert dict(signal.choices) == {0: "Off"}


def test_one_nodes_tpdo_that_is_anothers_rpdo_is_one_frame_with_both_receiving():
    """A network maps a node's output straight into another's input: the same
    frame, sent once. Described twice, it would be two messages on one id."""
    sender = to_dbc.Device("Sensor", 1, [pdo(0x181, [entry(0x2000, 0, 16, "Level")])])
    taker = to_dbc.Device(
        "Pump", 2, rpdos=[pdo(0x181, [entry(0x2100, 0, 16, "Demand")], name="RxPDO1_node2")]
    )
    made = to_dbc.build([sender, taker], network=False)
    message = by_id(made)[0x181]
    assert message.senders == ["Sensor"]
    assert "Pump" in message.signals[0].receivers
    assert made.notes == []


def test_a_disabled_pdo_is_left_out_and_a_clash_is_said():
    device = to_dbc.Device(
        "A",
        1,
        [
            pdo(0x181, [entry(0x2000, 0, 8, "Off")], enabled=False),
            pdo(0x281, [entry(0x2001, 0, 8, "One")], name="TxPDO2_node1"),
        ],
    )
    other = to_dbc.Device("B", 2, [pdo(0x281, [entry(0x2002, 0, 8, "Two")], name="TxPDO1_node2")])
    made = to_dbc.build([device, other], network=False)
    assert 0x181 not in by_id(made)
    assert by_id(made)[0x281].senders == ["A"], "the first one stays"
    assert len(made.notes) == 1


def test_the_cycle_is_the_event_timer_and_a_sync_pdo_has_none():
    timed = pdo(0x181, [entry(0x2000, 0, 8, "T")], event_timer=50)
    synced = pdo(0x281, [entry(0x2001, 0, 8, "S")], name="TxPDO2_node1", trans_type=1)
    made = to_dbc.build([to_dbc.Device("A", 1, [timed, synced])], network=False)
    assert by_id(made)[0x181].cycle_time == 50
    assert by_id(made)[0x281].cycle_time is None


def test_names_are_made_into_ones_a_dbc_takes():
    assert to_dbc.dbc_name("Motor speed (rpm)") == "Motor_speed_rpm"
    assert to_dbc.dbc_name("1st value") == "_1st_value"
    assert to_dbc.dbc_name("***") == "Signal"
    taken: set[str] = set()
    assert [to_dbc._unique("A", taken) for _ in range(3)] == ["A", "A_2", "A_3"]


# --- from the CANopen pane --------------------------------------------------------------------
@pytest.fixture
def window(app, tmp_path, monkeypatch):
    from pycangui.ui.main_window import MainWindow

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    win = MainWindow()
    yield win
    win.close()


def test_the_network_is_saved_from_a_file_in_the_list(window, tmp_path, monkeypatch):
    from pycangui.ui import folders

    view = window.canopen_view
    view.open_file(demo_dcf(tmp_path))
    view.open_file(resources.path("demo.eds"))  # and one that says nothing of a network
    target = tmp_path / "out.dbc"
    monkeypatch.setattr(folders, "save_file", lambda *a, **k: str(target))
    view.network_dbc_action.trigger()
    back = cantools.database.load_file(str(target))
    ids = {m.frame_id for m in back.messages}
    assert {0x000, 0x080, 0x100} <= ids
    assert back.get_message_by_frame_id(0x185).senders == ["drive"], "named after its file"
    assert {0x705, 0x085} <= ids, "node 5's heartbeat and emergency, from the DCF"
    assert {node.name for node in back.nodes} == {"Tester", "drive"}, "the EDS left out"


def test_an_eds_cannot_be_saved_as_dbc_on_its_own(window, tmp_path):
    view = window.canopen_view
    eds = view.open_file(resources.path("demo.eds"))
    dcf = view.open_file(demo_dcf(tmp_path))

    def offered(source):
        menu = view.file_menu(source)
        return next(a for a in menu.actions() if a.text() == "Save as DBC...").isEnabled()

    assert not offered(eds) and offered(dcf)


def test_a_node_whose_configuration_was_not_read_is_said_not_saved(window, monkeypatch):
    from pycangui.ui import canopen_dbc, messages

    said = []
    monkeypatch.setattr(messages, "warning", lambda *a, **k: said.append(a))
    canopen_dbc.save(window.canopen_view, [5], [], False)
    assert said, "a box saying why, and no file with nothing in it"


def test_one_node_is_on_its_right_click(window):
    menu = window.canopen_view.node_menu(5)
    action = next(a for a in menu.actions() if a.text() == "Save as DBC...")
    assert action.isEnabled(), "not asked of the node, so there whether it answers or not"
    nothing = window.canopen_view.node_menu(None)
    assert not next(a for a in nothing.actions() if a.text() == "Save as DBC...").isEnabled()


# --- a live node --------------------------------------------------------------------------------
def wait_until(pred, timeout=5.0):
    import time

    from PySide6.QtCore import QCoreApplication

    deadline = time.monotonic() + timeout
    while not pred():
        QCoreApplication.processEvents()
        if time.monotonic() > deadline and not pred():
            raise AssertionError("timed out")
        time.sleep(0.005)


def test_a_live_node_is_described_by_the_mapping_read_from_it(
    app, demo_device, tmp_path, monkeypatch
):
    """The configuration the pane has for it: read from the node once its EDS
    is loaded, which is what is decoded and sent by."""
    from pycangui.canopen.manager import CanopenManager
    from pycangui.core.bus import BusManager
    from pycangui.ui import canopen_dbc

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    bus = BusManager()
    manager = CanopenManager(bus)
    bus.connect_bus("virtual", "vcan_to_dbc", 500000, False)
    try:
        demo_device(bus, kinds=["canopen_device"])
        seen, loaded = [], []
        manager.node_seen.connect(lambda n, state: seen.append(n))
        manager.eds_loaded.connect(lambda n, p, name: loaded.append(n))
        wait_until(lambda: seen)
        manager.load_eds(5, str(resources.path("demo.eds")))
        wait_until(lambda: loaded)
        # Loading the EDS starts a read of both directions by itself. Let it
        # finish first: on a slow machine its signal came while the read
        # asked for below was still rebuilding the RPDO maps, and the
        # database was made without them.
        wait_until(lambda: manager.read_from_node(5) == (True, True), timeout=15.0)
        manager.read_pdo_config(5)  # Read PDO config: both directions, from the node
        # Until the worker has got through it, rather than until the next
        # rpdos_read: that could be the first read's, arriving while this one
        # was still rebuilding the RPDO maps.
        done = []
        manager.background(lambda: None, lambda _result, _error: done.append(True))
        wait_until(lambda: done, timeout=15.0)

        view = SimpleNamespace(manager=manager, _node_item=lambda node_id: None)
        notes: list[str] = []
        device = canopen_dbc.node_device(view, 5, set(), notes)
        made = to_dbc.build([device], network=False)
    finally:
        bus.disconnect_bus()
        manager.shutdown()

    assert notes == []
    assert device.name == "Node5", "named by its number where the list has no name"
    assert by_id(made)[0x185].senders == ["Node5"]
    assert {0x185, 0x205, 0x705} <= set(by_id(made)), "TPDO, RPDO and the heartbeat heard"


def test_the_dbc_menu_saves_the_selected_dcf_and_offers_no_eds(window, tmp_path, monkeypatch):
    """DBC > Save ... as DBC is the selected row alone: a DCF, named in the
    entry, or a node; never an EDS, which says how a device ships, not how
    a node is set up."""
    from pycangui.ui import folders

    view = window.canopen_view
    eds = view.open_file(resources.path("demo.eds"))
    dcf = view.open_file(demo_dcf(tmp_path))

    view.nodes.setCurrentItem(view._file_item(eds))
    assert not view.selected_dbc_action.isEnabled()

    view.nodes.setCurrentItem(view._file_item(dcf))
    assert view.selected_dbc_action.isEnabled()
    target = tmp_path / "one.dbc"
    monkeypatch.setattr(folders, "save_file", lambda *a, **k: str(target))
    view.selected_dbc_action.trigger()
    back = cantools.database.load_file(str(target))
    assert back.get_message_by_frame_id(0x185).senders == ["drive"]
    assert 0x000 not in {m.frame_id for m in back.messages}, "one node, so no NMT"
