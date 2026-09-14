# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""A pane on screen: a dock of its own, built by picking objects.

The two halves that matter are that a pane is a *dock* -- so two of them sit
side by side comparing two nodes, which is the case the whole thing exists for
-- and that building one takes no code: the objects are picked in the object
dictionary, where they can be searched for and where their names already are.
"""

import pytest
from PySide6.QtCore import QSettings

from pycangui.canopen.display import Display
from pycangui.custom_panes import model
from pycangui.custom_panes.model import CustomPane, Field
from pycangui.ui import messages
from pycangui.ui.main_window import MainWindow, custom_instance


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    win = MainWindow()
    win.show()
    app.processEvents()
    yield win
    win.close()


def settle(app, times=5):
    for _ in range(times):
        app.processEvents()


def sample(title="Battery limits") -> CustomPane:
    return CustomPane(
        title=title,
        node=5,
        fields=[
            Field(index=0x2001, kind="number", label="Motor current"),
            Field(index=0x1018, sub=1, kind="value", label="Vendor-ID"),
        ],
    )


# --- a pane is a dock ---------------------------------------------------------------
def test_opening_a_custom_pane_gives_it_a_dock_of_its_own(window):
    model.save("battery", sample())
    name = window.open_custom_pane("battery")

    assert name == custom_instance("battery")
    assert window.panes.docks[name].windowTitle() == "Battery limits"
    assert window.panes.kind_of(name) == "custom"


def test_a_custom_pane_opens_in_a_window_of_its_own(app, window):
    """It is something to look at beside the panes already on screen."""
    model.save("battery", sample())
    name = window.open_custom_pane("battery")
    settle(app)
    assert window.panes.docks[name].isFloating()
    assert window.panes.docks[name].isVisible()


def test_two_custom_panes_are_two_docks(window):
    """The case the whole thing exists for: node 1 beside node 2."""
    model.save("battery", sample("Battery limits"))
    model.save("gains", sample("Control gains"))
    window.open_custom_pane("battery")
    window.open_custom_pane("gains")

    titles = [
        window.panes.docks[n].windowTitle() for n in window.panes.names() if n.startswith("custom:")
    ]
    assert sorted(titles) == ["Battery limits", "Control gains"]


def test_opening_the_same_custom_pane_twice_shows_the_one_that_is_open(window):
    model.save("battery", sample())
    first = window.open_custom_pane("battery")
    assert window.open_custom_pane("battery") == first
    assert len([n for n in window.panes.names() if n.startswith("custom:")]) == 1


def test_a_custom_pane_with_no_file_yet_gets_one(window):
    window.open_custom_pane("new one")
    assert model.load("new one") is not None
    assert model.load("new one").title == "new one"


def test_a_custom_pane_is_not_offered_as_a_standard_one(window):
    """There is no Custom pane 2; there is the one called Battery limits."""
    offered = {a.text() for a in _submenu(window, "Standard panes").actions()}
    assert not any("ustom" in text for text in offered)


def test_a_custom_pane_cannot_be_opened_without_saying_which(window):
    assert window.panes.add("custom") == ""
    assert "opened by name" in window.log.toPlainText()


def test_the_custom_menu_lists_what_the_workspace_has(app, window):
    model.save("battery", sample())
    model.save("gains", sample("Control gains"))
    window._build_view_menu()
    listed = [a.text() for a in _submenu(window, "Custom panes").actions() if a.text()]
    assert listed == ["battery", "gains", "New custom pane..."]


def _submenu(window, title):
    for action in window.view_menu.actions():
        if action.menu() is not None and action.text() == title:
            return action.menu()
    raise AssertionError(f"no {title} submenu")


# --- and it comes back --------------------------------------------------------------------
def test_the_default_name_of_a_custom_pane_is_what_its_file_says(app, window):
    """No amount of looking at "custom:battery" produces "Battery limits"."""
    model.save("battery", sample())
    name = window.open_custom_pane("battery")
    assert window.panes.default_title(name) == "Battery limits"

    window.panes.rename(name, "Pack")
    assert window.panes.docks[name].windowTitle() == "Pack"
    window.panes.rename(name, "")
    assert window.panes.docks[name].windowTitle() == "Battery limits", "not the instance name"


def test_renaming_a_custom_pane_survives_editing_its_file(app, window):
    """Editing the title in the file must not quietly undo a rename."""
    model.save("battery", sample())
    name = window.open_custom_pane("battery")
    window.panes.rename(name, "Pack")

    window.panes.set_default_title(name, "Battery limits, revised")
    assert window.panes.docks[name].windowTitle() == "Pack"
    window.panes.rename(name, "")
    assert window.panes.docks[name].windowTitle() == "Battery limits, revised", "the newer default"


def test_a_custom_pane_left_open_is_open_next_time(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    first = MainWindow()
    model.save("battery", sample())
    first.open_custom_pane("battery")
    first.close()
    settle(app)

    second = MainWindow()
    second.show()
    settle(app)
    assert custom_instance("battery") in second.panes.docks
    assert second.panes.docks[custom_instance("battery")].windowTitle() == "Battery limits"
    second.close()


def test_closing_a_custom_pane_does_not_throw_the_pane_away(window):
    """The dock is a view of the file, and closing a window is not deleting."""
    model.save("battery", sample())
    name = window.open_custom_pane("battery")
    window.panes.remove(name)
    assert model.load("battery") is not None
    assert "battery" in model.names()


# --- picking the objects ---------------------------------------------------------------------
def test_objects_picked_in_the_dictionary_land_on_a_custom_pane(window):
    window._add_to_custom_pane(
        "battery", [Field(index=0x2001, kind="number", label="Motor current")]
    )
    pane = model.load("battery")
    assert [(f.index, f.label) for f in pane.fields] == [(0x2001, "Motor current")]
    assert custom_instance("battery") in window.panes.docks, "and it opens"


def test_several_at_once_are_asked_about_once(window, monkeypatch):
    """Being asked six times what to call the pane would be its own argument
    against the feature."""
    from PySide6.QtWidgets import QInputDialog

    asked = []

    def once(*_a, **_k):
        asked.append(1)
        return ("Battery limits", True)

    monkeypatch.setattr(QInputDialog, "getText", once)
    window._add_to_custom_pane("", [Field(index=0x2001), Field(index=0x2002), Field(index=0x2003)])

    assert len(asked) == 1
    assert len(model.load("Battery limits").fields) == 3


def test_a_name_that_will_not_do_makes_no_custom_pane(window, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("../escape", True))
    window._add_to_custom_pane("", [Field(index=0x2001)])
    assert model.names() == []
    assert "file name" in window.log.toPlainText()


def test_the_dictionary_offers_what_is_selected(app, window):
    """A writable object is offered as one to type into, a read-only one as a
    reading -- which is what they are."""
    view = window.canopen_view
    view.od.clear()
    from PySide6.QtWidgets import QTreeWidgetItem

    from pycangui.ui.canopen_view import ROLE_INDEX, ROLE_SUB

    for index, name, access in ((0x2001, "Motor current", "rw"), (0x1000, "Device type", "ro")):
        item = QTreeWidgetItem([f"{index:04X}", name, "UNSIGNED32", access, "", ""])
        item.setData(0, ROLE_INDEX, index)
        item.setData(0, ROLE_SUB, None)
        view.od.addTopLevelItem(item)
    view.od.selectAll()

    picked = view._picked_fields()
    assert [(f.index, f.kind, f.label) for f in picked] == [
        (0x2001, "number", "Motor current"),
        (0x1000, "value", "Device type"),
    ]


def test_picking_reaches_the_window(app, window):
    from PySide6.QtWidgets import QTreeWidgetItem

    from pycangui.ui.canopen_view import ROLE_INDEX, ROLE_SUB

    view = window.canopen_view
    view.od.clear()
    item = QTreeWidgetItem(["2001", "Motor current", "UNSIGNED32", "rw", "", ""])
    item.setData(0, ROLE_INDEX, 0x2001)
    item.setData(0, ROLE_SUB, None)
    view.od.addTopLevelItem(item)
    view.od.selectAll()

    view.add_to_custom_pane.emit("battery", view._picked_fields())
    settle(app)
    assert [f.index for f in model.load("battery").fields] == [0x2001]


# --- where the values come from -----------------------------------------------------------------
def test_the_pane_reads_through_whatever_it_is_bound_to(app, window):
    model.save("battery", sample())
    window.open_custom_pane("battery")
    view = window._custom_pane_view("battery")

    asked = []
    view.bind(_Fake(asked))
    view.refresh()
    assert (0x2001, 0) in asked and (0x1018, 1) in asked


def test_a_value_reaches_the_field_it_belongs_to(app, window):
    model.save("battery", sample())
    window.open_custom_pane("battery")
    view = window._custom_pane_view("battery")
    source = _Fake([])
    view.bind(source)

    source.value.emit(0x2001, 0, 1234, None)
    settle(app)
    assert view._widgets[0].edit.text() == "1234"


def test_writing_goes_to_the_source_and_not_to_the_bus(app, window):
    model.save("battery", sample())
    window.open_custom_pane("battery")
    view = window._custom_pane_view("battery")
    source = _Fake([])
    view.bind(source)

    view._widgets[0].edit.setText("42")
    view._widgets[0].edit.editingFinished.emit()
    assert source.written == [(0x2001, 0, 42)]


def test_a_source_that_cannot_be_written_to_makes_the_pane_read_only(app, window):
    model.save("battery", sample())
    window.open_custom_pane("battery")
    view = window._custom_pane_view("battery")
    source = _Fake([])
    source.writable = False
    view.bind(source)
    assert view._widgets[0].edit.isReadOnly()


def test_with_nothing_bound_a_read_says_so_rather_than_failing(app, window):
    model.save("battery", sample())
    window.open_custom_pane("battery")
    view = window._custom_pane_view("battery")
    view.bind(None)
    view.refresh()
    settle(app)
    assert "no node or file selected" in window.log.toPlainText()


def test_the_selector_offers_the_nodes_and_a_file(app, window):
    model.save("battery", sample())
    window.open_custom_pane("battery")
    view = window._custom_pane_view("battery")
    entries = [view.sources.itemText(i) for i in range(view.sources.count())]
    assert "Node 5" in entries, "the pane's usual node, even before it is heard from"
    assert entries[-1].startswith("Open a DCF")


# --- what is wrong with it ---------------------------------------------------------------
def test_a_custom_pane_with_a_bad_field_still_opens_and_says_why(app, window):
    """Refusing to open it would leave nobody able to see which field it was."""
    model.save("broken", CustomPane(title="Broken", fields=[Field(index=0x2001, kind="flags")]))
    window.open_custom_pane("broken")
    view = window._custom_pane_view("broken")
    assert view.note.isVisible()
    assert "would show nothing" in view.note.text()


def test_editing_the_pane_rewrites_its_file_and_its_title(app, window):
    model.save("battery", sample())
    window.open_custom_pane("battery")
    view = window._custom_pane_view("battery")

    view.pane = CustomPane(title="Pack limits", fields=view.pane.fields)
    model.save("battery", view.pane)
    view.rebuild()
    view.changed.emit()
    settle(app)
    assert window.panes.docks[custom_instance("battery")].windowTitle() == "Pack limits"


class _Fake:
    """A source that records what it was asked, without a bus behind it."""

    def __init__(self, asked):
        from PySide6.QtCore import QObject, Signal

        class Emitter(QObject):
            value = Signal(int, int, object, object)
            changed = Signal()

        self._emitter = Emitter()
        self.value = self._emitter.value
        self.changed = self._emitter.changed
        self.label = "Fake"
        self.writable = True
        self.asked = asked
        self.written = []

    def display(self, _index, _sub):
        return Display()

    def request(self, index, sub):
        self.asked.append((index, sub))

    def write(self, index, sub, raw):
        self.written.append((index, sub, raw))


# --- adding objects from the editor -----------------------------------------------------
def demo_source():
    """An EDS, so a pane can be built with no bus anywhere near it."""
    from pathlib import Path

    from pycangui import resources
    from pycangui.custom_panes.source import FileSource

    return FileSource(Path(resources.__file__).parent / "demo.eds")


def test_a_source_says_what_it_holds(app):
    """An EDS knows the whole dictionary without a bus being present, which is
    what lets a pane be built at a desk."""
    entries = demo_source().objects()
    assert entries, "the demo EDS has objects in it"
    assert all(len(e) == 4 for e in entries)
    assert (0x1018, 1) in {(index, sub) for index, sub, _n, _a in entries}


def test_a_source_that_knows_nothing_says_nothing(app):
    """A node with no EDS can still be read object by object; it just cannot
    be browsed, and empty is the honest answer rather than a guess."""
    from pycangui.custom_panes.source import Source

    assert Source().objects() == []


def test_the_editor_offers_add(app, window):
    """A dialog with Move up, Move down and Remove and no Add reads as an
    oversight, because it was one."""
    from PySide6.QtWidgets import QPushButton

    from pycangui.ui.custom_pane_view import CustomPaneEditor

    editor = CustomPaneEditor(None, sample(), demo_source())
    offered = {b.text() for b in editor.findChildren(QPushButton)}
    assert {"Add...", "Move up", "Move down", "Remove"} <= offered
    editor.deleteLater()


def test_the_picker_lists_what_the_source_holds(app, window):
    from pycangui.ui.custom_pane_view import AddObjects

    picker = AddObjects(None, demo_source())
    assert picker.list.count() > 5
    picker.deleteLater()


def test_the_picker_searches_the_way_the_tree_does(app, window):
    from pycangui.ui.custom_pane_view import AddObjects

    picker = AddObjects(None, demo_source())
    picker.search.setText("vendor")
    shown = [
        picker.list.item(i).text()
        for i in range(picker.list.count())
        if not picker.list.item(i).isHidden()
    ]
    assert shown and all("vendor" in text.lower() for text in shown)
    picker.deleteLater()


def test_a_picked_object_arrives_named_and_shown_sensibly(app, window):
    """A writable object is one to type into, a read-only one a reading."""
    from pycangui.ui.custom_pane_view import AddObjects

    picker = AddObjects(None, demo_source())
    picker.index.setText("1018")
    picker.sub.setText("1")
    field = picker._typed()
    assert field.index == 0x1018 and field.sub == 1
    assert field.label, "the file already knows what it is called"
    assert field.kind == "value", "0x1018:01 is read-only"
    picker.deleteLater()


def test_an_index_nobody_has_a_name_for_is_still_added(app, window):
    """Somebody with the documentation in front of them should not have to
    find a node first."""
    from pycangui.ui.custom_pane_view import AddObjects

    picker = AddObjects(None, None)
    assert picker.list.count() == 0, "nothing to pick from, and that is not a dead end"
    picker.index.setText("0x2001")
    field = picker._typed()
    assert (field.index, field.sub) == (0x2001, 0)
    picker.deleteLater()


def test_an_index_that_is_not_one_is_refused(app, window, monkeypatch):

    from pycangui.ui.custom_pane_view import AddObjects

    said = []
    monkeypatch.setattr(messages, "warning", lambda _p, _t, text: said.append(text))
    picker = AddObjects(None, None)
    picker.index.setText("not an index")
    assert picker._typed() is None
    assert said and "not a hex object index" in said[0]
    picker.deleteLater()


def test_nothing_typed_is_not_an_object(app, window):
    from pycangui.ui.custom_pane_view import AddObjects

    picker = AddObjects(None, demo_source())
    assert picker._typed() is None
    picker.deleteLater()


def test_added_objects_land_on_the_pane(app, window, monkeypatch):
    from PySide6.QtWidgets import QDialog

    from pycangui.ui import custom_pane_view

    model.save("battery", CustomPane(title="Battery limits", fields=[]))
    window.open_custom_pane("battery")
    view = window._custom_pane_view("battery")
    view.bind(demo_source())
    settle(app)

    added = [Field(index=0x2001, kind="number", label="Speed demand")]
    monkeypatch.setattr(custom_pane_view.AddObjects, "chosen_fields", lambda _self: added)
    # Press Add, then OK, without either dialog appearing.
    monkeypatch.setattr(
        custom_pane_view.CustomPaneEditor, "exec", lambda self: (self._add(), QDialog.Accepted)[1]
    )

    view._edit()
    settle(app)
    assert [(f.index, f.label) for f in view.pane.fields] == [(0x2001, "Speed demand")]
    assert [(f.index, f.label) for f in model.load("battery").fields] == [
        (0x2001, "Speed demand")
    ], "and written to the file"


# --- saving a file ----------------------------------------------------------------------------
ACME = """[FileInfo]
FileName=acme.eds
EDSVersion=4.0
[DeviceInfo]
VendorName=Acme
VendorNumber=1
ProductName=Widget
ProductNumber=2
RevisionNumber=3
[MandatoryObjects]
SupportedObjects=1
1=0x1000
[1000]
ParameterName=Device type
ObjectType=7
DataType=7
AccessType=ro
DefaultValue=0
[OptionalObjects]
SupportedObjects=1
1=0x2001
[2001]
ParameterName=Motor current
ObjectType=7
DataType=3
AccessType=rw
LowLimit=0
HighLimit=1000
DefaultValue=250
"""


@pytest.fixture
def acme(tmp_path):
    path = tmp_path / "acme.eds"
    path.write_text(ACME, encoding="utf-8")
    return path


def bound_to(app, window, path):
    from pycangui.custom_panes.source import FileSource

    model.save("battery", sample())
    window.open_custom_pane("battery")
    view = window._custom_pane_view("battery")
    view.bind(FileSource(path))
    settle(app)
    return view


def type_into(app, view, text):
    view._widgets[0].edit.setText(text)
    view._widgets[0].edit.editingFinished.emit()
    settle(app)


def answering(monkeypatch, button):
    """Answer "save the changes?" with this button, and note what was asked."""
    asked = []

    def ask(_parent, title, _text, *_args, **_kwargs):
        asked.append(title)
        return button

    monkeypatch.setattr(messages, "question", ask)
    return asked


def test_a_node_has_nothing_to_save(app, window):
    """Every write has already gone to it."""
    model.save("battery", sample())
    window.open_custom_pane("battery")
    view = window._custom_pane_view("battery")
    assert view.file_bar.isHidden(), "nothing bound"
    view.bind(_Fake([]))
    assert view.file_bar.isHidden()


def test_a_file_says_when_there_is_something_to_save(app, window, tmp_path):
    dcf = tmp_path / "acme.dcf"
    dcf.write_text(ACME, encoding="utf-8")
    view = bound_to(app, window, dcf)
    assert not view.file_bar.isHidden()
    assert not view.save_button.isEnabled() and view.unsaved_note.isHidden()

    type_into(app, view, "500")
    assert view.unsaved and view.save_button.isEnabled()
    assert not view.unsaved_note.isHidden()

    assert view.save()
    assert "ParameterValue=500" in dcf.read_text()
    assert not view.unsaved and view.unsaved_note.isHidden()


def test_an_eds_is_saved_as_a_dcf_and_the_pane_carries_on_with_that(
    app, window, tmp_path, acme, monkeypatch
):
    from pycangui.ui import folders

    view = bound_to(app, window, acme)
    type_into(app, view, "500")
    target = tmp_path / "desk.dcf"
    monkeypatch.setattr(folders, "save_file", lambda *a, **k: str(target))

    assert view.save()
    assert "ParameterValue" not in acme.read_text(), "the EDS keeps its defaults"
    assert "ParameterValue=500" in target.read_text()
    assert view.sources.currentText() == "desk.dcf"
    assert not view.unsaved


def test_cancelling_save_as_keeps_the_edits(app, window, acme, monkeypatch):
    from pycangui.ui import folders

    view = bound_to(app, window, acme)
    type_into(app, view, "500")
    monkeypatch.setattr(folders, "save_file", lambda *a, **k: "")
    assert not view.save()
    assert view.unsaved


def test_pointing_the_pane_elsewhere_asks_and_cancel_keeps_the_file(app, window, acme, monkeypatch):
    view = bound_to(app, window, acme)
    type_into(app, view, "500")
    asked = answering(monkeypatch, messages.Button.Cancel)

    view.sources.setCurrentIndex(view.sources.findData(5))
    view._on_source_chosen(view.sources.currentIndex())
    assert asked and "acme.eds" in asked[0]
    assert view.source.label == "acme.eds" and view.unsaved
    assert view.sources.currentText() == "acme.eds", "the selector goes back to the file"


def test_discard_lets_the_pane_go_elsewhere(app, window, acme, monkeypatch):
    view = bound_to(app, window, acme)
    type_into(app, view, "500")
    answering(monkeypatch, messages.Button.Discard)

    view.sources.setCurrentIndex(view.sources.findData(5))
    view._on_source_chosen(view.sources.currentIndex())
    assert view._key_of(view.source) == 5
    assert view.file_bar.isHidden()


def test_choosing_the_file_already_open_does_not_open_it_again(app, window, acme, monkeypatch):
    """Opening it again would read it from disk and lose the edits."""
    view = bound_to(app, window, acme)
    type_into(app, view, "500")
    asked = answering(monkeypatch, messages.Button.Cancel)
    source = view.source

    view._on_source_chosen(view.sources.currentIndex())
    assert not asked
    assert view.source is source and view.unsaved


def test_closing_with_unsaved_edits_asks_and_cancel_stays_open(app, window, acme, monkeypatch):
    view = bound_to(app, window, acme)
    type_into(app, view, "500")
    asked = answering(monkeypatch, messages.Button.Cancel)

    assert not window.close()
    assert asked and window.isVisible() and view.unsaved
    answering(monkeypatch, messages.Button.Discard)  # so the fixture can close it


def test_removing_a_pane_with_unsaved_edits_asks(app, window, acme, monkeypatch):
    view = bound_to(app, window, acme)
    type_into(app, view, "500")
    name = custom_instance("battery")

    answering(monkeypatch, messages.Button.Cancel)
    window._remove_pane(name)
    assert name in window.panes.docks

    answering(monkeypatch, messages.Button.Discard)
    window._remove_pane(name)
    assert name not in window.panes.docks


def test_a_workspace_switch_somebody_cancelled_leaves_the_window_they_had(app):
    from PySide6.QtCore import Signal
    from PySide6.QtWidgets import QMainWindow

    from pycangui.ui.session import Session

    class Staying(QMainWindow):
        reopen_requested = Signal(str)

        def closeEvent(self, event):
            event.ignore()

    built = []

    def build():
        built.append(Staying())
        return built[-1]

    session = Session(build)
    first = session.open()
    assert session.reopen("anything") is first
    assert session.window is first and len(built) == 1
    first.hide()
    first.deleteLater()
