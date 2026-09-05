"""Several of one pane: a second trace, a second plot, and a name each.

Every dock used to be a singleton, and the main window named each one by a
string constant in nineteen places.  A second trace with its own filter is the
smallest case that breaks that, and a user's own panels -- two side by side,
one per node -- are the case it is being broken for.

What has to hold is that the two instances are genuinely separate: their own
settings, their own place in the layout, their own entry in what is saved.
And that the first of a kind is untouched, since everything written before any
of this existed calls it by the kind's own name.
"""

import pytest
from PySide6.QtCore import QSettings

from pycangui.core.bus import Frame
from pycangui.ui.main_window import MainWindow


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


def restart(app, previous=None):
    """Close a window if given, then open a fresh one on the same settings."""
    if previous is not None:
        previous.close()
        settle(app)
    window = MainWindow()
    window.show()
    settle(app, 10)
    return window


def frame(can_id=0x123):
    return Frame(0.0, "CAN 1", can_id, False, False, True, b"\x01\x02")


# --- one kind, several instances ------------------------------------------------------
def test_the_first_of_a_kind_is_named_after_the_kind(window):
    """Every layout and setting written before W4 names its pane this way."""
    assert "trace" in window.panes.docks
    assert window.panes.docks["trace"].objectName() == "trace"
    assert window.panes.docks["trace"].windowTitle() == "Trace"


def test_a_second_one_gets_a_name_and_a_title_of_its_own(window):
    name = window.panes.add("trace")
    assert name == "trace 2"
    assert window.panes.docks[name].windowTitle() == "Trace 2"


def test_the_docks_are_told_apart_by_their_object_names(window):
    """Qt's saveState and restoreState identify a dock by nothing else."""
    second = window.panes.add("trace")
    names = {window.panes.docks[n].objectName() for n in ("trace", second)}
    assert names == {"trace", "trace 2"}


def test_they_keep_counting_up(window):
    assert [window.panes.add("trace") for _ in range(2)] == ["trace 2", "trace 3"]


def test_asking_for_a_pane_that_is_open_is_not_an_error(window):
    """It opens the one that is there rather than a second of it."""
    before = len(window.panes.names())
    assert window.panes.add("trace", name="trace") == "trace"
    assert len(window.panes.names()) == before


def test_a_kind_that_means_nothing_twice_refuses(window):
    """One Event Log however many windows you point at it."""
    assert window.panes.add("log") == "log"
    assert window.panes.add("log", name="log 2") == ""
    assert "log 2" not in window.panes.docks
    assert "only be one" in window.log.toPlainText()


def test_an_unknown_kind_is_declined_rather_than_guessed_at(window):
    assert window.panes.add("nothing of the sort") == ""


# --- and they are genuinely separate ---------------------------------------------------
def test_each_trace_keeps_its_own_filter(window):
    """The reason to open a second trace is that it should show something else."""
    second = window.panes.view(window.panes.add("trace"))
    window.trace.search.setText("first")
    second.search.setText("second")
    assert window.trace.search.text() == "first"

    window.trace._group_actions["PDO"].setChecked(False)
    assert window.ctx.settings.get("trace.hidden_groups") == ["PDO"]
    assert not window.ctx.settings.get("trace 2.hidden_groups", []), "not the other one's"


def test_both_traces_are_fed_the_capture(app, window):
    second = window.panes.view(window.panes.add("trace"))
    window.channels.frames.emit([frame()])
    settle(app)
    assert window.trace.model.rowCount() == 1
    assert second.model.rowCount() == 1


def test_each_plot_keeps_its_own_splitter(app, window, tmp_path):
    second = window.panes.view(window.panes.add("scope"))
    window.scope.splitter.setSizes([100, 900])
    second.splitter.setSizes([700, 300])
    window.panes.save_view_states()
    layout = window.ctx.layout
    assert layout.get("panes/scope") != layout.get("panes/scope 2")


# --- removing one ----------------------------------------------------------------------
def test_removing_an_extra_takes_its_dock_with_it(window):
    second = window.panes.add("trace")
    window.panes.remove(second)
    assert second not in window.panes.docks
    assert second not in window.panes.names()


def test_the_first_of_a_kind_cannot_be_removed(window):
    """It is the pane.  Putting it away is what the close button is for."""
    window.panes.remove("trace")
    assert "trace" in window.panes.docks


def test_a_removed_trace_stops_being_fed(app, window):
    """Deletion is deferred, so without disconnecting it goes on filling up."""
    second_name = window.panes.add("trace")
    second = window.panes.view(second_name)
    window.panes.remove(second_name)
    window.channels.frames.emit([frame()])
    settle(app)
    assert second.model.rowCount() == 0
    assert window.trace.model.rowCount() == 1, "the one still open carries on"


def test_closing_a_pane_is_not_removing_it(app, window):
    """Hiding and removing are answers to different questions."""
    second = window.panes.add("trace")
    window.panes.docks[second].close()
    settle(app)
    assert second in window.panes.docks, "still there, and still in the View menu"
    assert not window.panes.docks[second].isVisible()


