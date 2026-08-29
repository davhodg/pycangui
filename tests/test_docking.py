"""Undocked panes: left as Qt makes them, with two buttons on the pane itself."""

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


# --- floating is Qt's own, untouched --------------------------------------------------
def test_floating_is_left_as_qt_makes_it(app, window):
    """Promoting a floated pane to a plain window cost more than it bought."""
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


# --- the Ctrl tip ---------------------------------------------------------------------
def test_the_ctrl_tip_is_said_once(app, window):
    float_out(app, window._docks["canopen"])
    assert "Hold Ctrl" in window.log.toPlainText()
    before = window.log.toPlainText().count("Hold Ctrl")

    float_out(app, window._docks["uds"])
    assert window.log.toPlainText().count("Hold Ctrl") == before, "said once, not per pane"


def test_a_hidden_floating_pane_is_not_worth_a_tip(app, tmp_path, monkeypatch):
    """A pane undocked and then closed is restored floating but hidden.

    That said "hold Ctrl while dragging" at every start-up, with nothing on
    screen to say it about.
    """
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    first = MainWindow()
    first.show()
    settle(app)
    dock = first._docks["canopen"]
    float_out(app, dock)
    dock.close()  # undocked, then shut
    settle(app)
    first.close()  # saves the layout

    second = MainWindow()
    second.show()
    settle(app)
    restored = second._docks["canopen"]
    assert restored.isFloating() and not restored.isVisible(), "the case in question"
    assert "Hold Ctrl" not in second.log.toPlainText()
    assert not second._bars["canopen"].isVisible(), "and no buttons for a pane nobody sees"
    second.close()


# --- the buttons ----------------------------------------------------------------------
def test_the_buttons_are_hidden_while_the_pane_is_docked(app, window):
    window._docks["canopen"].setVisible(True)
    settle(app)
    assert not window._bars["canopen"].isVisible()


def test_undocking_shows_them(app, window):
    float_out(app, window._docks["canopen"])
    bar = window._bars["canopen"]
    assert bar.isVisible()
    assert bar.pin.text() == "Pin"
    assert bar.move_button.text() == "Detach"


def test_each_button_says_what_pressing_it_will_do(app, window):
    """Pin becomes Unpin, Detach becomes Attach: no reading it twice."""
    float_out(app, window._docks["canopen"])
    bar = window._bars["canopen"]

    bar.pin.setChecked(True)
    settle(app)
    assert bar.pin.text() == "Unpin"
    bar.pin.setChecked(False)
    settle(app)
    assert bar.pin.text() == "Pin"

    bar.move_button.click()  # Detach
    settle(app)
    assert bar.move_button.text() == "Attach"
    bar.move_button.click()  # Attach
    settle(app)
    assert "canopen" not in window._detached
    assert bar.move_button.text() == "Detach"
    # Back where it came from, which was floating, so it is still out and
    # still carries its buttons.
    assert bar.isVisible()


def test_the_buttons_explain_themselves_on_hover(app, window):
    """Qt tooltips: what DVT called the balloon."""
    float_out(app, window._docks["canopen"])
    bar = window._bars["canopen"]
    assert "above every other window" in bar.pin.toolTip()
    assert "taskbar" in bar.move_button.toolTip()

    bar.move_button.click()
    settle(app)
    assert "back where it came from" in bar.move_button.toolTip(), "and change with it"


def test_the_pin_button_keeps_the_pane_on_top(app, window):
    dock = window._docks["canopen"]
    float_out(app, dock)
    window._bars["canopen"].pin.setChecked(True)
    settle(app)
    assert dock.windowFlags() & Qt.WindowStaysOnTopHint


def test_detaching_says_nothing_in_the_log(app, window):
    """A window appearing is its own announcement."""
    float_out(app, window._docks["canopen"])
    before = window.log.toPlainText()
    window._bars["canopen"].move_button.click()
    settle(app)
    assert window.log.toPlainText() == before


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
    # Checking the window and not what is inside it is how a window with a
    # title, a taskbar entry and nothing in it got through.
    assert detached.pane.isVisible(), "a detached pane must not be a blank window"
    assert window._docks["canopen"].widget() is None
    assert not window._docks["canopen"].isVisible()


