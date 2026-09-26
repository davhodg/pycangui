# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Mapping an object into a PDO: the picker, and what it says an object is.

Reported: *Map object...* threw before the dialog appeared. It asked an
ODVariable for ``bit_length``, which the ``canopen`` package has never had
-- so the whole of PDO mapping was unreachable while reading a node's
existing configuration worked perfectly, which is why it looked like a
mapping problem rather than a missing attribute.

Nothing here had a test. These cover the picker end to end, because the
crash was in building its list and no test had ever built one.
"""

import time

import pytest
from PySide6.QtCore import QCoreApplication

from pycangui import resources
from pycangui.canopen.manager import CanopenManager, mappable, mapped_bits
from pycangui.core.bus import BusManager
from pycangui.ui.pdo_view import ObjectPicker

NODE = 5


def wait_until(pred, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not pred():
        QCoreApplication.processEvents()
        if time.monotonic() > deadline:
            raise AssertionError("timed out")
        time.sleep(0.005)


def settle(manager):
    """Wait for the manager's worker to get through its queue, which it does
    in order.

    Loading an EDS also queues a read of the node's TPDOs over SDO, which
    empties each mapping and refills it one entry at a time. Until it is
    done, TPDO1 can be caught mapping nothing. That read is queued when the
    EDS has loaded, so wait for ``eds_loaded`` first, or this waits for less.
    """
    settled = []
    manager.background(lambda: None, lambda _result, _error: settled.append(True))
    wait_until(lambda: settled)


@pytest.fixture
def on_the_bus(app, demo_device, tmp_path, monkeypatch):
    """The demo node and a manager on one bus, the node's EDS not loaded yet."""
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    bus = BusManager()
    out = CanopenManager(bus)
    bus.connect_bus("virtual", "vcan_pdo_map", 500000, False)
    demo = demo_device(bus, kinds=["canopen_device"])
    yield out, demo
    bus.disconnect_bus()
    out.shutdown()


@pytest.fixture
def manager(on_the_bus):
    """A node with the demo EDS on it, which is what the picker reads."""
    out, _demo = on_the_bus
    seen, loaded = [], []
    out.node_seen.connect(lambda n, state: seen.append(n))
    out.eds_loaded.connect(lambda n, p, name: loaded.append(n))
    wait_until(lambda: seen)  # its heartbeat
    out.load_eds(NODE, str(resources.path("demo.eds")))
    wait_until(lambda: loaded)
    settle(out)
    return out


def variable(data_type: int):
    from canopen.objectdictionary import ODVariable

    var = ODVariable("x", 0x2000, 0)
    var.data_type = data_type
    return var


# --- how long an object is ---------------------------------------------------------
def test_the_bit_length_comes_from_the_data_type():
    from canopen.objectdictionary import datatypes

    assert mapped_bits(variable(datatypes.UNSIGNED8)) == 8
    assert mapped_bits(variable(datatypes.INTEGER16)) == 16
    assert mapped_bits(variable(datatypes.UNSIGNED32)) == 32
    assert mapped_bits(variable(datatypes.REAL64)) == 64


def test_an_object_with_no_length_known_in_advance_cannot_be_mapped():
    """A PDO is eight bytes with a layout agreed beforehand, so a string
    has no length to write into a mapping entry."""
    from canopen.objectdictionary import datatypes

    assert mappable(variable(datatypes.UNSIGNED16))
    assert not mappable(variable(datatypes.VISIBLE_STRING))
    assert not mappable(variable(datatypes.DOMAIN))
    assert not mappable(None)


# --- the picker ---------------------------------------------------------------------
def test_the_picker_opens_and_lists_the_nodes_objects(app, manager):
    """Reported as a crash before the dialog appeared."""
    picker = ObjectPicker(manager, NODE)
    assert picker.entries, "nothing to map, on a node with mappable objects"
    assert picker.list.count() == len(picker.entries)


def test_it_offers_the_manufacturer_and_profile_objects_with_their_widths(app, manager):
    picker = ObjectPicker(manager, NODE)
    found = {(e.index, e.subindex): e for e in picker.entries}
    assert found[(0x2000, 1)].bits == 16, "Motor speed is an INTEGER16"
    assert found[(0x2000, 2)].bits == 32, "Odometer is an UNSIGNED32"
    assert found[(0x6040, 0)].bits == 16, "Controlword is an UNSIGNED16"