def test_removing_a_detached_instance_closes_its_window(app, window):
    second = window.panes.add("trace")
    window.panes.detach(second)
    settle(app)
    detached = window.panes.detached[second]
    window.panes.remove(second)
    settle(app)
    assert second not in window.panes.detached
    assert not detached.isVisible()


def test_an_instance_is_pinned_and_detached_on_its_own(app, window):
    second = window.panes.add("trace")
    window.panes.set_on_top(second, True)
    settle(app)
    assert window.panes.on_top == {second}, "not the trace it was opened beside"
    assert window.panes.bars[second].pin.isChecked()
    assert not window.panes.bars["trace"].pin.isChecked()


# --- where a new one opens ------------------------------------------------------------
def test_a_pane_you_ask_for_opens_in_front_of_the_window(app, window):
    """Docking it takes the room the panes on screen were using, and somebody
    opening a second trace wants it beside what is there, not instead of it."""
    name = window.panes.add("trace", floating=True)
    settle(app)
    dock = window.panes.docks[name]
    assert dock.isFloating()
    assert dock.isVisible()
    assert dock.width() > 100 and dock.height() > 100, "a real window, not a sliver"


def test_it_carries_its_buttons_like_any_other_undocked_pane(app, window):
    name = window.panes.add("trace", floating=True)
    settle(app)
    assert window.panes.bars[name].isVisible()


def test_the_panes_the_window_opens_with_are_not_floated(app, window):
    """Where those go is the saved layout's business."""
    assert not any(window.panes.docks[n].isFloating() for n in ("trace", "log", "scope"))


def test_the_second_one_does_not_land_on_the_first(app, window):
    first = window.panes.add("trace", floating=True)
    second = window.panes.add("scope", floating=True)
    settle(app)
    assert window.panes.docks[first].pos() != window.panes.docks[second].pos(), (
        "or one window hides the other and looks like nothing happened"
    )


def test_a_restored_pane_is_placed_by_the_layout_and_not_floated(app, tmp_path, monkeypatch):
    """It was somewhere last time; that is where it goes."""
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    first = restart(app)
    name = first.panes.add("trace", floating=True)
    first.panes.docks[name].setFloating(False)  # docked by hand
    settle(app)

    second = restart(app, first)
    assert not second.panes.docks[name].isFloating(), "docking it was the last word"
    second.close()


# --- what comes back ---------------------------------------------------------------------
def test_an_extra_pane_comes_back_after_a_restart(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    first = restart(app)
    first.panes.add("trace")
    settle(app)

    second = restart(app, first)
    assert "trace 2" in second.panes.docks
    assert second.panes.docks["trace 2"].windowTitle() == "Trace 2"
    # Visible, which is the proof that the saved layout knew about it: the
    # panes are opened before restoreState so that it can place them, and a
    # pane it did not know about would still be hidden by the default arrangement.
    assert second.panes.docks["trace 2"].isVisible()
    second.close()


def test_it_comes_back_with_its_own_settings(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    first = restart(app)
    extra = first.panes.view(first.panes.add("trace"))
    extra._group_actions["PDO"].setChecked(False)
    settle(app)

    second = restart(app, first)
    again = second.panes.view("trace 2")
    assert again.hidden_groups() == {"PDO"}
    assert second.trace.hidden_groups() == set(), "the first one was never filtered"
    second.close()


def test_a_removed_pane_does_not_come_back(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    first = restart(app)
    first.panes.remove(first.panes.add("trace"))
    settle(app)

    second = restart(app, first)
    assert "trace 2" not in second.panes.docks
    second.close()


def test_opening_a_pane_does_not_forget_what_was_pinned(app, tmp_path, monkeypatch):
    """The instances are written while the window is still being built.

    Saving them and the pinned list in one call wrote an empty list over the
    saved one before anything had had the chance to read it back.
    """
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    first = restart(app)
    first.panes.docks["canopen"].setVisible(True)
    first.panes.docks["canopen"].setFloating(True)
    first.panes.set_on_top("canopen", True)
    first.panes.add("trace")
    settle(app)

    second = restart(app, first)
    assert "canopen" in second.panes.on_top
    second.close()


# --- the View menu ------------------------------------------------------------------------
def _submenu(window, title):
    for action in window.view_menu.actions():
        if action.menu() is not None and action.text() == title:
            return action.menu()
    raise AssertionError(f"no {title} submenu")


def test_only_the_kinds_that_mean_something_twice_are_offered(window):
    offered = {a.text() for a in _submenu(window, "New pane").actions()}
    assert offered == {"Trace", "Signals and Plot"}


def test_every_pane_is_listed_by_its_own_title(app, window):
    window.panes.add("trace")
    settle(app)
    listed = [a.text() for a in window.view_menu.actions()]
    assert "Trace" in listed and "Trace 2" in listed


def test_there_is_nothing_to_remove_until_there_is(app, window):
    remove = _submenu(window, "Remove pane")
    assert not remove.isEnabled(), "the fixed panes are not removable"

    window.panes.add("trace")
    settle(app)
    remove = _submenu(window, "Remove pane")
    assert remove.isEnabled()
    assert [a.text() for a in remove.actions()] == ["Trace 2"]
