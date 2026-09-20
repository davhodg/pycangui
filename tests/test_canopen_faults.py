# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""What a node says about its own faults, asked rather than heard.

An EMCY reaches whoever was listening at the time. Everything here is about
the other case -- plugging in after a controller has faulted -- which is
answered by reading three objects off the node, two of which are optional in
CiA 301. Most of these tests are about that word: a node with no 0x1003 has
to read differently from a node with an empty one.
"""

import time

import pytest
from PySide6.QtCore import QCoreApplication

from pycangui import resources
from pycangui.canopen import faults
from pycangui.canopen.manager import CanopenManager
from pycangui.core.bus import BusManager
from pycangui.nodes import canopen_device

OVER_CURRENT = 0x2310


def wait_until(pred, timeout=8.0):
    deadline = time.monotonic() + timeout
    while not pred():
        QCoreApplication.processEvents()
        if time.monotonic() > deadline:
            raise AssertionError("timed out")
        time.sleep(0.005)


# --- the entries, without a bus ---------------------------------------------------------
def test_an_entry_is_a_code_and_the_makers_own_word():
    """CiA 301 puts the emergency code in the low word and leaves the high
    word to the maker, who usually puts a sub-code in it."""
    error = faults.unpack(0x00072310)
    assert error.code == 0x2310
    assert error.info == 0x0007
    assert "Continuous over current" in error.description
    assert "0x0007" in str(error)


def test_an_entry_with_nothing_in_the_high_word_does_not_invent_one():
    assert faults.unpack(0x00002310).info == 0
    assert "manufacturer" not in str(faults.unpack(0x00002310))


def test_a_node_with_no_fault_and_a_node_that_cannot_say_are_different(app):
    """Saying 0 for both would be inventing a reading."""
    quiet = faults.FaultState(1, register=0, manufacturer_status=0)
    assert not quiet.faulted
    assert quiet.says(faults.MANUFACTURER_STATUS)

    silent = faults.FaultState(2, register=0, missing={faults.MANUFACTURER_STATUS})
    assert silent.manufacturer_status is None
    assert not silent.says(faults.MANUFACTURER_STATUS)
    assert "0x1002" in faults.missing_text(faults.MANUFACTURER_STATUS)


def test_a_stored_error_is_history_rather_than_a_fault_now():
    """A node that faulted this morning and recovered still holds the entry.
    Calling that a fault would report the past as the present."""
    state = faults.FaultState(1, register=0, stored=[faults.unpack(OVER_CURRENT)])
    assert state.stored and not state.faulted

    state.register = 0x02
    assert state.faulted and "current" in state.register_text


# --- against the demo device ------------------------------------------------------------
@pytest.fixture
def stack(app, tmp_path, monkeypatch, demo_device):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    bus = BusManager()
    manager = CanopenManager(bus)
    bus.connect_bus("virtual", "vcan_faults", 500000, False)
    demo = demo_device(bus, kinds=["canopen_device"])
    manager.load_eds(5, str(resources.path("demo.eds")))
    wait_until(lambda: manager.node(5) is not None and len(manager.node(5).object_dictionary))
    yield manager, demo
    manager.shutdown()
    bus.disconnect_bus()


def read(manager, node_id=5, stored=True):
    seen: list = []
    manager.fault_state.connect(seen.append)
    manager.read_faults(node_id, stored=stored)
    wait_until(lambda: seen)
    manager.fault_state.disconnect(seen.append)
    return seen[-1]


def test_a_healthy_node_says_it_is_not_faulted(stack):
    manager, _demo = stack
    state = read(manager)
    assert state.register == 0
    assert not state.faulted


def test_an_object_the_node_has_not_got_is_an_answer_of_its_own(stack):
    """The demo device has no 0x1002, as plenty of real ones do not. That
    has to read differently from one reporting zero."""
    manager, _demo = stack
    state = read(manager)
    assert faults.MANUFACTURER_STATUS in state.missing
    assert state.manufacturer_status is None
    assert faults.ERROR_REGISTER not in state.missing, "0x1001 is mandatory and it has one"


def test_a_fault_is_found_by_asking_afterwards(stack):
    """The point of the whole thing: nobody was listening when it happened."""
    manager, demo = stack
    device = demo["canopen_device"].state.device
    device.set_data(canopen_device.ERROR_REGISTER, 0, bytes([0x02]))
    canopen_device._remember(device, OVER_CURRENT, 0x0005)

    state = read(manager)

    assert state.faulted and "current" in state.register_text
    assert [e.code for e in state.stored] == [OVER_CURRENT]
    assert state.stored[0].info == 0x0005, "the maker's own word came back with it"


def test_the_newest_stored_error_is_first(stack):
    manager, demo = stack
    device = demo["canopen_device"].state.device
    canopen_device._remember(device, 0x3210, 0)  # DC link over voltage, first
    canopen_device._remember(device, OVER_CURRENT, 0)  # then this

    state = read(manager)

    assert [e.code for e in state.stored] == [OVER_CURRENT, 0x3210]


def test_clearing_empties_the_nodes_list_and_not_the_tools(stack):
    """What pycangui saw and what the node kept are two different records."""
    manager, demo = stack
    device = demo["canopen_device"].state.device
    canopen_device._remember(device, OVER_CURRENT, 0)
    manager.emcy_history.append(object())  # stands for what the tool saw

    manager.clear_stored_errors(5)
    wait_until(lambda: not read(manager).stored)

    assert manager.emcy_history, "the Emergencies list is untouched"


def test_asking_only_whether_it_is_faulted_skips_the_list(stack):
    """An SDO per entry is a real cost, and a history does not change while
    nothing is happening."""
    manager, demo = stack
    device = demo["canopen_device"].state.device
    canopen_device._remember(device, OVER_CURRENT, 0)

    state = read(manager, stored=False)

    assert state.register is not None
    assert state.stored == []


# --- the pane -----------------------------------------------------------------------
@pytest.fixture
def window(app, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings

    from pycangui.ui.main_window import MainWindow

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    win = MainWindow()
    monkeypatch.setattr(win.canopen, "identify", lambda _node_id: None)
    yield win
    win.close()


def test_the_tab_says_nothing_until_a_node_is_chosen(app, window):
    view = window.canopen_view.faults
    assert not view.read_btn.isEnabled()
    assert "No node" in view.state.text()


def test_the_tab_follows_the_node_list(app, window):
    from pycangui.canopen.manager import ADDED_BY_HAND

    window.canopen.node_seen.emit(7, ADDED_BY_HAND)
    view = window.canopen_view
    view.nodes.setCurrentItem(view._node_item(7))

    assert view.faults._node == 7
    assert view.faults.read_btn.isEnabled()
    assert "Nothing read" in view.faults.state.text(), "and it does not read by itself"


def show(window, state):
    """Hand the pane a reading, the way the manager's signal does."""
    window.canopen.fault_states[state.node_id] = state
    window.canopen.fault_state.emit(state)


