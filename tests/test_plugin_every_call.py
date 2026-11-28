# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""A plugin that uses every call of the API: each does what it says, and
unloading the plugin takes all of it back.

The plugin is tests/plugins/every_call. A call added to the plugin API and not
used there fails the first test, so it stays the whole API.
"""

import ast
import shutil
import sys
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QMessageBox

from pycangui.core.hooks import registry
from pycangui.ui import field_widgets
from pycangui.ui.main_window import MainWindow
from pycangui.ui.plugin_app import PluginApp

SOURCE = Path(__file__).parent / "plugins" / "every_call"
NAME = "every_call"
#: Called by pycangui on the plugin's behalf, not by the plugin.
NOT_FOR_PLUGINS = {"remove_all"}


def used_in(source: Path) -> set[str]:
    """Every ``app.<name>`` the plugin's source mentions."""
    tree = ast.parse(source.read_text(encoding="utf-8"))
    return {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "app"
    }


@pytest.fixture
def agrees(monkeypatch):
    from pycangui.ui import plugin_manager

    monkeypatch.setattr(plugin_manager.messages, "warning", lambda *_a, **_k: QMessageBox.Yes)


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    win = MainWindow()
    win.show()
    app.processEvents()
    shutil.copytree(SOURCE, win.ctx.workspace_dir / "plugins" / NAME)
    win._reload_plugins()
    settle(app)
    yield win
    win.close()


def settle(app, condition=lambda: True, seconds=10.0) -> bool:
    end = time.monotonic() + seconds
    app.processEvents()
    while not condition() and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    return condition()


def loaded(window):
    record = window.plugins.loaded[NAME]
    assert record.ok, record.error
    return record.app


def test_the_plugin_uses_every_call_there_is(app, window):
    plugin = loaded(window)
    offered = {name for name in dir(plugin) if not name.startswith("_")} - NOT_FOR_PLUGINS
    assert offered - used_in(SOURCE / "plugin.py") == set(), "add a use of it to the plugin"


def test_it_imports_nothing_of_pycanguis():
    tree = ast.parse((SOURCE / "plugin.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert not any(name.split(".")[0] == "pycangui" for name in imported)


def test_each_call_does_what_it_says(app, window):
    plugin = loaded(window)
    seen = plugin.seen
    assert seen["lib"] == "1.0", "from its own lib/, which met its REQUIRES"
    assert seen["api"] == PluginApp.api_version

    assert seen["pane"] in window.panes.names()
    assert True in seen["shown"], "opened, and told so"
    assert not seen["button"].icon().isNull()
    assert seen["menu"] in window.plugin_menu(NAME).actions()
    seen["menu"].trigger()
    assert seen["pressed"] == "menu"
    seen["toolbar"].trigger()
    assert seen["pressed"] == "toolbar" and seen["toolbar"].isChecked()

    assert seen["labeller"] in window._labellers
    assert field_widgets.BY_KIND["every_call"].__name__ == "Shown"
    assert settle(app, lambda: seen["done"] is not None)
    assert seen["done"] == (42, None)

    assert seen["paused"] is False and seen["held"] is True
    assert window.ctx.pause.holders == ["every call, still"]
    window.ctx.pause.stop_all()
    assert seen["stops"] == 1

    assert seen["emcy"]
    assert hasattr(seen["poller"], "asked")
    assert list(window.signals.get("Every call/Value").values) == [7.0]
    assert seen["devices"] == [], "no bus, no nodes"
    assert seen["device"] is window.devices.device(127), "one per key, kept"
    assert seen["provider"] in window.devices._providers

    assert seen["count"] == 1 and plugin.settings.keys() == ["count"]
    assert window.plugin_names["every_call"] is plugin
    assert seen["hooked"] == "Hello Ada"
    assert (window.ctx.hooks_dir / "every_call.py").exists()
    assert settle(app, lambda: seen["ready"] == 4712)
    assert seen["helper"].running

    assert all(thing is not None for thing in seen["live"])


def test_uninstalling_it_takes_all_of_that_back(app, window, agrees):
    plugin = loaded(window)
    seen = plugin.seen
    assert settle(app, lambda: seen["ready"] == 4712)
    window.plugin_actions.uninstall(NAME)  # unloaded first: its helper holds the folder open
    window._reload_plugins()
    settle(app)

    assert NAME not in window.plugins.loaded
    assert seen["pane"] not in window.panes.names()
    assert seen["shutdown"] == 1, "told its pane went"
    assert NAME not in window._plugin_menus
    assert seen["toolbar"] not in window.connect_bar.actions()
    assert seen["labeller"] not in window._labellers
    assert "every_call" not in field_widgets.BY_KIND
    assert window.ctx.pause.holders == [], "the pause it still held, given back"
    assert "every_call" not in window.plugin_names
    assert "every_call" not in registry()
    assert not seen["helper"].running
    assert seen["provider"] not in window.devices._providers
    assert "every_call_lib" not in sys.modules
    assert seen["count"] == 1, "its settings stay, as a program's do"

    window.ctx.pause.stop_all()
    window.ctx.pause.hold("after")
    assert seen["stops"] == 0, "nothing of it left connected"
    assert seen["pauses"] == [True, False, True], "held, released, held again; nothing after"


def test_closing_the_window_tells_it_and_stops_its_helper(app, window):
    plugin = loaded(window)
    seen = plugin.seen
    assert settle(app, lambda: seen["ready"] == 4712)
    window.close()
    settle(app)
    assert seen["closing"] == 1
    assert not seen["helper"].running
