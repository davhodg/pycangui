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
from pycangui.core.context import Context
from pycangui.core.hooks import Hooks
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
    ctx = Context(log=print)
    manager = CanopenManager(bus, Hooks(ctx))
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


# --- the emergencies tab: grouped, and what is still wrong ---------------------------
def emcy(node_id, code, register=0x01):
    from pycangui.canopen.emcy import Emcy

    return Emcy(node_id=node_id, code=code, register=register, timestamp=1.0)


def node_rows(view):
    tree = view.emcy
    return {
        tree.topLevelItem(i).text(0): tree.topLevelItem(i) for i in range(tree.topLevelItemCount())
    }


def test_emergencies_are_grouped_under_the_node_that_sent_them(app, window):
    view = window.canopen_view
    for one in (emcy(1, 0x2310), emcy(1, 0x3210), emcy(5, 0x4210)):
        view.on_emcy(one)

    rows = node_rows(view)
    assert set(rows) == {"Node 1", "Node 5"}
    assert rows["Node 1"].childCount() == 2
    assert rows["Node 5"].childCount() == 1


def test_a_node_row_says_how_much_is_still_wrong(app, window):
    from pycangui.ui.canopen_view import COL_EMCY_DESCRIPTION

    view = window.canopen_view
    view.on_emcy(emcy(1, 0x2310))
    view.on_emcy(emcy(1, 0x3210))
    assert node_rows(view)["Node 1"].text(COL_EMCY_DESCRIPTION) == "2 active"

    view.on_emcy(emcy(1, 0x0000))
    assert node_rows(view)["Node 1"].text(COL_EMCY_DESCRIPTION) == "all clear"


def test_a_reset_clears_that_node_and_leaves_the_others(app, window):
    from pycangui.canopen.emcy import ACTIVE, CLEARED
    from pycangui.ui.canopen_view import COL_EMCY_STATE

    view = window.canopen_view
    view.on_emcy(emcy(1, 0x2310))
    view.on_emcy(emcy(5, 0x4210))
    view.on_emcy(emcy(1, 0x0000))

    rows = node_rows(view)
    assert rows["Node 1"].child(0).text(COL_EMCY_STATE) == CLEARED
    assert rows["Node 5"].child(0).text(COL_EMCY_STATE) == ACTIVE, "a different machine"


def test_the_oldest_arrivals_go_first_whichever_node_sent_them(app, window, monkeypatch):
    """Grouping is how it is shown; arrival order is what says which to
    drop. A node whose last one goes takes its row with it."""
    from pycangui.ui import canopen_view as view_mod

    view = window.canopen_view
    monkeypatch.setattr(view_mod, "MOST_EMERGENCIES", 3)
    for one in (emcy(1, 0x2310), emcy(5, 0x4210), emcy(1, 0x3210), emcy(1, 0x4310)):
        view.on_emcy(one)

    rows = node_rows(view)
    assert rows["Node 1"].childCount() == 2, "its oldest went, being the oldest of all"
    assert rows["Node 5"].childCount() == 1, "newer than that one, so it stays"

    monkeypatch.setattr(view_mod, "MOST_EMERGENCIES", 2)
    view.on_emcy(emcy(1, 0x5000))

    rows = node_rows(view)
    assert "Node 5" not in rows, "its only one went, and the row went with it"
    assert rows["Node 1"].childCount() == 2


def test_saving_writes_what_arrived_and_what_it_is_now(app, window, tmp_path, monkeypatch):
    import csv

    from pycangui.canopen.emcy import ACTIVE, CLEARED, RESET
    from pycangui.ui import folders

    window.canopen.emcy_history.extend([emcy(1, 0x2310), emcy(5, 0x4210), emcy(1, 0x0000)])
    out = tmp_path / "emergencies.csv"
    monkeypatch.setattr(folders, "save_file", lambda *_a, **_k: str(out))

    window.canopen_view._save_emergencies()

    rows = list(csv.reader(out.read_text(encoding="utf-8").splitlines()))
    assert rows[0][:5] == ["Time", "Node", "Code", "Description", "State"]
    assert [r[4] for r in rows[1:]] == [CLEARED, ACTIVE, RESET]


def test_saving_nothing_says_so_rather_than_writing_an_empty_file(app, window, monkeypatch):
    from pycangui.ui import folders

    asked: list = []
    monkeypatch.setattr(folders, "save_file", lambda *a, **k: asked.append(a))
    window.canopen_view._save_emergencies()
    assert not asked, "no dialog for a file with nothing in it"


# --- a device that keeps its faults somewhere of its own -----------------------------
def hooked(manager, monkeypatch, **answers):
    """Stand in for a workspace hook file answering for this device."""
    real = manager._hooks.call

    def call(module, name, *args, **kwargs):
        if module == "canopen" and name in answers:
            return answers[name](*args)
        return real(module, name, *args, **kwargs)

    monkeypatch.setattr(manager._hooks, "call", call)


def test_a_hook_can_read_the_fault_list_its_own_way(stack, monkeypatch):
    """CiA 301 keeps them in 0x1003 and plenty of makers do not."""
    manager, _demo = stack
    hooked(manager, monkeypatch, stored_errors=lambda _node: [0x00070041, 0x00000065])

    state = read(manager)

    assert [e.code for e in state.stored] == [0x0041, 0x0065]
    assert state.stored[0].info == 0x0007, "read the way a 0x1003 entry is read"
    assert faults.PREDEFINED_ERROR_FIELD not in state.missing, "it answered, so it is not missing"


