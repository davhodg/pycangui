"""Undocked panes: left as Qt makes them, with two things they can be asked."""

import pytest
from PySide6.QtCore import QSettings, Qt

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


def float_out(app, dock):
    dock.setVisible(True)
    dock.setFloating(True)
    settle(app)


def submenu_titles(window):
    return [action.text() for action in window._undocked_menu.actions() if action.menu()]


def entries_for(window, title):
    for action in window._undocked_menu.actions():
        if action.text() == title and action.menu():
            return [a.text() for a in action.menu().actions()]
    return []


# --- floating is Qt's own, untouched --------------------------------------------------
def test_floating_is_left_as_qt_makes_it(app, window):
    """Promotion is gone: Qt's floating pane docks back readily and stays on top.

    Making it a plain window bought a maximise button and cost both of those,
    which was the wrong trade.  Detaching is where a real window lives now.
    """
    dock = window._docks["canopen"]
    float_out(app, dock)
    assert dock.isFloating()
    assert not dock.windowFlags() & Qt.WindowStaysOnTopHint, "not until it is asked for"


def test_docking_again_still_works(app, window):
    dock = window._docks["canopen"]
    float_out(app, dock)
    dock.setFloating(False)
    settle(app)
    assert not dock.isFloating()
    assert window.dockWidgetArea(dock) != Qt.NoDockWidgetArea


# --- the menu appears only when there is something to use it on ----------------------
def test_the_menu_is_empty_until_a_pane_is_undocked(app, window):
    assert not window._undocked_menu.isEnabled()
    assert submenu_titles(window) == []

    float_out(app, window._docks["canopen"])
    assert window._undocked_menu.isEnabled()
    assert submenu_titles(window) == ["CANopen"]
    assert "Always on top" in entries_for(window, "CANopen")
    assert "Detach into its own window" in entries_for(window, "CANopen")


def test_it_empties_again_when_the_pane_goes_back(app, window):
    dock = window._docks["canopen"]
    float_out(app, dock)
    dock.setFloating(False)
    settle(app)
    assert not window._undocked_menu.isEnabled()


def test_the_ctrl_tip_is_said_once(app, window):
    float_out(app, window._docks["canopen"])
    assert "Hold Ctrl" in window.log.toPlainText()
    before = window.log.toPlainText().count("Hold Ctrl")

    float_out(app, window._docks["uds"])
    assert window.log.toPlainText().count("Hold Ctrl") == before, "said once, not per pane"


# --- always on top --------------------------------------------------------------------
def test_always_on_top_is_applied(app, window):
    dock = window._docks["canopen"]
    float_out(app, dock)
    window._set_pane_on_top("canopen", True)
    settle(app)
    assert dock.windowFlags() & Qt.WindowStaysOnTopHint

    window._set_pane_on_top("canopen", False)
    settle(app)
    assert not dock.windowFlags() & Qt.WindowStaysOnTopHint


def test_always_on_top_survives_qt_resetting_the_flags(app, window):
    """Qt re-applies its own flags at the end of a drag, over the top of ours."""
    dock = window._docks["canopen"]
    float_out(app, dock)
    window._set_pane_on_top("canopen", True)
    settle(app)

    dock.setWindowFlags(Qt.Tool | Qt.WindowTitleHint)  # what Qt does when a drag ends
    dock.show()
    settle(app)
    assert dock.windowFlags() & Qt.WindowStaysOnTopHint, "it has to be put back"


def test_a_pane_being_dragged_is_left_alone(app, window):
    dock = window._docks["canopen"]
    float_out(app, dock)
    window._set_pane_on_top("canopen", True)
    settle(app)

    dock.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint)  # mid-drag, as Qt has it
    dock.show()
    settle(app)
    assert dock.windowFlags() & Qt.FramelessWindowHint, "the drag must not be interrupted"


