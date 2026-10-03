# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""A DCF or EDS as a row of the CANopen node list: its dictionary in the tree,
read and changed with no node and no bus, and saved as a DCF."""

from pathlib import Path

import pytest
from PySide6.QtCore import Qt

from pycangui.canopen.manager import CanopenManager
from pycangui.core.bus import BusManager
from pycangui.core.context import Context
from pycangui.core.hooks import Hooks
from pycangui.ui import folders, messages, pdo_view
from pycangui.ui.canopen_view import COL_VALUE, EDITED_COLOUR, ROLE_FILE, CanopenView
from pycangui.ui.pdo_view import COL_ENABLED, COL_TRANS

EDS = """[FileInfo]
FileName=drive.eds
EDSVersion=4.0
[DeviceInfo]
VendorNumber=0x42
ProductNumber=0x1234
[MandatoryObjects]
SupportedObjects=1
1=0x1000
[1000]
ParameterName=Device type
ObjectType=0x7
DataType=0x0007
AccessType=ro
DefaultValue=0x00020192
[ManufacturerObjects]
SupportedObjects=2
1=0x2001
2=0x2002
[2001]
ParameterName=Current limit
ObjectType=0x7
DataType=0x0003
AccessType=rw
DefaultValue=250
LowLimit=0
HighLimit=1000
[2002]
ParameterName=Drive name
ObjectType=0x7
DataType=0x0009
AccessType=rw
DefaultValue=left
"""


@pytest.fixture
def eds(tmp_path):
    path = tmp_path / "drive.eds"
    path.write_text(EDS, encoding="utf-8")
    return path


@pytest.fixture
def ctx(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path / "home"))
    return Context(log=print)


@pytest.fixture
def view(ctx):
    bus = BusManager()
    manager = CanopenManager(bus)
    widget = CanopenView(manager, Hooks(ctx), ctx)
    yield widget
    manager.shutdown()


def value_of(view, index, sub=0):
    return view._od_item(index, sub).text(COL_VALUE)


def edit(view, index, text, sub=0):
    view._od_item(index, sub).setText(COL_VALUE, text)


def test_a_file_is_a_row_whose_dictionary_is_in_the_tree(view, eds):
    source = view.open_file(eds)
    row = view.nodes.currentItem()
    assert row.data(0, ROLE_FILE), "selected, as a file"
    assert view.selected_node() is None, "and not a node: nothing goes on the bus for it"
    assert source.current(0x2001, 0) == (250, None), "the file's value, with no node and no bus"
    assert value_of(view, 0x2001) and value_of(view, 0x2002), "shown in the tree"


def test_a_value_changed_in_the_tree_changes_the_file_in_memory(app, view, eds):
    source = view.open_file(eds)
    edit(view, 0x2001, "400")
    app.processEvents()
    assert source.edited == {(0x2001, 0): 400}
    assert source.unsaved and eds.read_text(encoding="utf-8") == EDS, "not on disk yet"
    assert view._od_item(0x2001, 0).background(COL_VALUE).color() == EDITED_COLOUR
    assert view.save_file_btn.isVisible() or view.save_file_btn.isEnabled()


def test_a_value_the_file_will_not_take_is_refused_and_put_back(app, view, eds):
    source = view.open_file(eds)
    shown = value_of(view, 0x2001)
    edit(view, 0x2001, "5000")  # above HighLimit
    app.processEvents()
    assert source.edited == {} and value_of(view, 0x2001) == shown, "refused, and put back"
    edit(view, 0x2001, "twelve")
    app.processEvents()
    assert source.edited == {}


def test_text_goes_in_as_text(app, view, eds):
    source = view.open_file(eds)
    edit(view, 0x2002, "right")
    app.processEvents()
    assert source.edited == {(0x2002, 0): "right"}


def test_an_eds_is_saved_as_a_new_dcf_and_the_row_follows(app, view, eds, tmp_path, monkeypatch):
    source = view.open_file(eds)
    edit(view, 0x2001, "400")
    app.processEvents()
    target = tmp_path / "drive.dcf"
    monkeypatch.setattr(folders, "save_file", lambda *a, **k: str(target))
    assert view.save_file(source), "an EDS keeps its defaults: saved under a new name"
    assert target.is_file() and "ParameterValue=400" in target.read_text(encoding="utf-8")
    assert not source.unsaved and source.path == target
    assert view.nodes.currentItem().data(0, ROLE_FILE) == str(target.resolve())


def test_disconnecting_the_bus_keeps_the_files(view, eds):
    view.open_file(eds)
    view.clear()
    assert view.nodes.topLevelItemCount() == 1 and view.selected_file() is not None


def test_closing_an_edited_file_asks_and_cancel_keeps_it(app, view, eds, monkeypatch):
    source = view.open_file(eds)
    edit(view, 0x2001, "400")
    app.processEvents()
    monkeypatch.setattr(messages, "question", lambda *a, **k: messages.Button.Cancel)
    assert not view.close_file(source) and view.selected_file() is source
    assert not view.may_discard(), "and quitting asks the same"
    monkeypatch.setattr(messages, "question", lambda *a, **k: messages.Button.Discard)
    assert view.close_file(source) and view.nodes.topLevelItemCount() == 0


def test_open_files_come_back_with_the_workspace(ctx, eds):
    first = CanopenView(CanopenManager(BusManager()), Hooks(ctx), ctx)
    first.open_file(eds)
    again = CanopenView(CanopenManager(BusManager()), Hooks(ctx), ctx)
    rows = [again.nodes.topLevelItem(i) for i in range(again.nodes.topLevelItemCount())]
    assert [Path(r.data(0, ROLE_FILE)).name for r in rows] == ["drive.eds"]


def test_a_file_row_offers_what_a_file_can_do(view, eds):
    source = view.open_file(eds)
    actions = [a.text() for a in view.file_menu(source).actions() if a.text()]
    assert actions == ["Save", "Save as...", "Close file"]
    assert all(not b.isEnabled() for b in view._node_buttons), "nothing to ask of a file"


def test_something_that_is_not_a_dictionary_is_said_rather_than_listed(view, tmp_path):
    junk = tmp_path / "notes.eds"
    junk.write_text("hello", encoding="utf-8")
    assert view.open_file(junk) is None and view.nodes.topLevelItemCount() == 0


@pytest.mark.parametrize("typed", ["", " "])
def test_nothing_typed_is_not_a_value(app, view, eds, typed):
    source = view.open_file(eds)
    edit(view, 0x2001, typed)
    app.processEvents()
    assert source.edited == {}


def test_the_watch_column_is_not_offered_for_a_file(view, eds):
    view.open_file(eds)
    from pycangui.ui.canopen_view import COL_WATCH

    assert view._od_item(0x2001, 0).data(COL_WATCH, Qt.CheckStateRole) is None


# --- the PDO tab, for a file ----------------------------------------------------------------
@pytest.fixture
def demo(tmp_path):
    """The demo device's EDS: a TPDO of three objects and an RPDO of one, their
    COB-IDs given as the node ID plus a base."""
    from pycangui import resources

    path = tmp_path / "demo.eds"
    path.write_bytes(resources.path("demo.eds").read_bytes())
    return path


def pdos(view):
    return {f"{c.direction}{c.number}": c for c in view.pdo_config._configs}


def test_a_files_pdos_are_in_the_pdo_tab(view, demo):
    view.open_file(demo)
    found = pdos(view)
    assert set(found) == {"TPDO1", "RPDO1"}
    assert found["TPDO1"].cob_id == 0x180 and found["TPDO1"].cob_id_text, "relative to the node"
    assert [(e.index, e.subindex, e.bits) for e in found["TPDO1"].entries] == [
        (0x6041, 0, 16),
        (0x2000, 1, 16),
        (0x2000, 2, 32),
    ]
    assert view.pdo_config.tree.topLevelItemCount() == 2


def test_only_what_changed_goes_into_the_file(view, demo):
    from pycangui.canopen import pdo_file

    source = view.open_file(demo)
    config = pdos(view)["TPDO1"]
    assert pdo_file.apply(source, config) == "" and source.edited == {}, "untouched: nothing"
    config.transmission_type = 1
    assert pdo_file.apply(source, config) == ""
    assert source.edited == {(0x1800, 2): 1}, "and the node-relative COB-ID is left as text"


def test_unmapping_and_disabling_go_straight_into_the_files_objects(app, view, demo):
    """In a file there is no second step: a change is in the file as it is
    made, so it is saved, or asked about on closing, like any other."""
    source = view.open_file(demo)
    tab = view.pdo_config
    assert tab.write_btn.isHidden(), "nothing to put anywhere"
    row = tab.tree.topLevelItem(0)
    assert tab._config_of(row).direction == "TPDO"
    row.setCheckState(COL_ENABLED, Qt.Unchecked)
    assert source.unsaved, "disabled in the file at once"
    row = tab.tree.topLevelItem(0)  # the tree is drawn again from the file
    tab.tree.setCurrentItem(row.child(row.childCount() - 1))
    tab._remove_entry()
    assert source.edited == {(0x1800, 1): 0x8000_0180, (0x1A00, 0): 2}
    again = pdos(view)["TPDO1"]
    assert not again.enabled and len(again.entries) == 2 and not again.cob_id_text


def test_a_cell_typed_in_the_pdo_tab_is_in_the_file_and_closing_asks(app, view, demo, monkeypatch):
    source = view.open_file(demo)
    tab = view.pdo_config
    tab.tree.topLevelItem(0).setText(COL_TRANS, "1")
    assert source.edited == {(0x1800, 2): 1}
    asked = []
    monkeypatch.setattr(
        messages, "question", lambda *a, **k: asked.append(a[1]) or messages.Button.Cancel
    )
    assert not view.close_file(source) and asked, "the change is not lost without a word"


def test_a_change_the_file_has_no_room_for_is_refused_and_not_shown(app, view, demo, monkeypatch):
    from pycangui.canopen import PdoEntry

    source = view.open_file(demo)
    tab = view.pdo_config
    boxes = []
    monkeypatch.setattr(pdo_view.messages, "warning", lambda *a, **k: boxes.append(a[1]))
    rpdo = next(
        tab.tree.topLevelItem(i)
        for i in range(tab.tree.topLevelItemCount())
        if tab._config_of(tab.tree.topLevelItem(i)).direction == "RPDO"
    )
    config = tab._config_of(rpdo)
    config.entries += [PdoEntry(0x2001, 0, 8)] * 6
    assert not tab._into_file(config)
    assert boxes and source.edited == {}
    assert len(pdos(view)["RPDO1"].entries) == 1, "the tree is the file again"


class _Picks:
    """Stands in for the object picker: answers with one entry, at once."""

    def __init__(self, *_args, **_kwargs):
        from pycangui.canopen import PdoEntry

        self.entry = PdoEntry(0x2001, 0, 8, "Picked")

    def exec(self):
        return pdo_view.QDialog.Accepted

    def chosen(self):
        return self.entry


def mapped(view, name="TPDO1"):
    return [(e.index, e.subindex) for e in pdos(view)[name].entries]


def test_an_object_is_mapped_after_the_one_selected(app, view, demo, monkeypatch):
    view.open_file(demo)
    tab = view.pdo_config
    monkeypatch.setattr(pdo_view, "ObjectPicker", _Picks)
    row = tab.tree.topLevelItem(0)
    del tab._config_of(row).entries[-1]  # room for eight more bits
    tab._into_file(tab._config_of(row))
    row = tab.tree.topLevelItem(0)
    tab.tree.setCurrentItem(row.child(0))
    tab._add_entry()
    assert mapped(view) == [(0x6041, 0), (0x2001, 0), (0x2000, 1)]
    picked = tab.tree.selectedItems()[0]
    assert picked.parent() is not None and picked.parent().indexOfChild(picked) == 1


def test_with_the_pdo_selected_an_object_goes_at_the_end(app, view, demo, monkeypatch):
    view.open_file(demo)
    tab = view.pdo_config
    monkeypatch.setattr(pdo_view, "ObjectPicker", _Picks)
    row = tab.tree.topLevelItem(0)
    del tab._config_of(row).entries[0]
    tab._into_file(tab._config_of(row))
    tab.tree.setCurrentItem(tab.tree.topLevelItem(0))
    tab._add_entry()
    assert mapped(view) == [(0x2000, 1), (0x2000, 2), (0x2001, 0)]


def test_a_mapped_object_moves_up_and_down(app, view, demo):
    view.open_file(demo)
    tab = view.pdo_config
    tab.tree.setCurrentItem(tab.tree.topLevelItem(0).child(2))
    tab._move_entry(-1)
    assert mapped(view) == [(0x6041, 0), (0x2000, 2), (0x2000, 1)]
    tab._move_entry(-1)
    tab._move_entry(-1)  # already first: stays
    assert mapped(view) == [(0x2000, 2), (0x6041, 0), (0x2000, 1)]
    tab._move_entry(1)
    assert mapped(view) == [(0x6041, 0), (0x2000, 2), (0x2000, 1)]


def can_edit(tree, item, column) -> bool:
    from PySide6.QtWidgets import QStyleOptionViewItem

    index = tree.indexFromItem(item, column)
    editor = tree.itemDelegate().createEditor(tree.viewport(), QStyleOptionViewItem(), index)
    if editor is not None:
        editor.deleteLater()
    return editor is not None


def test_only_a_value_is_typed_into_in_the_dictionary(view, eds):
    """The index, name, type and access are what the EDS says. Typing over
    them changed nothing and left the cell looking changed."""
    view.open_file(eds)
    row = view._od_item(0x2001, 0)
    assert can_edit(view.od, row, COL_VALUE)
    assert not any(can_edit(view.od, row, column) for column in range(COL_VALUE))


def test_a_pdo_s_name_and_size_are_not_typed_into(view, demo):
    from pycangui.ui.pdo_view import COL_BITS, COL_COBID, COL_NAME

    view.open_file(demo)
    tree = view.pdo_config.tree
    row = tree.topLevelItem(0)
    assert can_edit(tree, row, COL_COBID) and can_edit(tree, row, COL_TRANS)
    assert not can_edit(tree, row, COL_NAME) and not can_edit(tree, row, COL_BITS)


def test_a_pdo_with_more_objects_than_the_file_has_room_for_is_refused(view, demo):
    from pycangui.canopen import PdoEntry, pdo_file

    source = view.open_file(demo)
    config = pdos(view)["RPDO1"]
    config.entries += [PdoEntry(0x2001, 0, 16)] * 4
    assert pdo_file.apply(source, config) != "" and source.edited == {}


def test_a_saved_dcf_has_the_pdo_it_was_given(view, demo, tmp_path):
    from pycangui.canopen import pdo_file
    from pycangui.custom_panes.source import FileSource

    source = view.open_file(demo)
    config = pdos(view)["TPDO1"]
    config.cob_id, config.transmission_type = 0x1A5, 10
    config.entries.reverse()
    assert pdo_file.apply(source, config) == ""
    saved = FileSource(source.save(tmp_path / "set.dcf"))
    read = {f"{c.direction}{c.number}": c for c in pdo_file.configs(saved)}["TPDO1"]
    assert (read.cob_id, read.transmission_type, read.enabled) == (0x1A5, 10, True)
    assert [e.index for e in read.entries] == [0x2000, 0x2000, 0x6041]


def test_a_node_selected_after_a_file_gets_the_tab_back(view, demo):
    view.open_file(demo)
    view.nodes.setCurrentItem(None)
    assert view.pdo_config._configs == [] and view.pdo_config._file is None
