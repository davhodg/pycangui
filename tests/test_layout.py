"""The layout a first run opens with."""

import pytest
from PySide6.QtCore import QSettings, Qt

from pycangui.ui.main_window import DEFAULT_VISIBLE, MainWindow


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()  # a first run, whatever ran before
    win = MainWindow()
    win.resize(1400, 900)
    win.show()  # a dock of a window that was never shown reports itself hidden
    app.processEvents()
    yield win
    win.close()


def test_only_three_panes_are_open_to_start_with(app, window):
    """Nine panes at once is a wall; the rest are one click away in View."""
    visible = {name for name, dock in window._docks.items() if dock.isVisible()}
    assert visible == set(DEFAULT_VISIBLE) == {"trace", "log", "scope"}


def test_they_are_stacked_in_one_column(app, window):
    tops = [window._docks[n].geometry().y() for n in ("trace", "log", "scope")]
    assert tops == sorted(tops), "trace above the log, the log above signals and plot"
    assert window._docks["trace"].geometry().height() > window._docks["log"].geometry().height()


def test_a_hidden_pane_comes_back_where_it_belongs(app, window):
    dock = window._docks["canopen"]
    assert not dock.isVisible()
    dock.toggleViewAction().trigger()  # what the View menu does
    app.processEvents()
    assert dock.isVisible()
    assert window.dockWidgetArea(dock) == Qt.RightDockWidgetArea


def test_reset_layout_puts_the_extra_panes_away_again(app, window):
    window._docks["canopen"].toggleViewAction().trigger()
    window._docks["console"].toggleViewAction().trigger()
    app.processEvents()
    window._reset_layout()
    app.processEvents()
    visible = {name for name, dock in window._docks.items() if dock.isVisible()}
    assert visible == set(DEFAULT_VISIBLE)


def test_the_log_says_where_the_hidden_panes_went(app, window):
    text = window.log.toPlainText()
    assert "View menu" in text
    for title in ("CANopen", "UDS", "J1939", "XCP", "Transmit", "Python Console"):
        assert title in text
