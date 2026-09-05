"""A panel on screen: a dock of its own, built by picking objects.

The two halves that matter are that a panel is a *dock* -- so two of them sit
side by side comparing two nodes, which is the case the whole thing exists for
-- and that building one takes no code: the objects are picked in the object
dictionary, where they can be searched for and where their names already are.
"""

import pytest
from PySide6.QtCore import QSettings

from pycangui.canopen.display import Display
from pycangui.panels import model
from pycangui.panels.model import Field, Panel
from pycangui.ui.main_window import MainWindow, panel_instance


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


def sample(title="Battery limits") -> Panel:
    return Panel(
        title=title,
        node=5,
        fields=[
            Field(index=0x2001, kind="number", label="Motor current"),
            Field(index=0x1018, sub=1, kind="value", label="Vendor-ID"),
        ],
    )


# --- a panel is a dock ---------------------------------------------------------------
def test_opening_a_panel_gives_it_a_dock_of_its_own(window):
    model.save("battery", sample())
    name = window.open_panel("battery")

    assert name == panel_instance("battery")
    assert window.panes.docks[name].windowTitle() == "Battery limits"
    assert window.panes.kind_of(name) == "panel"


def test_two_panels_are_two_docks(window):
    """The case the whole thing exists for: node 1 beside node 2."""
    model.save("battery", sample("Battery limits"))
    model.save("gains", sample("Control gains"))
    window.open_panel("battery")
    window.open_panel("gains")

    titles = [window.panes.docks[n].windowTitle() for n in window.panes.names() if ":" in n]
    assert sorted(titles) == ["Battery limits", "Control gains"]


def test_opening_the_same_panel_twice_shows_the_one_that_is_open(window):
    model.save("battery", sample())
    first = window.open_panel("battery")
    assert window.open_panel("battery") == first
    assert len([n for n in window.panes.names() if ":" in n]) == 1


def test_a_panel_with_no_file_yet_gets_one(window):
    window.open_panel("new one")
    assert model.load("new one") is not None
    assert model.load("new one").title == "new one"


def test_a_panel_is_not_offered_as_another_one_of_these(window):
    """There is no Panel 2; there is the panel called Battery limits."""
    offered = {a.text() for a in _submenu(window, "New pane").actions()}
    assert "Panel" not in offered


def test_a_panel_pane_cannot_be_opened_without_saying_which(window):
    assert window.panes.add("panel") == ""
    assert "opened by name" in window.log.toPlainText()


def test_the_panels_menu_lists_what_the_workspace_has(app, window):
    model.save("battery", sample())
    model.save("gains", sample("Control gains"))
    window._build_view_menu()
    listed = [a.text() for a in _submenu(window, "Panels").actions() if a.text()]
    assert listed == ["battery", "gains", "New panel..."]


def _submenu(window, title):
    for action in window.view_menu.actions():
        if action.menu() is not None and action.text() == title:
            return action.menu()
    raise AssertionError(f"no {title} submenu")


