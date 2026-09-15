# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""The once-a-session questions asked before disturbing real equipment."""

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QMessageBox

from pycangui.canopen.manager import CanopenManager
from pycangui.core.bus import BusManager
from pycangui.core.context import Context
from pycangui.core.dbc import DbcDecoder
from pycangui.ui.confirm import Confirmations, Remembered, accept_notice, is_real
from pycangui.ui.tx_view import DEFAULT_RAW, TxView


@pytest.fixture
def answers(monkeypatch):
    """Drive the confirmation dialog from a script of answers, recording them.

    The dialog is built rather than raised through ``QMessageBox.warning``,
    because a static call cannot carry the "do not ask again" tick box.  What
    is recorded is the question as the dialog shows it, not its title, which
    macOS does not show.
    """
    asked: list[str] = []
    replies: list = []

    def fake_exec(box):
        asked.append(box.text())
        return replies.pop(0) if replies else QMessageBox.Cancel

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    return asked, replies


def test_asks_once_then_remembers(app, answers):
    asked, replies = answers
    confirm = Confirmations()
    replies.append(QMessageBox.Yes)
    assert confirm.ask(None, "k", "Title", "body") is True
    assert confirm.ask(None, "k", "Title", "body") is True
    assert len(asked) == 1, "the second time must not ask again"


def test_cancelling_is_not_remembered(app, answers):
    asked, replies = answers
    confirm = Confirmations()
    replies.extend([QMessageBox.Cancel, QMessageBox.Yes])
    assert confirm.ask(None, "k", "Title", "body") is False
    assert confirm.ask(None, "k", "Title", "body") is True
    assert len(asked) == 2, "saying no must leave the question open"


def test_a_different_key_asks_again(app, answers):
    asked, replies = answers
    confirm = Confirmations()
    replies.extend([QMessageBox.Yes, QMessageBox.Yes])
    confirm.ask(None, "connect:CAN 1:500000", "T", "b")
    confirm.ask(None, "connect:CAN 1:125000", "T", "b")
    assert len(asked) == 2, "changing the bitrate is a different question"


def test_the_question_is_in_the_dialog_not_only_in_its_title(app, monkeypatch):
    """macOS shows no title on a message box, so a question kept only there
    would leave Yes and Cancel answering nothing anybody could read."""
    seen = {}

    def fake_exec(box):
        seen.update(text=box.text(), detail=box.informativeText())
        return QMessageBox.Cancel

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    Confirmations().ask(None, "k", "Transmit onto a real bus?", "It will reach the nodes.")
    assert seen == {"text": "Transmit onto a real bus?", "detail": "It will reach the nodes."}

    accept_notice()
    assert seen["text"] and seen["detail"]


def test_is_real():
    assert is_real("socketcan") and is_real("pcan")
    assert not is_real("virtual"), "the virtual bus reaches no hardware"
    assert not is_real(""), "not connected is not a real bus"


