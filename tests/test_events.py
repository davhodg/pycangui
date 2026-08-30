"""Nothing said to the user is lost because a pane was closed.

The Event Log is a pane like any other and can be shut.  Until it carried a
level, shutting it meant "throw away anything you were going to tell me":
press Add from DBC with no database, or tick Cyclic with no bus, and the
button appeared to do nothing at all.
"""

import pytest
from PySide6.QtCore import QSettings

from pycangui.core.context import Context
from pycangui.core.events import ERROR, INFORMATION, WARNING, EventLog
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


# --- the levels themselves ------------------------------------------------------------
def test_a_level_reaches_whoever_is_listening(app):
    events = EventLog()
    seen = []
    events.posted.connect(lambda message, level: seen.append((message, level)))

    events.information("a note")
    events.warning("did not happen")
    events.error("went wrong")
    assert seen == [("a note", INFORMATION), ("did not happen", WARNING), ("went wrong", ERROR)]


def test_a_level_nobody_recognises_is_only_a_note(app):
    """A typo in somebody's hook must not be able to make the pane spring open."""
    events = EventLog()
    seen = []
    events.posted.connect(lambda message, level: seen.append(level))
    events.post("hello", "URGENT!!")
    assert seen == [INFORMATION], "and the message still arrives"


def test_a_context_keeps_its_plain_sink(app, tmp_path, monkeypatch):
    """Scripts and hooks call ctx.log(text) and should carry on doing so."""
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    lines = []
    ctx = Context(log=lines.append)
    ctx.log("a note")
    ctx.warn("did not happen")
    assert lines == ["a note", "did not happen"], "the level is the window's business"


# --- the pane -------------------------------------------------------------------------
def test_a_note_does_not_reopen_a_closed_log(app, window):
    """Closing it has to keep meaning "stop chattering at me"."""
    dock = window._docks["log"]
    dock.close()
    settle(app)
    assert not dock.isVisible()

    window.events.information("something happened")
    settle(app)
    assert not dock.isVisible()
    assert "something happened" in window.log.toPlainText(), "recorded even so"


@pytest.mark.parametrize("level", (WARNING, ERROR))
def test_a_problem_opens_it(app, window, level):
    dock = window._docks["log"]
    dock.close()
    settle(app)

    window.events.post("that did not work", level)
    settle(app)
    assert dock.isVisible(), f"a {level} with nowhere to appear is a button that does nothing"
    assert "that did not work" in window.log.toPlainText()


def test_a_burst_of_problems_opens_it_once(app, window):
    """Ticking Cyclic on a selection with no bus is one warning per row."""
    dock = window._docks["log"]
    dock.close()
    settle(app)
    for row in range(20):
        window.events.warning(f"row {row}: no bus")
    assert window._surfacing, "still pending, so twenty warnings cost one show"
    settle(app)
    assert dock.isVisible()
    assert not window._surfacing


def test_a_problem_while_the_window_is_still_opening_is_not_lost(app, tmp_path, monkeypatch):
    """The saved layout is restored during __init__, after such a warning.

    Showing the pane there and then would simply be undone by it, which is
    why the show waits for the event loop.
    """
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    first = MainWindow()
    first.show()
    settle(app)
    first._docks["log"].close()
    settle(app)
    first.close()  # saves a layout with the Event Log hidden

    second = MainWindow()
    second.show()
    settle(app)
    assert not second._docks["log"].isVisible(), "the saved layout is honoured"
    second.events.warning("a problem at startup")
    settle(app)
    assert second._docks["log"].isVisible()
    second.close()


def test_the_pane_is_written_to_in_one_place(app, window):
    """So that colouring warnings later is a change there and nowhere else."""
    import inspect

    from pycangui.ui import main_window

    source = inspect.getsource(main_window)
    assert source.count("self.log.appendPlainText") == 1


# --- what now says it properly --------------------------------------------------------
def test_a_row_that_cannot_be_sent_is_a_warning(app, window):
    """Cyclic with no bus connected: the row unticks and you are told why."""
    from PySide6.QtCore import Qt

    from pycangui.ui.tx_view import COL_CYCLIC, DEFAULT_RAW

    dock = window._docks["log"]
    dock.close()
    settle(app)
    row = window.tx.add_message(dict(DEFAULT_RAW))
    window.tx.item(row).setCheckState(COL_CYCLIC, Qt.Checked)
    settle(app)

    assert dock.isVisible(), "it did not start, and that has to be visible somewhere"
    assert window.tx.item(row).checkState(COL_CYCLIC) != Qt.Checked


def test_the_dbc_picker_explains_itself_rather_than_the_log(app, window):
    """The button used to log a line and open nothing, which from the outside
    is indistinguishable from a button that does not work."""
    from pycangui.ui.tx_view import MessagePicker

    picker = MessagePicker(window.dbc, window)
    text = " ".join(picker.list.item(i).text() for i in range(picker.list.count()))
    assert "No database loaded" in text
    assert "Load DBC" in text, "and where to go about it"
    assert not picker.list.isEnabled()
    assert picker.chosen() is None, "so Ok cannot add one of the explanations"
    picker.deleteLater()