def test_closing_a_detached_pane_closes_it(app, window):
    """Closing a window means closing it, as it does for a docked pane."""
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


def test_attaching_from_the_button_shows_it(app, window):
    float_out(app, window._docks["canopen"])
    window._detach_pane("canopen")
    settle(app)

    window._bars["canopen"].move_button.click()
    settle(app)
    dock = window._docks["canopen"]
    assert "canopen" not in window._detached
    assert dock.isVisible() and dock.widget().isVisible()
    assert dock.isFloating(), "it was floating before it was detached"


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
    float_out(app, win._docks["canopen"])
    win._detach_pane("canopen")
    app.processEvents()
    detached = win._detached["canopen"]

    win.close()
    app.processEvents()
    assert not detached.isVisible()
    assert not win._detached


# --- pinning must not empty the window it is pinning ----------------------------------
def test_pinning_a_detached_pane_keeps_its_contents(app, window):
    """Changing a window flag rebuilds the window and hides what is in it.

    Worse, it hides the window itself, so a visibility check *after* the
    change is always told it is hidden -- nothing was shown again, and
    pressing Pin emptied the window, taking the button with it.
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


# --- what survives a restart ----------------------------------------------------------
def restart(app, tmp_path, previous=None):
    """Close a window if given, then open a fresh one on the same settings."""
    if previous is not None:
        previous.close()
        settle(app)
    window = MainWindow()
    window.show()
    settle(app, 10)
    return window


def test_a_pane_restored_floating_has_its_buttons(app, tmp_path, monkeypatch):
    """A restored pane is shown after topLevelChanged says it is floating.

    Asking then found it invisible and left it with no buttons at all, on a
    pane sitting in plain sight.
    """
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    first = restart(app, tmp_path)
    float_out(app, first._docks["canopen"])
    first._bars["canopen"].pin.setChecked(True)
    settle(app)

    second = restart(app, tmp_path, first)
    assert second._docks["canopen"].isFloating() and second._docks["canopen"].isVisible()
    assert second._bars["canopen"].isVisible(), "an undocked pane must carry its buttons"
    assert second._bars["canopen"].pin.isChecked(), "and remember it was pinned"
    assert second._bars["canopen"].pin.text() == "Unpin"
    second.close()


def test_a_detached_pane_comes_back_detached(app, tmp_path, monkeypatch):
    """It came back closed: putting it away on the way out was the last thing
    saved about it."""
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    first = restart(app, tmp_path)
    float_out(app, first._docks["canopen"])
    first._detach_pane("canopen")
    settle(app)

    second = restart(app, tmp_path, first)
    assert "canopen" in second._detached, "left on another screen, so put it back there"
    assert second._detached["canopen"].isVisible()
    assert second._detached["canopen"].pane.isVisible()
    assert second._bars["canopen"].isVisible()
    second.close()


def test_attaching_returns_a_pane_to_where_it_was(app, window):
    """It was undocked before it was detached, so that is where it goes back."""
    dock = window._docks["canopen"]
    float_out(app, dock)
    window._detach_pane("canopen")
    settle(app)

    window._bars["canopen"].move_button.click()  # Attach
    settle(app)
    assert dock.isFloating(), "the main window is not where it came from"
    assert dock.isVisible()
    assert window._bars["canopen"].isVisible(), "still out, so still has its buttons"


def test_attaching_a_pane_detached_from_docked_docks_it(app, window):
    """And one that was docked goes back to being docked."""
    dock = window._docks["canopen"]
    dock.setVisible(True)
    settle(app)
    window._detach_pane("canopen")
    settle(app)

    window._restore_pane("canopen")
    settle(app)
    assert not dock.isFloating()
    assert dock.isVisible()
    assert not window._bars["canopen"].isVisible(), "docked panes carry no buttons"