def test_a_hook_can_name_the_codes_the_standard_does_not_know(stack, monkeypatch):
    """A device using its own numbering gets the wrong name out of the CiA
    table, or none at all."""
    manager, _demo = stack
    hooked(
        manager,
        monkeypatch,
        stored_errors=lambda _node: [faults.StoredError(0x0041, text="Encoder fault")],
    )

    state = read(manager)

    assert state.stored[0].description == "Encoder fault"
    assert "Encoder fault" in str(state.stored[0])


def test_a_hook_saying_none_stored_is_not_the_same_as_no_hook(stack, monkeypatch):
    """An empty list is an answer: this device has no faults kept."""
    manager, demo = stack
    device = demo["canopen_device"].state.device
    canopen_device._remember(device, OVER_CURRENT, 0)  # 0x1003 has one in it
    hooked(manager, monkeypatch, stored_errors=lambda _node: [])

    state = read(manager)

    assert state.stored == [], "the hook answered, so 0x1003 was not read"
    assert faults.PREDEFINED_ERROR_FIELD not in state.missing


def test_without_a_hook_the_standard_object_is_still_read(stack, monkeypatch):
    manager, demo = stack
    device = demo["canopen_device"].state.device
    canopen_device._remember(device, OVER_CURRENT, 0)
    hooked(manager, monkeypatch, stored_errors=lambda _node: None)

    assert [e.code for e in read(manager).stored] == [OVER_CURRENT]


def test_a_hook_can_clear_the_list_its_own_way(stack, monkeypatch):
    """A device with its own list is cleared its own way, and writing to a
    0x1003 it may not have would be a write to the wrong place."""
    manager, demo = stack
    device = demo["canopen_device"].state.device
    canopen_device._remember(device, OVER_CURRENT, 0)
    cleared: list = []
    hooked(manager, monkeypatch, clear_stored_errors=lambda _node: cleared.append(True) or True)

    manager.clear_stored_errors(5)
    wait_until(lambda: cleared)

    assert int.from_bytes(device.get_data(0x1003, 0), "little") == 1, "0x1003 untouched"


def test_without_a_hook_clearing_writes_to_the_standard_object(stack, monkeypatch):
    manager, demo = stack
    device = demo["canopen_device"].state.device
    canopen_device._remember(device, OVER_CURRENT, 0)
    hooked(manager, monkeypatch, clear_stored_errors=lambda _node: None)

    manager.clear_stored_errors(5)
    wait_until(lambda: int.from_bytes(device.get_data(0x1003, 0), "little") == 0)


# --- what is wrong now, for a device that can list it -------------------------------
def test_a_hook_can_say_what_is_active_where_the_standard_cannot(stack, monkeypatch):
    """CiA 301 has no object for it: 0x1001 gives categories rather than
    faults, and 0x1002 is a word meaning whatever the maker chose."""
    manager, _demo = stack
    hooked(
        manager,
        monkeypatch,
        active_faults=lambda _node: [faults.StoredError(1, text="Over temperature")],
    )

    state = read(manager)

    assert [e.description for e in state.active] == ["Over temperature"]
    assert state.faulted, "it listed one, so the node is faulted whatever 0x1001 says"
    assert state.active_text == "Over temperature"


def test_an_empty_list_is_a_device_saying_it_is_healthy(stack, monkeypatch):
    manager, demo = stack
    device = demo["canopen_device"].state.device
    device.set_data(canopen_device.ERROR_REGISTER, 0, bytes([0x02]))  # the register disagrees
    hooked(manager, monkeypatch, active_faults=lambda _node: [])

    state = read(manager)

    assert state.active == []
    assert not state.faulted, "the hook is the better answer, and it says nothing is active"


def test_without_the_hook_the_register_is_still_the_answer(stack, monkeypatch):
    manager, demo = stack
    device = demo["canopen_device"].state.device
    device.set_data(canopen_device.ERROR_REGISTER, 0, bytes([0x02]))
    hooked(manager, monkeypatch, active_faults=lambda _node: None)

    state = read(manager)

    assert state.active is None, "nobody said, which is not the same as nothing active"
    assert state.faulted and "current" in state.active_text


def test_the_pane_hides_the_list_for_a_device_that_cannot_say(app, window):
    """An empty box under a heading reads as "nothing is wrong", and on a
    device that cannot list its faults nobody made that claim."""
    from pycangui.canopen.manager import ADDED_BY_HAND

    window.canopen.node_seen.emit(7, ADDED_BY_HAND)
    view = window.canopen_view
    view.nodes.setCurrentItem(view._node_item(7))

    show(window, faults.FaultState(7, register=0x02))
    assert not view.faults.active.isVisibleTo(view.faults)
    assert not view.faults.active_note.isVisibleTo(view.faults)

    show(window, faults.FaultState(7, register=0x02, active=[faults.StoredError(1, text="Hot")]))
    assert view.faults.active.isVisibleTo(view.faults)
    assert view.faults.active.topLevelItem(0).text(1) == "Hot"

    show(window, faults.FaultState(7, register=0x00, active=[]))
    assert not view.faults.active.isVisibleTo(view.faults), "nothing to list"
    assert view.faults.active_note.isVisibleTo(view.faults), "but it said so"


def test_the_node_list_shows_which_fault_rather_than_which_category(app, window):
    from pycangui.canopen.manager import ADDED_BY_HAND
    from pycangui.ui.canopen_view import COL_ERROR

    window.canopen.node_seen.emit(7, ADDED_BY_HAND)
    view = window.canopen_view

    show(window, faults.FaultState(7, register=0x02, active=[faults.StoredError(1, text="Hot")]))
    assert view._node_item(7).text(COL_ERROR) == "Hot"

    show(window, faults.FaultState(7, register=0x02))
    assert "current" in view._node_item(7).text(COL_ERROR), "no hook, so the category"
