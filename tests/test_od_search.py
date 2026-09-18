# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Finding one object in a dictionary of fifteen hundred, and keeping the ones you use.

A real controller offers well over a thousand entries and any given job uses
eight of them. The search is how you find those eight; the watch list is how
you stop finding them again every morning.
"""

import pytest
from PySide6.QtCore import Qt

from pycangui.canopen import NodeIdentity
from pycangui.canopen.manager import CanopenManager
from pycangui.core.bus import BusManager
from pycangui.core.context import Context
from pycangui.core.hooks import Hooks
from pycangui.ui.canopen_view import COL_WATCH, ROLE_INDEX, ROLE_SUB, CanopenView


@pytest.fixture
def view(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    ctx = Context(log=print)
    bus = BusManager()
    manager = CanopenManager(bus)
    widget = CanopenView(manager, Hooks(ctx), ctx)
    yield widget
    bus.disconnect_bus()


def load_demo(view, node_id=5):
    """The shipped demo EDS, which is a real dictionary with records in it."""
    from pycangui import resources

    path = str(__import__("pathlib").Path(resources.__file__).parent / "demo.eds")
    import canopen

    node = canopen.RemoteNode(node_id, path)
    view.manager._nodes = getattr(view.manager, "_nodes", {})
    view._identities[node_id] = NodeIdentity(node_id, vendor_id=1, product_code=2, revision=3)
    return node, path


def rows(view):
    """(index, sub, hidden) for every row in the tree, parents and children."""
    out = []
    for i in range(view.od.topLevelItemCount()):
        top = view.od.topLevelItem(i)
        out.append((top.data(0, ROLE_INDEX), top.data(0, ROLE_SUB), top.isHidden()))
        for j in range(top.childCount()):
            child = top.child(j)
            out.append((child.data(0, ROLE_INDEX), child.data(0, ROLE_SUB), child.isHidden()))
    return out


def visible(view):
    return [(index, sub) for index, sub, hidden in rows(view) if not hidden]


@pytest.fixture
def loaded(view, monkeypatch):
    """A view with the demo dictionary in its tree."""
    node, _path = load_demo(view)
    monkeypatch.setattr(view.manager, "node", lambda _n, node=node: node)
    view._populate_od(5)
    return view


# --- the filter --------------------------------------------------------------------
def test_everything_shows_before_anything_is_typed(loaded):
    assert visible(loaded), "an empty filter hides nothing"
    assert len(visible(loaded)) == len(rows(loaded))


def test_an_index_finds_its_object(loaded):
    loaded.od_search.setText("1018")
    assert {index for index, _sub in visible(loaded)} == {0x1018}


def test_a_name_finds_it_too(loaded):
    loaded.od_search.setText("device type")
    found = visible(loaded)
    assert found and all(index == 0x1000 for index, _sub in found)


def test_several_words_must_all_match(loaded):
    """So a search narrows as you type rather than widening."""
    loaded.od_search.setText("identity")
    broad = len(visible(loaded))
    loaded.od_search.setText("identity serial")
    assert 0 < len(visible(loaded)) < broad


def test_a_record_that_matches_shows_what_is_inside_it(loaded):
    """Searching for the record and being shown an empty record is no answer."""
    loaded.od_search.setText("identity")
    found = visible(loaded)
    assert any(sub is not None for _index, sub in found), "the sub-indices came too"


def test_a_sub_index_that_matches_brings_its_parent(loaded):
    """A child on its own would have nowhere to appear."""
    loaded.od_search.setText("serial")
    found = visible(loaded)
    parents = [(index, sub) for index, sub in found if sub is None]
    assert parents, "the record has to be visible for its child to be"


def test_nothing_matching_hides_everything_and_keeps_it(loaded):
    before = len(rows(loaded))
    loaded.od_search.setText("no such object anywhere")
    assert visible(loaded) == []
    loaded.od_search.setText("")
    assert len(visible(loaded)) == before, "nothing was discarded"


# --- the watch list ----------------------------------------------------------------
def _item(view, index, sub):
    return view._od_item(index, sub)


def test_ticking_watch_remembers_the_object(loaded):
    _item(loaded, 0x1018, 1).setCheckState(COL_WATCH, Qt.Checked)
    assert (0x1018, 1) in loaded._watched(5)


def test_unticking_forgets_it(loaded):
    item = _item(loaded, 0x1018, 1)
    item.setCheckState(COL_WATCH, Qt.Checked)
    item.setCheckState(COL_WATCH, Qt.Unchecked)
    assert (0x1018, 1) not in loaded._watched(5)


def test_watched_only_shows_the_short_list(loaded):
    _item(loaded, 0x1018, 1).setCheckState(COL_WATCH, Qt.Checked)
    loaded.watched_only.setChecked(True)
    found = visible(loaded)
    assert (0x1018, 1) in found
    assert (0x1000, None) not in found, "an object nobody watched is not on the list"


def test_a_watched_child_keeps_its_parent_visible(loaded):
    _item(loaded, 0x1018, 1).setCheckState(COL_WATCH, Qt.Checked)
    loaded.watched_only.setChecked(True)
    assert (0x1018, None) in visible(loaded)


def test_the_filter_and_the_list_narrow_together(loaded):
    for sub in (1, 2):
        _item(loaded, 0x1018, sub).setCheckState(COL_WATCH, Qt.Checked)
    loaded.watched_only.setChecked(True)
    loaded.od_search.setText("vendor")
    found = [(i, s) for i, s in visible(loaded) if s is not None]
    assert found == [(0x1018, 1)], "watched and matching, not one or the other"


def test_the_list_belongs_to_the_device_not_the_address(loaded):
    """Two different controllers can sit at node 5 on different days."""
    _item(loaded, 0x1018, 1).setCheckState(COL_WATCH, Qt.Checked)
    stored = loaded.ctx.settings.get("canopen.watch", {})
    assert loaded._identities[5].key in stored
    assert "node5" not in stored


def test_an_unidentified_node_still_gets_a_list(view):
    """Falling back to the address is worse than an identity and better than nothing."""
    assert view._watch_key(7) == "node7"


def test_the_list_survives_a_restart(loaded, app, tmp_path, monkeypatch):
    _item(loaded, 0x1018, 1).setCheckState(COL_WATCH, Qt.Checked)

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    ctx = Context(log=print)
    bus = BusManager()
    again = CanopenView(CanopenManager(bus), Hooks(ctx), ctx)
    again._identities[5] = loaded._identities[5]
    assert (0x1018, 1) in again._watched(5)
    bus.disconnect_bus()


def test_an_empty_list_is_not_stored(loaded):
    """Otherwise settings.json fills with keys saying nothing."""
    item = _item(loaded, 0x1018, 1)
    item.setCheckState(COL_WATCH, Qt.Checked)
    item.setCheckState(COL_WATCH, Qt.Unchecked)
    assert loaded.ctx.settings.get("canopen.watch", {}) == {}