# --- detaching ------------------------------------------------------------------------
def test_detaching_gives_the_pane_a_window_with_no_parent(app, window):
    """An owned window is the thing Windows keeps out of the taskbar."""
    float_out(app, window._docks["canopen"])
    window._detach_pane("canopen")
    settle(app)

    detached = window._detached["canopen"]
    assert detached.parent() is None, "an owner is what costs it the taskbar entry"
    assert detached.isVisible()
    assert detached.pane.isAncestorOf(window.canopen_view), "the pane moved, not a copy"
    # Checking the window and not the pane inside it is how a window with a
    # title, a taskbar entry and nothing in it got through: Qt hides a widget
    # when its parent changes, and showing the window does not undo that.
    assert detached.pane.isVisible(), "a detached pane must not be a blank window"
    assert not detached.pane.isHidden()
    assert window._docks["canopen"].widget() is None
    assert not window._docks["canopen"].isVisible()


def test_closing_a_detached_pane_closes_it(app, window):
    """Closing a window means closing it, as it does for a docked pane.

    The widget still goes home to its dock, so the View menu can show it
    again -- but a pane that reappeared in the main window because you had
    shut it would be answering a question nobody asked.
    """
    float_out(app, window._docks["canopen"])
    window._detach_pane("canopen")
    settle(app)

    window._detached["canopen"].close()
    settle(app)
    dock = window._docks["canopen"]
    assert "canopen" not in window._detached
    assert not dock.isVisible(), "closed means closed, not docked"
    assert not dock.toggleViewAction().isChecked(), "and the View menu agrees"
    assert dock.widget() is not None, "the pane went home even so"
    assert not dock.isFloating()


def test_the_view_menu_can_show_it_again_after_that(app, window):
    float_out(app, window._docks["canopen"])
    window._detach_pane("canopen")
    settle(app)
    window._detached["canopen"].close()
    settle(app)

    dock = window._docks["canopen"]
    dock.toggleViewAction().trigger()
    settle(app)
    assert dock.isVisible()
    assert dock.widget().isVisible(), "and the pane inside it, not a blank dock"
    assert window.dockWidgetArea(dock) != Qt.NoDockWidgetArea


def test_putting_it_back_from_the_menu_shows_it(app, window):
    """Unlike closing: asking for it back means you want to see it."""
    float_out(app, window._docks["canopen"])
    window._detach_pane("canopen")
    settle(app)

    window._restore_pane("canopen")
    settle(app)
    dock = window._docks["canopen"]
    assert "canopen" not in window._detached
    assert dock.isVisible() and dock.widget().isVisible()
    assert not dock.isFloating()
    assert window.dockWidgetArea(dock) != Qt.NoDockWidgetArea


def test_the_pane_is_still_shown_after_a_round_trip(app, window):
    """Out and back twice: each reparent hides it again, so each needs undoing."""
    for _ in range(2):
        float_out(app, window._docks["canopen"])
        window._detach_pane("canopen")
        settle(app)
        assert window._detached["canopen"].pane.isVisible()
        window._restore_pane("canopen")
        settle(app)
        assert window._docks["canopen"].widget().isVisible()


def test_a_detached_pane_can_be_kept_on_top(app, window):
    float_out(app, window._docks["canopen"])
    window._set_pane_on_top("canopen", True)
    window._detach_pane("canopen")
    settle(app)
    assert window._detached["canopen"].windowFlags() & Qt.WindowStaysOnTopHint


def test_the_menu_offers_the_way_back_while_detached(app, window):
    float_out(app, window._docks["canopen"])
    window._detach_pane("canopen")
    settle(app)
    entries = entries_for(window, "CANopen")
    assert "Put back in the window" in entries
    assert "Detach into its own window" not in entries, "it already is"


def test_dock_all_panes_collects_detached_ones_too(app, window):
    float_out(app, window._docks["canopen"])
    float_out(app, window._docks["uds"])
    window._detach_pane("canopen")
    settle(app)

    window._dock_all()
    settle(app)
    assert not window._detached
    assert not any(d.isFloating() for d in window._docks.values())


def test_closing_the_main_window_closes_detached_panes(app, tmp_path, monkeypatch):
    """They have no parent, so they would keep the application running."""
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    win = MainWindow()
    win.show()
    app.processEvents()
    win._docks["canopen"].setVisible(True)
    win._docks["canopen"].setFloating(True)
    app.processEvents()
    win._detach_pane("canopen")
    app.processEvents()
    detached = win._detached["canopen"]

    win.close()
    app.processEvents()
    assert not detached.isVisible()
    assert not win._detached