def test_a_node_with_no_stored_list_says_so_instead_of_showing_an_empty_one(app, window):
    from pycangui.canopen.manager import ADDED_BY_HAND

    window.canopen.node_seen.emit(7, ADDED_BY_HAND)
    view = window.canopen_view
    view.nodes.setCurrentItem(view._node_item(7))

    show(window, faults.FaultState(7, register=0, missing={faults.PREDEFINED_ERROR_FIELD}))

    assert "no stored error list" in view.faults.note.text()
    assert not view.faults.clear_btn.isEnabled(), "nothing to clear"
    assert view.faults.stored.topLevelItemCount() == 0


def test_a_node_with_an_empty_list_reads_differently(app, window):
    from pycangui.canopen.manager import ADDED_BY_HAND

    window.canopen.node_seen.emit(7, ADDED_BY_HAND)
    view = window.canopen_view
    view.nodes.setCurrentItem(view._node_item(7))

    show(window, faults.FaultState(7, register=0, stored=[]))

    assert "holding no stored errors" in view.faults.note.text()
    assert view.faults.clear_btn.isEnabled()


def test_a_fault_shows_in_the_node_list_and_goes_when_it_clears(app, window):
    from pycangui.canopen.manager import ADDED_BY_HAND
    from pycangui.ui.canopen_view import COL_ERROR

    window.canopen.node_seen.emit(7, ADDED_BY_HAND)
    view = window.canopen_view

    show(window, faults.FaultState(7, register=0x02))
    assert "current" in view._node_item(7).text(COL_ERROR)

    show(window, faults.FaultState(7, register=0x00))
    assert view._node_item(7).text(COL_ERROR) == "", "not faulted now, so nothing to say"


def test_what_was_read_is_still_there_when_the_node_is_chosen_again(app, window):
    """A fault state is state rather than an event."""
    from pycangui.canopen.manager import ADDED_BY_HAND

    for node_id in (7, 8):
        window.canopen.node_seen.emit(node_id, ADDED_BY_HAND)
    view = window.canopen_view
    view.nodes.setCurrentItem(view._node_item(7))
    show(window, faults.FaultState(7, register=0x02, stored=[faults.unpack(OVER_CURRENT)]))

    view.nodes.setCurrentItem(view._node_item(8))
    assert "Nothing read" in view.faults.state.text()

    view.nodes.setCurrentItem(view._node_item(7))
    assert "Faulted" in view.faults.state.text()
    assert view.faults.stored.topLevelItemCount() == 1