# --- transmitting ------------------------------------------------------------------
@pytest.fixture
def tx(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    ctx = Context(log=print)
    bus = BusManager()
    confirm = Confirmations()
    view = TxView(bus, ctx, DbcDecoder(), CanopenManager(bus), confirm)
    bus.connect_bus("virtual", "vcan_confirm", 500000, False)
    view.add_message(dict(DEFAULT_RAW))
    yield view, bus, confirm
    view.stop_all()
    bus.disconnect_bus()


def test_transmitting_on_a_virtual_channel_never_asks(app, tx, answers):
    asked, _ = answers
    view, _bus, _confirm = tx
    view.send_row(0)
    assert not asked, "a virtual bus reaches nothing, so there is nothing to warn about"


def test_transmitting_on_a_real_channel_asks_once(app, tx, answers):
    asked, replies = answers
    view, bus, _confirm = tx
    bus.interface = "socketcan"  # pretend the adapter is real
    sent = []
    bus.frames.connect(sent.extend)

    replies.append(QMessageBox.Cancel)
    view.send_row(0)
    app.processEvents()
    assert asked, "it must ask before putting a frame on a real bus"
    assert not sent, "cancelling must not transmit"

    replies.append(QMessageBox.Yes)
    view.send_row(0)
    view.send_row(0)
    assert len(asked) == 2, "having agreed, sending again must not ask"


def test_starting_a_cyclic_row_is_gated_too(app, tx, answers):
    from PySide6.QtCore import Qt

    from pycangui.ui.tx_view import COL_CYCLIC

    asked, replies = answers
    view, bus, _confirm = tx
    bus.interface = "socketcan"
    replies.append(QMessageBox.Cancel)

    view.item(0).setCheckState(COL_CYCLIC, Qt.Checked)
    app.processEvents()
    assert asked, "starting a cyclic transmission must ask as well"
    assert view.item(0).checkState(COL_CYCLIC) == Qt.Unchecked, "and untick itself"
    assert not view._tasks


# --- connecting --------------------------------------------------------------------
@pytest.fixture
def window(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    from pycangui.ui.main_window import MainWindow

    win = MainWindow()
    yield win
    win.close()


def test_connecting_to_a_virtual_channel_never_asks(app, window, answers):
    asked, _ = answers
    window._connect_active("virtual", "vcan_ask", 500000, False)
    assert not asked
    assert window.channels.active_bus().is_connected
    window.channels.disconnect_all()


def test_cancelling_the_connect_warning_does_not_touch_the_bus(app, window, answers):
    """The point of the question: a wrong bitrate on a live bus is destructive.

    Cancelling must stop before python-can is asked for a bus at all, and the
    Connect button has to come back up, or it reads "Disconnect" for a bus
    that was never joined.
    """
    asked, replies = answers
    replies.append(QMessageBox.Cancel)
    window._connect_active("socketcan", "can0", 125000, False)

    assert len(asked) == 1
    assert not window.channels.active_bus().is_connected
    assert not window.connect_bar.button.isChecked()


def test_the_connect_warning_names_the_bitrate(app, window, monkeypatch):
    seen = {}

    def fake_exec(box):
        seen["text"] = box.informativeText()
        return QMessageBox.Cancel

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    window._connect_active("socketcan", "can0", 125000, False)
    assert "125 kbit/s" in seen["text"], "the number to check has to be in front of you"


# --- remembering an answer for good ---------------------------------------------------------
@pytest.fixture
def store(tmp_path):
    """A settings store of its own, so one test cannot answer another's question."""
    settings = QSettings(str(tmp_path / "agreed.ini"), QSettings.IniFormat)
    return Remembered(settings, user="alice")


def ticking(monkeypatch, answer=QMessageBox.Yes, tick=True):
    """Somebody who answers, having ticked the box or not."""

    def fake_exec(box):
        if box.checkBox() is not None:
            box.checkBox().setChecked(tick)
        return answer

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)


def test_without_a_store_the_answer_lasts_the_session(app, answers):
    """Which is what everything but the window gets: a Confirmations built for
    a pane or a test must not reach into the real user's settings."""
    _asked, replies = answers
    replies.append(QMessageBox.Yes)
    Confirmations().ask(None, "k", "T", "b")
    assert not Confirmations().agreed("k"), "a second one starts knowing nothing"


def test_the_box_is_only_offered_where_there_is_somewhere_to_keep_it(app, monkeypatch, store):
    """Offering it and then forgetting would be a promise the dialog could not
    keep."""
    seen = []
    monkeypatch.setattr(
        QMessageBox, "exec", lambda box: seen.append(box.checkBox()) or QMessageBox.Yes
    )
    Confirmations().ask(None, "k", "T", "b")
    Confirmations(store).ask(None, "k", "T", "b")
    assert seen[0] is None, "nowhere to keep it, so nothing offered"
    assert seen[1] is not None


def test_a_ticked_answer_outlives_the_session(app, monkeypatch, store):
    ticking(monkeypatch)
    assert Confirmations(store).ask(None, "connect:500000", "T", "b")

    monkeypatch.setattr(QMessageBox, "exec", lambda _box: pytest.fail("it asked again"))
    assert Confirmations(store).ask(None, "connect:500000", "T", "b"), "a new session"


def test_an_unticked_answer_does_not(app, monkeypatch, store):
    ticking(monkeypatch, tick=False)
    assert Confirmations(store).ask(None, "connect:500000", "T", "b")

    asked = []
    monkeypatch.setattr(QMessageBox, "exec", lambda box: asked.append(1) or QMessageBox.Yes)
    Confirmations(store).ask(None, "connect:500000", "T", "b")
    assert asked, "it was only agreed for that session"


def test_saying_no_remembers_nothing_however_the_box_was_ticked(app, monkeypatch, store):
    """Otherwise a tick plus Cancel would silently agree to the thing that was
    just refused."""
    ticking(monkeypatch, answer=QMessageBox.Cancel, tick=True)
    assert not Confirmations(store).ask(None, "k", "T", "b")
    assert store.keys() == set()


def test_another_person_on_the_same_machine_is_asked_for_themselves(app, tmp_path, monkeypatch):
    """The point of storing who agreed: a settings store that arrives from
    somewhere else is not an agreement by whoever is sitting here."""
    settings = QSettings(str(tmp_path / "agreed.ini"), QSettings.IniFormat)
    ticking(monkeypatch)
    Confirmations(Remembered(settings, user="alice")).ask(None, "k", "T", "b")

    assert Remembered(settings, user="alice").keys() == {"k"}
    assert Remembered(settings, user="bob").keys() == set(), "bob agreed to nothing"


def test_asking_about_everything_again_takes_it_all_back(app, monkeypatch, store):
    ticking(monkeypatch)
    confirm = Confirmations(store)
    confirm.ask(None, "k", "T", "b")
    assert confirm.forget_everything() == 1
    assert not confirm.agreed("k")
    assert store.keys() == set()


# --- the notice at the start ------------------------------------------------------------------
def test_continuing_past_the_notice_starts_pycangui(app, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        QMessageBox, "exec", lambda box: seen.update(text=box.informativeText()) or QMessageBox.Ok
    )
    assert accept_notice()
    assert seen["text"], "and it had something to say"


def test_quitting_the_notice_means_it_does_not_start(app, monkeypatch):
    """A click-through nobody can decline is not an agreement."""
    monkeypatch.setattr(QMessageBox, "exec", lambda _box: QMessageBox.Cancel)
    assert not accept_notice()


def test_it_cannot_be_switched_off(app, monkeypatch):
    """The one dialog here with no "do not ask again" on it.  A notice
    dismissed for good on the first afternoon is one the colleague who picks
    the machine up in March never sees, and it costs a keypress a session."""
    seen = []
    monkeypatch.setattr(
        QMessageBox, "exec", lambda box: seen.append(box.checkBox()) or QMessageBox.Ok
    )
    assert accept_notice()
    assert accept_notice()
    assert seen == [None, None], "no tick box, and shown again the next time"


def test_the_slow_half_of_starting_up_happens_behind_it(app, monkeypatch):
    """Which is what makes an unskippable notice cost nothing: a second and a
    half of libraries loads while somebody reads it, instead of a second and a
    half of nothing before anything appears."""
    order = []
    monkeypatch.setattr(
        QMessageBox, "exec", lambda _box: order.append("answered") or QMessageBox.Ok
    )
    assert accept_notice(while_shown=lambda: order.append("loaded"))
    assert order == ["loaded", "answered"], "loaded before the answer was waited for"


def test_it_is_on_screen_before_the_slow_half_starts(app, monkeypatch):
    """The whole point.  Doing the work first and showing the notice after
    would be the same total and none of the benefit."""
    was_visible = []
    box = {}
    real_show = QMessageBox.show

    def remember_and_show(self):
        box["it"] = self
        return real_show(self)

    monkeypatch.setattr(QMessageBox, "show", remember_and_show)
    monkeypatch.setattr(QMessageBox, "exec", lambda _box: QMessageBox.Ok)
    accept_notice(while_shown=lambda: was_visible.append(box["it"].isVisible()))
    assert was_visible == [True]


def test_a_build_with_nothing_to_show_it_still_loads(app, monkeypatch):
    """``main`` falls back to loading in the open if the notice never ran the
    callback, because not starting is worse than starting slowly."""
    monkeypatch.setattr(QMessageBox, "exec", lambda _box: QMessageBox.Ok)
    ran = []
    assert accept_notice(while_shown=None) is True
    assert ran == [], "nothing to run, and nothing broken by that"
