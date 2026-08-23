"""The once-a-session questions asked before disturbing real equipment."""

import pytest
from PySide6.QtWidgets import QMessageBox

from pycangui.canopen.manager import CanopenManager
from pycangui.core.bus import BusManager
from pycangui.core.context import Context
from pycangui.core.dbc import DbcDecoder
from pycangui.ui.confirm import Confirmations, is_real
from pycangui.ui.tx_view import DEFAULT_RAW, TxView


@pytest.fixture
def answers(monkeypatch):
    """Drive QMessageBox.warning from a script of answers, recording the calls."""
    asked: list[str] = []
    replies: list = []

    def fake(_parent, title, text, *_args, **_kwargs):
        asked.append(title)
        return replies.pop(0) if replies else QMessageBox.Cancel

    monkeypatch.setattr(QMessageBox, "warning", fake)
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

    assert asked == ["Connect to a real CAN bus?"]
    assert not window.channels.active_bus().is_connected
    assert not window.connect_bar.button.isChecked()


def test_the_connect_warning_names_the_bitrate(app, window, monkeypatch):
    seen = {}

    def fake(_parent, title, text, *_args, **_kwargs):
        seen["text"] = text
        return QMessageBox.Cancel

    monkeypatch.setattr(QMessageBox, "warning", fake)
    window._connect_active("socketcan", "can0", 125000, False)
    assert "125 kbit/s" in seen["text"], "the number to check has to be in front of you"
