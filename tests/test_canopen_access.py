# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""A node added by hand, and logging in to one.

A node appears by itself when its heartbeat arrives, which leaves out the ones
somebody most needs to reach: heartbeat off, held in pre-operational, sitting
in a bootloader. So one can be added by hand.

CANopen has no login of its own, so logging in is entirely the maker's, written
in ``hooks/canopen.py``. What pycangui owns is asking, saying what came back,
and not letting a password go anywhere but the hook.
"""

import time

import pytest
from PySide6.QtCore import QCoreApplication, QSettings
from PySide6.QtWidgets import QDialog, QInputDialog, QLineEdit

from pycangui.canopen.manager import ADDED_BY_HAND, CanopenManager
from pycangui.core.bus import BusManager
from pycangui.core.context import Context
from pycangui.core.hooks import Hooks, registry
from pycangui.ui import canopen_login
from pycangui.ui.canopen_login import LoginDialog
from pycangui.ui.main_window import MainWindow


def wait_until(pred, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not pred():
        QCoreApplication.processEvents()
        if time.monotonic() > deadline:
            raise AssertionError("timed out")
        time.sleep(0.005)


class FakeHooks:
    """Answers per hook name, and a record of what was asked."""

    def __init__(self, **answers):
        self.answers = answers
        self.calls: list[tuple] = []

    def call(self, module, name, *args, **_kwargs):
        self.calls.append((module, name, args))
        return self.answers.get(name)


@pytest.fixture
def connected(app):
    bus = BusManager()
    manager = CanopenManager(bus, hooks=FakeHooks())
    said: list[str] = []
    manager.message.connect(said.append)
    bus.connect_bus("virtual", "vcan_access", 500000, False)
    yield manager, said
    bus.disconnect_bus()
    manager.shutdown()


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    return tmp_path


def levels_of(manager):
    seen: list[tuple] = []
    manager.access_level.connect(lambda node_id, level: seen.append((node_id, level)))
    return seen


# --- a node by hand ---------------------------------------------------------------------------
def test_a_node_nobody_has_heard_can_be_added_by_hand(connected):
    manager, _said = connected
    seen = []
    manager.node_seen.connect(lambda *a: seen.append(a))
    assert manager.add_node(12)
    assert seen == [(12, ADDED_BY_HAND)]
    assert 12 in manager.nodes() and manager.node(12) is not None


def test_a_node_id_that_is_not_one_is_refused(connected):
    manager, said = connected
    assert not manager.add_node(200)
    assert said and "not a node id" in said[-1]


def test_with_no_bus_there_is_nothing_to_add_it_to(app):
    manager = CanopenManager(BusManager())
    said = []
    manager.message.connect(said.append)
    assert not manager.add_node(12)
    assert said and "not connected" in said[0]
    manager.shutdown()


# --- logging in ---------------------------------------------------------------------------------
def test_a_granted_login_shows_the_level_the_node_says_it_holds(connected):
    manager, said = connected
    manager._hooks = FakeHooks(login=True, current_level=3)
    seen = levels_of(manager)

    manager.login(5, 2, "secret")
    wait_until(lambda: seen)
    assert seen == [(5, 3)] and manager.access_levels[5] == 3
    assert any("logged in at level 3 (asked for 2)" in s for s in said)
    login_call = next(args for _m, name, args in manager._hooks.calls if name == "login")
    assert login_call[1:] == (2, "secret"), "the level and the password reach the hook"


def test_the_password_goes_to_the_hook_and_nowhere_else(connected):
    manager, said = connected
    manager._hooks = FakeHooks(login=False)
    seen = levels_of(manager)
    manager.login(5, 2, "hunter2")
    wait_until(lambda: seen)
    assert not any("hunter2" in s for s in said)


def test_without_current_level_the_granted_level_is_shown(connected):
    manager, _said = connected
    manager._hooks = FakeHooks(login=True, current_level=None)
    seen = levels_of(manager)
    manager.login(5, 2)
    wait_until(lambda: seen)
    assert seen == [(5, 2)]


def test_a_refused_login_says_so_and_holds_no_level(connected):
    manager, said = connected
    manager.access_levels[5] = 3
    manager._hooks = FakeHooks(login=False)
    seen = levels_of(manager)
    manager.login(5, 4)
    wait_until(lambda: seen)
    assert seen == [(5, None)] and 5 not in manager.access_levels
    assert any("level 4 refused" in s for s in said)


def test_a_device_with_no_login_says_where_one_is_written(connected):
    manager, said = connected
    manager._hooks = FakeHooks()
    seen = levels_of(manager)
    manager.login(5, 1)
    wait_until(lambda: any("hooks/canopen.py::login" in s for s in said))
    assert seen == []


def test_read_level_asks_the_node(connected):
    manager, _said = connected
    manager._hooks = FakeHooks(current_level=4)
    seen = levels_of(manager)
    manager.read_level(5)
    wait_until(lambda: seen)
    assert seen == [(5, 4)] and manager.access_levels[5] == 4


def test_read_level_with_no_way_to_ask_says_where_one_is_written(connected):
    manager, said = connected
    manager._hooks = FakeHooks()
    manager.read_level(5)
    wait_until(lambda: any("hooks/canopen.py::current_level" in s for s in said))


def test_a_level_lasts_the_connection_at_most(connected):
    manager, _said = connected
    manager.access_levels[5] = 3
    manager.forget_nodes()
    assert manager.access_levels == {}


def test_the_supplied_hooks_have_no_login_until_one_is_written(app, home):
    assert {"login", "current_level"} <= set(registry()["canopen"])
    hooks = Hooks(Context(log=print))
    assert hooks.call("canopen", "login", object(), 1, "") is None
    assert hooks.call("canopen", "current_level", object()) is None


# --- the pane -----------------------------------------------------------------------------------
@pytest.fixture
def window(app, home, monkeypatch):
    win = MainWindow()
    # Identifying a node is an SDO exchange nobody on this bus will answer.
    monkeypatch.setattr(win.canopen, "identify", lambda _node_id: None)
    yield win
    win.close()


def seen_by_hand(window, node_id):
    window.canopen.node_seen.emit(node_id, ADDED_BY_HAND)
    return window.canopen_view._node_item(node_id)


def test_add_node_puts_it_in_the_list_and_selects_it(window, monkeypatch):
    view = window.canopen_view
    monkeypatch.setattr(QInputDialog, "getInt", lambda *a, **k: (12, True))
    monkeypatch.setattr(
        window.canopen,
        "add_node",
        lambda n: (window.canopen.node_seen.emit(n, ADDED_BY_HAND), True)[1],
    )
    view._add_node()
    item = view._node_item(12)
    assert item is not None and item.text(2) == ADDED_BY_HAND
    assert view.selected_node() == 12


def test_login_hands_the_level_and_password_on_and_remembers_only_the_level(window, monkeypatch):
    view = window.canopen_view
    view.nodes.setCurrentItem(seen_by_hand(window, 12))
    asked = []
    monkeypatch.setattr(window.canopen, "login", lambda *a: asked.append(a))

    def answered(dialog):
        dialog.level.setValue(3)
        dialog.password.setText("hunter2")
        return QDialog.Accepted

    monkeypatch.setattr(LoginDialog, "exec", answered)
    view._login()
    assert asked == [(12, 3, "hunter2")]
    assert window.ctx.settings.get(canopen_login.LEVEL_KEY) == 3
    assert "hunter2" not in repr(window.ctx.settings.get("canopen", {}))


def test_login_with_no_node_selected_says_so(window):
    window.canopen_view._login()
    assert "no node selected" in window.log.toPlainText()


def test_the_access_column_shows_the_level_held(window):
    item = seen_by_hand(window, 12)
    window.canopen.access_level.emit(12, 3)
    assert item.text(4) == "3"
    window.canopen.access_level.emit(12, None)
    assert item.text(4) == ""


def test_the_password_is_not_shown_as_it_is_typed(app):
    dialog = LoginDialog(None, 5, 1)
    assert dialog.password.echoMode() == QLineEdit.Password
    dialog.deleteLater()


def test_a_remembered_level_that_is_nonsense_is_not_used(app):
    assert canopen_login.remembered_level({"canopen.login_level": "high"}) == 1
    assert canopen_login.remembered_level({"canopen.login_level": 900}) == 255