# --- the buttons, where the pane is ---------------------------------------------------
def test_the_buttons_are_hidden_while_the_pane_is_docked(app, window):
    """None of them apply to a docked pane, so none of them are shown."""
    window._docks["canopen"].setVisible(True)
    settle(app)
    assert not window._bars["canopen"].isVisible()


def test_undocking_shows_them(app, window):
    float_out(app, window._docks["canopen"])
    bar = window._bars["canopen"]
    assert bar.isVisible()
    assert bar.pin.isVisible() and bar.detach.isVisible() and bar.dock.isVisible()


def test_the_pin_button_keeps_the_pane_on_top(app, window):
    dock = window._docks["canopen"]
    float_out(app, dock)
    window._bars["canopen"].pin.setChecked(True)
    settle(app)
    assert dock.windowFlags() & Qt.WindowStaysOnTopHint


def test_the_dock_button_docks_it(app, window):
    dock = window._docks["canopen"]
    float_out(app, dock)
    window._bars["canopen"].dock.click()
    settle(app)
    assert not dock.isFloating()
    assert not window._bars["canopen"].isVisible(), "and the buttons go with it"


def test_the_detach_button_detaches_it(app, window):
    float_out(app, window._docks["canopen"])
    window._bars["canopen"].detach.click()
    settle(app)
    assert "canopen" in window._detached


def test_the_strip_travels_with_the_pane_and_offers_the_way_back(app, window):
    """It is part of the pane's own content, so detaching carries it along."""
    float_out(app, window._docks["canopen"])
    window._detach_pane("canopen")
    settle(app)

    bar = window._bars["canopen"]
    assert bar.isVisible(), "still there in the window of its own"
    assert not bar.detach.isVisible(), "it already is detached"
    assert bar.dock.isVisible()

    bar.dock.click()
    settle(app)
    assert "canopen" not in window._detached
    assert window._docks["canopen"].isVisible()
    assert not bar.isVisible()


def test_the_pin_button_follows_the_pane_out_to_its_own_window(app, window):
    float_out(app, window._docks["canopen"])
    window._bars["canopen"].pin.setChecked(True)
    window._detach_pane("canopen")
    settle(app)
    assert window._detached["canopen"].windowFlags() & Qt.WindowStaysOnTopHint
    assert window._bars["canopen"].pin.isChecked()


# --- pinning must not empty the window it is pinning ----------------------------------
def test_pinning_a_detached_pane_keeps_its_contents(app, window):
    """Changing a window flag rebuilds the window and hides what is in it.

    Worse, it hides the window itself, so a visibility check *after* the change
    is always told it is hidden -- nothing was shown again, and pressing Pin
    emptied the window, taking the button that had just been pressed with it.
    """
    float_out(app, window._docks["canopen"])
    window._detach_pane("canopen")
    settle(app)
    detached = window._detached["canopen"]
    bar = window._bars["canopen"]

    for wanted in (True, False, True):
        bar.pin.setChecked(wanted)
        settle(app)
        assert bool(detached.windowFlags() & Qt.WindowStaysOnTopHint) is wanted
        assert detached.isVisible(), "the window itself must survive being pinned"
        assert detached.pane.isVisible(), "and what is in it"
        assert bar.isVisible(), "including the button that was just pressed"
        assert bar.pin.isVisible() and bar.dock.isVisible()


def test_pinning_a_floating_pane_keeps_its_contents(app, window):
    dock = window._docks["canopen"]
    float_out(app, dock)
    bar = window._bars["canopen"]

    for wanted in (True, False, True):
        bar.pin.setChecked(wanted)
        settle(app)
        assert bool(dock.windowFlags() & Qt.WindowStaysOnTopHint) is wanted
        assert dock.widget().isVisible()
        assert bar.isVisible()


def test_pinning_one_of_several_undocked_panes_leaves_the_others(app, window):
    canopen, uds = window._docks["canopen"], window._docks["uds"]
    float_out(app, canopen)
    float_out(app, uds)

    window._bars["canopen"].pin.setChecked(True)
    settle(app)
    assert canopen.windowFlags() & Qt.WindowStaysOnTopHint
    assert not uds.windowFlags() & Qt.WindowStaysOnTopHint, "one pin, one pane"
    assert window._bars["uds"].isVisible() and not window._bars["uds"].pin.isChecked()