def test_it_leaves_out_the_communication_profile(app, manager):
    """0x1000 to 0x1FFF is how the node is configured, not what it measures."""
    picker = ObjectPicker(manager, NODE)
    assert not [e for e in picker.entries if e.index < 0x2000]


def test_it_leaves_out_the_container_row_of_a_record(app, manager):
    """An array's own row names the group; only its subs hold values."""
    picker = ObjectPicker(manager, NODE)
    rows = [e for e in picker.entries if e.index == 0x2000]
    assert rows, "the record vanished altogether"
    assert all(e.subindex != 0 or e.bits for e in rows)
    assert (0x2000, 1) in {(e.index, e.subindex) for e in rows}


def test_choosing_one_gives_back_what_it_showed(app, manager):
    picker = ObjectPicker(manager, NODE)
    wanted = next(i for i, e in enumerate(picker.entries) if (e.index, e.subindex) == (0x2000, 1))
    picker.list.setCurrentRow(wanted)
    chosen = picker.chosen()
    assert (chosen.index, chosen.subindex, chosen.bits) == (0x2000, 1, 16)


def test_nothing_chosen_is_not_an_object(app, manager):
    picker = ObjectPicker(manager, NODE)
    picker.list.setCurrentRow(-1)
    assert picker.chosen() is None


def test_the_filter_box_narrows_the_list(app, manager):
    picker = ObjectPicker(manager, NODE)
    picker.search.setText("odometer")
    shown = [i for i in range(picker.list.count()) if not picker.list.item(i).isHidden()]
    assert len(shown) == 1
    assert "Odometer" in picker.list.item(shown[0]).text()


