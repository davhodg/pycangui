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


@pytest.fixture
def manager(app, demo_device, tmp_path, monkeypatch):
    """A node with the demo EDS on it, which is what the picker reads."""
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    bus = BusManager()
    out = CanopenManager(bus)
    bus.connect_bus("virtual", "vcan_pdo_map", 500000, False)
    demo_device(bus, kinds=["canopen_device"])
    seen, loaded = [], []
    out.node_seen.connect(lambda n, state: seen.append(n))
    out.eds_loaded.connect(lambda n, p, name: loaded.append(n))
    wait_until(lambda: seen)  # its heartbeat
    out.load_eds(NODE, str(resources.path("demo.eds")))
    wait_until(lambda: loaded)
    yield out
    bus.disconnect_bus()
    out.shutdown()


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
