# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Reading the same objects over and over, and saying how fast that really went.

The requested rate is a ceiling, not a promise. An SDO read is a request and a
response on the bus against a controller that answers when it feels like it, so
asking for twelve objects at 100 Hz is asking for twelve hundred round trips a
second. Two things follow, and both are tested here: the rounds do not queue
up behind each other, and the rate reported is the one achieved rather than the
one typed.
"""

import time

import pytest
from PySide6.QtCore import QObject, QSettings, QTimer, Signal

from pycangui.canopen.display import Display
from pycangui.custom_panes.model import CustomPane, Field, save
from pycangui.custom_panes.polling import Poller, rate_text
from pycangui.ui.main_window import MainWindow


def pump(app, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.002)


def settle(app, times=5):
    for _ in range(times):
        app.processEvents()


# --- the poller on its own ------------------------------------------------------------
@pytest.fixture
def poller(app):
    made = Poller()
    asked: list[tuple[int, int]] = []
    made.read.connect(lambda index, sub: asked.append((index, sub)))
    made.set_objects([(0x2001, 0), (0x2002, 0)])
    yield made, asked
    made.stop()


def test_it_asks_for_everything_it_was_given(app, poller):
    made, asked = poller
    made.start(50)
    settle(app)
    assert asked == [(0x2001, 0), (0x2002, 0)]


def test_a_round_does_not_start_until_the_last_one_finished(app, poller):
    """Firing faster than the answers come back builds a backlog that grows
    until the tool is showing values from a minute ago -- and it looks exactly
    like a slow bus rather than like a mistake."""
    made, asked = poller
    made.start(50)
    settle(app)
    assert len(asked) == 2

    pump(app, 0.2)
    assert len(asked) == 2, "still waiting on the first round"

    made.answered(0x2001, 0)
    made.answered(0x2002, 0)
    pump(app, 0.1)
    assert len(asked) == 4, "and now the next one"


def test_one_object_answering_twice_does_not_start_a_round(app, poller):
    made, asked = poller
    made.start(50)
    settle(app)
    made.answered(0x2001, 0)
    made.answered(0x2001, 0)
    pump(app, 0.1)
    assert len(asked) == 2, "the other one has not answered"


def test_an_answer_for_something_else_is_ignored(app, poller):
    made, asked = poller
    made.start(50)
    settle(app)
    made.answered(0x9999, 0)
    pump(app, 0.1)
    assert len(asked) == 2


def test_stopping_stops(app, poller):
    made, asked = poller
    made.start(50)
    settle(app)
    made.stop()
    made.answered(0x2001, 0)
    made.answered(0x2002, 0)
    pump(app, 0.15)
    assert len(asked) == 2


def test_it_reports_what_it_achieved_and_not_what_was_asked(app, poller):
    """A value read at 12 Hz that looks like it was read at 100 is the sort of
    thing people build conclusions on."""
    made, _asked = poller
    seen: list[tuple[float, float]] = []
    made.rate.connect(lambda req, got: seen.append((req, got)))
    made.start(50)
    settle(app)
    # Two rounds, each taking about a tenth of a second to answer.
    for _ in range(2):
        pump(app, 0.1)
        made.answered(0x2001, 0)
        made.answered(0x2002, 0)
        settle(app)

    requested, achieved = seen[-1]
    assert requested == 50
    assert 5 < achieved < 20, f"about 10 Hz, not the 50 that was asked for: {achieved}"


def test_a_rate_the_bus_can_keep_up_with_is_reported_as_asked(app, poller):
    made, _asked = poller
    made.start(2)
    settle(app)
    made.answered(0x2001, 0)
    made.answered(0x2002, 0)
    settle(app)
    assert made.achieved == pytest.approx(2.0, rel=0.2), "not ten, just because it could"


def test_a_round_that_never_answers_does_not_wedge_it(app, poller, monkeypatch):
    """Every request normally produces exactly one answer, an abort included.
    This is for when something has gone wrong enough that one never arrives."""
    import pycangui.custom_panes.polling as polling

    monkeypatch.setattr(polling, "ROUND_TIMEOUT_S", 0.05)
    made, asked = poller
    made.start(50)
    settle(app)
    made.answered(0x2001, 0)  # the second one never comes

    pump(app, 0.3)
    assert len(asked) > 2, "it gave up and started again"


def test_nothing_to_read_is_not_a_reason_to_stop(app):
    """A pane that gains a field should start polling it without anybody
    having to press the button again."""
    made = Poller()
    asked: list[tuple[int, int]] = []
    made.read.connect(lambda index, sub: asked.append((index, sub)))
    made.start(50)
    pump(app, 0.1)
    assert made.running and asked == []

    made.set_objects([(0x2001, 0)])
    pump(app, 0.1)
    assert asked, "and now it reads it"
    made.stop()


def test_the_rate_is_clamped_to_what_the_box_offers(app):
    made = Poller()
    made.set_rate(10_000)
    assert made.hz <= 50
    made.set_rate(0)
    assert made.hz > 0


def test_duplicate_objects_are_read_once(app):
    made = Poller()
    made.set_objects([(0x2001, 0), (0x2001, 0), (0x2002, 0)])
    assert made.objects == [(0x2001, 0), (0x2002, 0)]


# --- what it says on screen -------------------------------------------------------------
def test_nothing_is_said_while_it_is_not_polling():
    assert rate_text(10, 0, running=False) == ""


def test_before_the_first_round_it_says_so():
    assert rate_text(10, 0, running=True) == "starting..."


def test_keeping_up_says_only_the_rate():
    """Repeating the number somebody just typed tells them nothing."""
    assert rate_text(10, 9.8, running=True) == "9.8 Hz"


def test_falling_short_says_so_plainly():
    assert rate_text(100, 12.0, running=True) == "12.0 Hz (asked for 100)"


def test_a_slow_rate_keeps_a_second_decimal():
    assert rate_text(0.5, 0.5, running=True) == "0.50 Hz"


# --- on a pane ---------------------------------------------------------------------------
class FakeNode(QObject):
    """A source that answers after a delay, the way a node does."""

    value = Signal(int, int, object, object)
    changed = Signal()
    label = "Node 5"
    writable = True
    live = True

    def __init__(self, delay_ms=10):
        super().__init__()
        self.delay_ms = delay_ms
        self.reads = 0

    def display(self, _index, _sub):
        return Display(unit="A", factor=0.1, decimals=1)

    def request(self, index, sub):
        self.reads += 1
        QTimer.singleShot(self.delay_ms, lambda: self.value.emit(index, sub, 1234, None))

    def write(self, *_a):
        pass


class FakeFile(FakeNode):
    live = False
    label = "drive.dcf"


@pytest.fixture
def pane(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    window = MainWindow()
    save(
        "battery",
        CustomPane(
            title="Battery limits",
            fields=[Field(index=0x2001, kind="value"), Field(index=0x2002, kind="value")],
        ),
    )
    window.open_custom_pane("battery")
    view = window._custom_pane_view("battery")
    yield window, view
    view.poller.stop()
    window.close()


def test_polling_is_not_offered_with_nothing_bound(app, pane):
    _window, view = pane
    assert not view.poll.isEnabled()


def test_polling_is_not_offered_against_a_file(app, pane):
    """A file does not change under you, so re-reading it has one answer."""
    _window, view = pane
    view.bind(FakeFile())
    settle(app)
    assert not view.poll.isEnabled()


def test_polling_a_node_reads_it_over_and_over(app, pane):
    _window, view = pane
    source = FakeNode()
    view.bind(source)
    settle(app)
    before = source.reads

    view.poll_hz.setValue(50.0)
    view.poll.setChecked(True)
    pump(app, 0.5)
    assert source.reads > before + 4, "several rounds of two objects"


def test_the_pane_shows_the_rate_it_is_managing(app, pane):
    _window, view = pane
    view.bind(FakeNode(delay_ms=50))
    settle(app)
    view.poll_hz.setValue(50.0)
    view.poll.setChecked(True)
    pump(app, 0.6)
    assert "asked for 50" in view.poll_rate.text(), view.poll_rate.text()


def test_stopping_clears_the_rate(app, pane):
    _window, view = pane
    view.bind(FakeNode())
    settle(app)
    view.poll.setChecked(True)
    pump(app, 0.2)
    view.poll.setChecked(False)
    settle(app)
    assert view.poll_rate.text() == ""


def test_a_polled_object_becomes_a_signal(app, pane):
    """So it plots and exports like any other, rather than being a number that
    only exists on this form."""
    window, view = pane
    view.bind(FakeNode())
    settle(app)
    view.poll_hz.setValue(20.0)  # the default is a couple a second
    view.poll.setChecked(True)
    pump(app, 0.5)

    keys = window.signals.keys()
    assert any("0x2001" in k for k in keys), keys
    series = window.signals.get(next(k for k in keys if "0x2001" in k))
    assert len(series.values) > 1, "a series, not one reading"
    assert series.unit == "A"
    assert series.values[0] == pytest.approx(123.4), "in its own units, as the pane shows it"


def test_reading_by_hand_does_not_fill_the_signal_list(app, pane):
    """A value read once is a reading; a series of one point in the plot would
    fill the list with things nobody is watching."""
    window, view = pane
    view.bind(FakeNode())
    settle(app)
    view.refresh()
    pump(app, 0.2)
    assert window.signals.keys() == []


def test_the_poll_rate_is_remembered_per_custom_pane(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    first = MainWindow()
    save("battery", CustomPane(title="Battery", fields=[Field(index=0x2001, kind="value")]))
    first.open_custom_pane("battery")
    first._custom_pane_view("battery").poll_hz.setValue(5.0)
    first.close()
    settle(app)

    second = MainWindow()
    second.open_custom_pane("battery")
    assert second._custom_pane_view("battery").poll_hz.value() == pytest.approx(5.0)
    second.close()


def test_a_box_being_typed_into_is_not_overwritten(app, tmp_path, monkeypatch):
    """A polled value landing in the box would take the half-typed number with
    it, and the next thing pressed would be Enter."""
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    window = MainWindow()
    save("gains", CustomPane(title="Gains", fields=[Field(index=0x2001, kind="number")]))
    window.open_custom_pane("gains")
    view = window._custom_pane_view("gains")
    view.bind(FakeNode())
    settle(app)

    widget = view._widgets[0]
    widget.edit.setText("999")
    widget.edit.setFocus()
    app.processEvents()
    if not widget.edit.hasFocus():
        pytest.skip("no focus under this platform plugin")

    widget.set_value(0x2001, 0, 4321, None)
    assert widget.edit.text() == "999", "what was being typed is still there"
    window.close()


# --- whose answer is this ----------------------------------------------------------------
def test_an_answer_nobody_polled_for_does_not_finish_a_round(app, pane):
    """A source answers in the order it was asked, so the oldest waiting
    request for an object is whose answer this is. Anything else and a round
    is finished by an answer it never asked for."""
    _window, view = pane
    view.bind(FakeNode(delay_ms=50))
    pump(app, 0.2)  # let the reads bind() started actually land

    told = []
    view.poller.answered = lambda index, sub: told.append((index, sub))
    view._widgets[0].read_requested.emit(0x2001, 0)  # somebody pressing Read
    pump(app, 0.15)
    assert told == [], "a read by hand is not an answer to a poll"


def test_an_answer_polling_did_ask_for_reaches_it(app, pane):
    _window, view = pane
    view.bind(FakeNode(delay_ms=10))
    pump(app, 0.1)

    told = []
    view.poller.answered = lambda index, sub: told.append((index, sub))
    view._on_poll_read(0x2001, 0)
    pump(app, 0.1)
    assert told == [(0x2001, 0)]


def test_reading_by_hand_while_polling_does_not_flatter_the_rate(app, pane):
    """The bug this pair exists for. Reading a pane while it polled used to
    finish whichever round was in flight, and the rate then read faster than
    the bus was really managing -- which is the one thing that number is there
    not to do."""
    _window, view = pane
    view.bind(FakeNode(delay_ms=50))
    pump(app, 0.2)
    view.poll_hz.setValue(50.0)
    view.poll.setChecked(True)
    pump(app, 0.2)

    for _ in range(5):
        view._widgets[0].read_requested.emit(0x2001, 0)
        pump(app, 0.05)
    assert "asked for 50" in view.poll_rate.text(), view.poll_rate.text()


def test_reads_that_are_never_answered_do_not_pile_up(app, pane):
    """A source that answers some and not others would otherwise grow this
    list for as long as the polling ran."""
    from pycangui.ui.custom_pane_view import MAX_WAITING

    _window, view = pane

    class Silent(FakeNode):
        def request(self, index, sub):
            self.reads += 1  # asked, and never answered

    view.bind(Silent())
    for _ in range(MAX_WAITING * 3):
        view._on_poll_read(0x2001, 0)
    assert len(view._asked[(0x2001, 0)]) == MAX_WAITING
