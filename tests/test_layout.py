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
    visible = {name for name, dock in window.panes.docks.items() if dock.isVisible()}
    assert visible == set(DEFAULT_VISIBLE) == {"trace", "log", "scope"}


def test_the_log_sits_beside_the_trace_with_the_plot_below(app, window):
    trace, log, scope = (window.panes.docks[n].geometry() for n in ("trace", "log", "scope"))
    assert log.x() > trace.x(), "the log is to the right of the trace..."
    assert log.y() == trace.y(), "...sharing the top row with it"
    assert trace.width() > log.width(), "and the trace gets the greater share of the width"
    assert scope.y() > trace.y(), "signals and plot go below both"
    assert scope.width() > trace.width(), "spanning the full width"


def test_a_hidden_pane_comes_back_where_it_belongs(app, window):
    dock = window.panes.docks["canopen"]
    assert not dock.isVisible()
    dock.toggleViewAction().trigger()  # what the View menu does
    app.processEvents()
    assert dock.isVisible()
    assert window.dockWidgetArea(dock) == Qt.RightDockWidgetArea


def test_reset_layout_puts_the_extra_panes_away_again(app, window):
    window.panes.docks["canopen"].toggleViewAction().trigger()
    window.panes.docks["console"].toggleViewAction().trigger()
    app.processEvents()
    window._reset_layout()
    app.processEvents()
    visible = {name for name, dock in window.panes.docks.items() if dock.isVisible()}
    assert visible == set(DEFAULT_VISIBLE)


def test_the_log_says_where_the_hidden_panes_went(app, window):
    text = window.log.toPlainText()
    assert "View menu" in text
    for title in ("CANopen", "UDS", "J1939", "XCP", "CAN Transmit", "Python Console"):
        assert title in text
