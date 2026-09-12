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


def test_only_the_general_panes_are_open_to_start_with(app, window):
    """Eleven panes at once is a wall.  These four apply whatever is on the
    bus; the protocol ones depend on what you have plugged in, and are one
    click away in View."""
    visible = {name for name, dock in window.panes.docks.items() if dock.isVisible()}
    assert visible == set(DEFAULT_VISIBLE) == {"trace", "tx", "scope", "log"}


def test_the_default_layout_is_the_one_drawn_in_arrange_default(app, window):
    """Trace above transmit on the left, the log beside them, the plot
    across the bottom.  Watching the bus and talking to it are the same
    job; the log is glanced at rather than worked in."""
    trace, tx, log, scope = (
        window.panes.docks[n].geometry() for n in ("trace", "tx", "log", "scope")
    )
    assert tx.y() > trace.y(), "the transmit list is under the trace..."
    assert tx.x() == trace.x(), "...in the same column"
    assert log.x() > trace.x(), "the log is to the right of both..."
    assert log.y() == trace.y(), "...spanning the rows they share"
    assert scope.y() > tx.y(), "signals and plot go below everything"
    assert scope.width() > trace.width(), "spanning the full width"


def test_the_transmit_list_gets_less_room_than_the_trace(app, window):
    """A transmit list is a short list of messages; a trace is an endless
    one, and it is what you are reading."""
    trace, tx = (window.panes.docks[n].geometry() for n in ("trace", "tx"))
    assert trace.height() > tx.height()


def test_neither_column_swallows_the_window(app, window):
    """The complaint this layout answers: the trace used to take most of
    it, so everything else was a strip."""
    trace, log = (window.panes.docks[n].geometry() for n in ("trace", "log"))
    assert 0.7 < trace.width() / log.width() < 1.4, "the two columns are about even"


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
    for title in ("CANopen", "UDS", "J1939", "XCP", "ASCII Log", "Python Console"):
        assert title in text