def test_a_node_with_no_eds_offers_nothing_rather_than_throwing(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    bus = BusManager()
    empty = CanopenManager(bus)
    picker = ObjectPicker(empty, 99)
    assert picker.entries == []
    bus.disconnect_bus()


# --- the edit survives the redraw ---------------------------------------------------
@pytest.fixture
def view(app, manager):
    from pycangui.core.context import Context
    from pycangui.ui.pdo_view import PdoConfigView

    out = PdoConfigView(manager, Context(log=print))
    out.set_node(NODE)
    assert out._configs, "no PDOs to edit, so nothing below tests anything"
    return out


def first_pdo(view):
    """The top-level row for the first PDO, selected."""
    item = view.tree.topLevelItem(0)
    view.tree.setCurrentItem(item)
    return item


def test_unmapping_an_object_takes_it_out_and_keeps_it_out(view):
    """Reported. The edit was made to a PdoConfig the redraw then threw
    away, because redrawing asked the manager for the node's own
    configuration again."""
    item = first_pdo(view)
    config = view._config_of(item)
    if not config.entries:
        pytest.skip("this PDO maps nothing to start with")
    before = list(config.entries)

    view.tree.setCurrentItem(item.child(0))
    view._remove_entry()

    assert view._config_of(view.tree.topLevelItem(0)).entries == before[1:]
    assert view.tree.topLevelItem(0).childCount() == len(before) - 1


def test_mapping_an_object_keeps_it_too(view, monkeypatch):
    """The same bug from the other side: Map object... appended to a
    throwaway copy."""
    from pycangui.canopen import PdoEntry
    from pycangui.ui import pdo_view

    view._config_of(view.tree.topLevelItem(0)).entries.clear()
    view._redraw()
    first_pdo(view)

    wanted = PdoEntry(0x2001, 0, 16, "Speed demand")
    monkeypatch.setattr(pdo_view.ObjectPicker, "exec", lambda self: pdo_view.QDialog.Accepted)
    monkeypatch.setattr(pdo_view.ObjectPicker, "chosen", lambda self: wanted)
    view._add_entry()

    assert view._config_of(view.tree.topLevelItem(0)).entries == [wanted]
    assert view.tree.topLevelItem(0).childCount() == 1


def test_reading_from_the_node_does_throw_the_edit_away(view):
    """The one thing that should: Read from node means what the node says."""
    view._config_of(view.tree.topLevelItem(0)).entries.clear()
    view._redraw()
    assert view.tree.topLevelItem(0).childCount() == 0

    view.refresh()  # what Read from node does when the answer arrives
    assert view._configs[0].entries, "the node's own mapping did not come back"


def test_the_pane_shows_what_the_node_maps_not_only_what_its_eds_says(app, on_the_bus):
    """Loading an EDS fills the pane with the mapping the file declares, then
    asks the node what it really maps. The pane was never told the answer,
    so a node remapped since it left the factory went on showing the EDS
    mapping -- or, open at the wrong moment, a PDO mapping nothing."""
    from pycangui.core.context import Context
    from pycangui.ui.pdo_view import PdoConfigView

    manager, demo = on_the_bus
    device = demo["canopen_device"].state.device
    device.set_data(0x1A00, 0, b"\x01")  # TPDO1 cut down to its first object
    view = PdoConfigView(manager, Context(log=print))
    view.set_node(NODE)

    loaded = []
    manager.eds_loaded.connect(lambda n, p, name: loaded.append(n))
    manager.load_eds(NODE, str(resources.path("demo.eds")))
    wait_until(lambda: loaded)
    settle(manager)

    tpdo1 = next(c for c in view._configs if (c.direction, c.number) == ("TPDO", 1))
    assert [e.name for e in tpdo1.entries] == ["Statusword"]
    assert view.tree.topLevelItem(0).childCount() == 1


# --- saying why an object did not go in ------------------------------------------------
def test_a_full_pdo_says_so_in_a_box_not_only_in_the_log(view, monkeypatch):
    """A line in a pane somebody may not have open is no answer to a button
    they just pressed."""
    from pycangui.canopen import PdoEntry
    from pycangui.ui import pdo_view

    config = view._config_of(view.tree.topLevelItem(0))
    config.entries.clear()
    config.entries.append(PdoEntry(0x2000, 2, 64, "Odometer"))  # full
    view._redraw()
    first_pdo(view)

    said = []
    monkeypatch.setattr(pdo_view.messages, "warning", lambda *a, **k: said.append(a))
    monkeypatch.setattr(pdo_view.ObjectPicker, "exec", lambda self: pdo_view.QDialog.Accepted)
    monkeypatch.setattr(
        pdo_view.ObjectPicker, "chosen", lambda self: PdoEntry(0x2001, 0, 16, "Speed demand")
    )
    view._add_entry()

    assert said, "only the Event Log was told"
    title, text = said[0][1], said[0][2]
    assert "full" in title.lower()
    assert "64" in text and "16" in text, "does not say how full, or by how much"
    assert len(view._config_of(view.tree.topLevelItem(0)).entries) == 1


def test_unmapping_with_a_pdo_selected_says_what_to_select(view, monkeypatch):
    from pycangui.ui import pdo_view

    first_pdo(view)  # the PDO itself, not one of its objects
    said = []
    monkeypatch.setattr(pdo_view.messages, "warning", lambda *a, **k: said.append(a))
    view._remove_entry()
    assert said, "nothing happened and nothing was said"


# --- what the identifier means, and what it does not -----------------------------------
def test_the_pdo_row_is_not_labelled_from_its_identifier(view):
    """Reported: a TPDO at 0x151 was shown as node 81's RPDO, because that
    is what the predefined connection set gives the number to."""
    for row in range(view.tree.topLevelItemCount()):
        text = view.tree.topLevelItem(row).text(0)
        assert "node" not in text.lower(), f"a guessed name is back: {text!r}"
        assert text.strip() == text.strip().split()[0], f"more than the PDO's name: {text!r}"


def test_the_identifier_offers_its_convention_as_a_tooltip(view):
    from pycangui.ui.pdo_view import COL_COBID

    tip = view.tree.topLevelItem(0).toolTip(COL_COBID)
    assert "CiA 301" in tip
    assert "not what this PDO carries" in tip or "not one of the identifiers" in tip


def test_the_selection_survives_a_redraw(view):
    """Unmapping one object should not mean hunting for the PDO again to
    unmap the next."""
    item = first_pdo(view)
    if item.childCount() < 2:
        pytest.skip("this PDO maps fewer than two objects")
    view.tree.setCurrentItem(item.child(0))
    view._remove_entry()

    chosen = view.tree.selectedItems()
    assert chosen, "nothing is selected any more"
    assert chosen[0].parent() is view.tree.topLevelItem(0), "selection moved to another PDO"
