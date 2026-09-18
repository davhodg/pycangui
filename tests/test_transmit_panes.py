# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""More than one transmit list, and the rule that one out of sight does not send.

A second list is a real thing to want: the background traffic a rig needs left
running, and a scratch list to try something in, without the two being the same
list. Two things have to hold for that to be safe rather than clever -- Stop
all cyclic still means all of them, and a list nobody can see is not quietly
putting frames on the bus.
"""

import pytest
from PySide6.QtCore import QSettings, Qt

from pycangui.ui.main_window import MainWindow
from pycangui.ui.tx_view import COL_CYCLIC, TxView


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    win = MainWindow()
    win.resize(1400, 900)
    win.show()
    app.processEvents()
    yield win
    win.close()


def settle(app, times=5):
    for _ in range(times):
        app.processEvents()


@pytest.fixture
def connected(app, window):
    bus = window.channels.active_bus()
    bus.connect_bus("virtual", "vcan_tx_panes", 500_000, False)
    settle(app)
    yield window
    bus.disconnect_bus()


def sending(app, view: TxView, can_id: str = "123") -> None:
    """One raw message in this list, repeating."""
    view.add_message({"kind": "raw", "id": can_id, "data": "01 02", "period": 50})
    settle(app)
    view.item(view.message_count() - 1).setCheckState(COL_CYCLIC, Qt.Checked)
    settle(app)


# --- a second list ------------------------------------------------------------------------
def test_transmit_is_offered_as_another_pane(app, window):
    offered = {a.text() for a in _submenu(window, "Standard panes").actions()}
    assert offered == {
        "Additional CAN Trace",
        "Additional Signals and Plot",
        "Additional CAN Transmit",
        "Additional ASCII Log",
    }


def test_the_second_list_is_a_second_list(app, window):
    """Not the first one shown twice."""
    second = window.panes.add("tx")
    settle(app)
    assert window.panes.view("tx").key != window.panes.view(second).key

    sending(app, window.panes.view("tx"), "123")
    assert window.panes.view(second).message_count() == 0


def test_each_list_is_remembered_on_its_own(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    first = MainWindow()
    first.show()
    settle(app)
    second_name = first.panes.add("tx")
    first.panes.view("tx").add_message({"kind": "raw", "id": "111", "data": "01"})
    first.panes.view(second_name).add_message({"kind": "raw", "id": "222", "data": "02"})
    settle(app)
    first.close()
    settle(app)

    again = MainWindow()
    again.show()
    settle(app)
    assert again.panes.view("tx").item(0).text(1) == "111"
    assert again.panes.view(second_name).item(0).text(1) == "222"
    again.close()


def _submenu(window, title):
    for action in window.view_menu.actions():
        if action.menu() is not None and action.text() == title:
            return action.menu()
    raise AssertionError(f"no {title} submenu")


# --- stop all means all ---------------------------------------------------------------------
def test_stop_all_cyclic_stops_every_list(app, connected):
    """The button says all, and a big red stop that stopped half of what was
    going onto a live bus would be the worst kind of wrong."""
    window = connected
    second = window.panes.view(window.panes.add("tx"))
    settle(app)
    first = window.panes.view("tx")
    sending(app, first, "123")
    sending(app, second, "456")
    assert (first.cyclic_count(), second.cyclic_count()) == (1, 1)

    first._on_stop_all_pressed()
    settle(app)
    assert (first.cyclic_count(), second.cyclic_count()) == (0, 0)


def test_a_list_on_its_own_still_stops_itself(app, connected):
    """Nothing listening -- a pane in a test, or one a plugin built."""
    window = connected
    view = window.panes.view("tx")
    sending(app, view)
    view.stop_all_requested.disconnect()
    view._on_stop_all_pressed()
    settle(app)
    assert view.cyclic_count() == 0


# --- and out of sight is not sending -----------------------------------------------------------
def test_closing_a_transmit_pane_stops_what_it_was_sending(app, connected):
    """Frames arriving on a live bus from a pane nobody can see is the hardest
    sort of fault to find: nothing on screen accounts for them."""
    window = connected
    view = window.panes.view("tx")
    window.panes.docks["tx"].setVisible(True)
    settle(app)
    sending(app, view)
    assert view.cyclic_count() == 1

    window.panes.docks["tx"].close()
    settle(app)
    assert view.cyclic_count() == 0


def test_bringing_it_back_does_not_start_sending_again(app, connected):
    """Beginning to transmit onto a bus is not something to do unasked."""
    window = connected
    view = window.panes.view("tx")
    window.panes.docks["tx"].setVisible(True)
    settle(app)
    sending(app, view)
    window.panes.docks["tx"].close()
    settle(app)

    window.panes.docks["tx"].setVisible(True)
    settle(app)
    assert view.cyclic_count() == 0
    assert view.item(0).checkState(COL_CYCLIC) == Qt.Unchecked, "and the tick is off, honestly"


def test_being_tabbed_behind_another_pane_is_not_being_put_away(app, connected):
    """Stopping a rig's traffic because somebody looked at the trace would be
    its own kind of unpleasant surprise."""
    window = connected
    view = window.panes.view("tx")
    window.panes.docks["tx"].setVisible(True)
    window.panes.docks["uds"].setVisible(True)
    settle(app)
    sending(app, view)

    window.tabifyDockWidget(window.panes.docks["tx"], window.panes.docks["uds"])
    window.panes.docks["uds"].raise_()
    settle(app)
    assert view.cyclic_count() == 1


def test_detaching_is_not_being_put_away_either(app, connected):
    """It is on screen in a window of its own, which is the whole point of it."""
    window = connected
    view = window.panes.view("tx")
    window.panes.docks["tx"].setVisible(True)
    settle(app)
    sending(app, view)

    window.panes.detach("tx")
    settle(app)
    assert view.cyclic_count() == 1
    window.panes.attach("tx")
    settle(app)


def test_only_transmit_panes_are_subject_to_this(app, connected):
    """Closing the trace does not stop anything; there is nothing to stop."""
    window = connected
    view = window.panes.view("tx")
    window.panes.docks["tx"].setVisible(True)
    settle(app)
    sending(app, view)

    window.panes.docks["trace"].close()
    settle(app)
    assert view.cyclic_count() == 1


def test_closing_the_window_is_not_reported_as_somebody_putting_a_pane_away(
    app, tmp_path, monkeypatch
):
    """Everything is hidden on the way out, and a paragraph about it is not
    what anybody wants at that moment."""
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    window = MainWindow()
    window.show()
    settle(app)
    bus = window.channels.active_bus()
    bus.connect_bus("virtual", "vcan_tx_close", 500_000, False)
    settle(app)
    window.panes.docks["tx"].setVisible(True)
    settle(app)
    sending(app, window.panes.view("tx"))

    before = window.log.toPlainText().count("were stopped")
    window.close()
    settle(app)
    assert window.log.toPlainText().count("were stopped") == before
    bus.disconnect_bus()
