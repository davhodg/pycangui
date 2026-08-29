"""Undocking a pane makes it a window, and docking it again is unchanged."""

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
    """Promotion is deferred a turn, so let the event loop run."""
    for _ in range(times):
        app.processEvents()


def window_type(dock):
    return dock.windowFlags() & Qt.WindowType_Mask


def float_out(app, dock):
    dock.setVisible(True)
    dock.setFloating(True)
    settle(app)


# --- undocking -----------------------------------------------------------------------
def test_an_undocked_pane_becomes_a_real_window(app, window):
    """Qt floats a dock as a tool window: no maximise button, no taskbar entry."""
    dock = window._docks["canopen"]
    float_out(app, dock)

    assert dock.isFloating()
    assert window_type(dock) == Qt.Window, "a tool window is not what undocking means"
    flags = dock.windowFlags()
    assert flags & Qt.WindowMaximizeButtonHint, "the button that was missing"
    assert flags & Qt.WindowMinimizeButtonHint
    assert dock.isVisible(), "setWindowFlags hides a window; it has to be shown again"


def test_it_can_actually_maximise(app, window):
    dock = window._docks["canopen"]
    float_out(app, dock)
    dock.showMaximized()
    settle(app)
    assert dock.isMaximized()
    dock.showNormal()


# --- docking again, which must be exactly as it was -----------------------------------
def test_docking_again_needs_nothing_undone(app, window):
    """The condition on all of this: the way back is unchanged.

    Qt reparents the pane and restores the flags itself, so nothing promotion
    did has to be reversed.
    """
    dock = window._docks["canopen"]
    float_out(app, dock)
    assert window_type(dock) == Qt.Window

    dock.setFloating(False)
    settle(app)
    assert not dock.isFloating()
    assert window_type(dock) == Qt.Widget, "back to being a child of the main window"
    assert window.dockWidgetArea(dock) != Qt.NoDockWidgetArea
    assert dock.isVisible()


def test_the_cycle_survives_repeating(app, window):
    dock = window._docks["uds"]
    for _ in range(3):
        float_out(app, dock)
        assert window_type(dock) == Qt.Window
        dock.setFloating(False)
        settle(app)
        assert window_type(dock) == Qt.Widget
    assert window.dockWidgetArea(dock) != Qt.NoDockWidgetArea


def test_dock_all_panes_brings_every_one_back(app, window):
    """A window of its own can end up behind the main one; this is the way back."""
    docks = [window._docks[name] for name in ("canopen", "uds", "xcp")]
    for dock in docks:
        float_out(app, dock)
    assert all(d.isFloating() for d in docks)

    window._dock_all()
    settle(app)
    assert not any(d.isFloating() for d in docks)
    assert all(window.dockWidgetArea(d) != Qt.NoDockWidgetArea for d in docks)
    assert "Docked 3 pane(s)" in window.log.toPlainText()


def test_dock_all_panes_says_so_when_there_is_nothing_to_do(app, window):
    window._dock_all()
    assert "No panes are undocked" in window.log.toPlainText()


def test_a_pane_docked_before_the_promotion_lands_is_left_alone(app, window):
    """Promotion is deferred, so the pane may be docked again before it runs."""
    dock = window._docks["canopen"]
    dock.setVisible(True)
    dock.setFloating(True)
    dock.setFloating(False)  # no event loop in between
    settle(app)
    assert not dock.isFloating()
    assert window_type(dock) == Qt.Widget, "it must not be promoted while docked"