# --- and it comes back --------------------------------------------------------------------
def test_a_panel_left_open_is_open_next_time(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    first = MainWindow()
    model.save("battery", sample())
    first.open_panel("battery")
    first.close()
    settle(app)

    second = MainWindow()
    second.show()
    settle(app)
    assert panel_instance("battery") in second.panes.docks
    assert second.panes.docks[panel_instance("battery")].windowTitle() == "Battery limits"
    second.close()


def test_closing_a_panel_pane_does_not_throw_the_panel_away(window):
    """The dock is a view of the file, and closing a window is not deleting."""
    model.save("battery", sample())
    name = window.open_panel("battery")
    window.panes.remove(name)
    assert model.load("battery") is not None
    assert "battery" in model.names()


# --- picking the objects ---------------------------------------------------------------------
def test_objects_picked_in_the_dictionary_land_on_a_panel(window):
    window._add_to_panel("battery", [Field(index=0x2001, kind="number", label="Motor current")])
    panel = model.load("battery")
    assert [(f.index, f.label) for f in panel.fields] == [(0x2001, "Motor current")]
    assert panel_instance("battery") in window.panes.docks, "and it opens"


def test_several_at_once_are_asked_about_once(window, monkeypatch):
    """Being asked six times what to call the panel would be its own argument
    against the feature."""
    from PySide6.QtWidgets import QInputDialog

    asked = []

    def once(*_a, **_k):
        asked.append(1)
        return ("Battery limits", True)

    monkeypatch.setattr(QInputDialog, "getText", once)
    window._add_to_panel("", [Field(index=0x2001), Field(index=0x2002), Field(index=0x2003)])

    assert len(asked) == 1
    assert len(model.load("Battery limits").fields) == 3


def test_a_name_that_will_not_do_makes_no_panel(window, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("../escape", True))
    window._add_to_panel("", [Field(index=0x2001)])
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

    view.add_to_panel.emit("battery", view._picked_fields())
    settle(app)
    assert [f.index for f in model.load("battery").fields] == [0x2001]


# --- where the values come from -----------------------------------------------------------------
def test_the_panel_reads_through_whatever_it_is_bound_to(app, window):
    model.save("battery", sample())
    window.open_panel("battery")
    view = window._panel_view("battery")

    asked = []
    view.bind(_Fake(asked))
    view.refresh()
    assert (0x2001, 0) in asked and (0x1018, 1) in asked


def test_a_value_reaches_the_field_it_belongs_to(app, window):
    model.save("battery", sample())
    window.open_panel("battery")
    view = window._panel_view("battery")
    source = _Fake([])
    view.bind(source)

    source.value.emit(0x2001, 0, 1234, None)
    settle(app)
    assert view._widgets[0].edit.text() == "1234"


def test_writing_goes_to_the_source_and_not_to_the_bus(app, window):
    model.save("battery", sample())
    window.open_panel("battery")
    view = window._panel_view("battery")
    source = _Fake([])
    view.bind(source)

    view._widgets[0].edit.setText("42")
    view._widgets[0].edit.editingFinished.emit()
    assert source.written == [(0x2001, 0, 42)]


def test_a_source_that_cannot_be_written_to_makes_the_panel_read_only(app, window):
    model.save("battery", sample())
    window.open_panel("battery")
    view = window._panel_view("battery")
    source = _Fake([])
    source.writable = False
    view.bind(source)
    assert view._widgets[0].edit.isReadOnly()


def test_with_nothing_bound_a_read_says_so_rather_than_failing(app, window):
    model.save("battery", sample())
    window.open_panel("battery")
    view = window._panel_view("battery")
    view.bind(None)
    view.refresh()
    settle(app)
    assert "no node or file selected" in window.log.toPlainText()


def test_the_selector_offers_the_nodes_and_a_file(app, window):
    model.save("battery", sample())
    window.open_panel("battery")
    view = window._panel_view("battery")
    entries = [view.sources.itemText(i) for i in range(view.sources.count())]
    assert "Node 5" in entries, "the panel's usual node, even before it is heard from"
    assert entries[-1].startswith("Open a DCF")


# --- what is wrong with it ---------------------------------------------------------------
def test_a_panel_with_a_bad_field_still_opens_and_says_why(app, window):
    """Refusing to open it would leave nobody able to see which field it was."""
    model.save("broken", Panel(title="Broken", fields=[Field(index=0x2001, kind="flags")]))
    window.open_panel("broken")
    view = window._panel_view("broken")
    assert view.note.isVisible()
    assert "would show nothing" in view.note.text()


def test_editing_the_panel_rewrites_its_file_and_its_title(app, window):
    model.save("battery", sample())
    window.open_panel("battery")
    view = window._panel_view("battery")

    view.panel = Panel(title="Pack limits", fields=view.panel.fields)
    model.save("battery", view.panel)
    view.rebuild()
    view.changed.emit()
    settle(app)
    assert window.panes.docks[panel_instance("battery")].windowTitle() == "Pack limits"


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
