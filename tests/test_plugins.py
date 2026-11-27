# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""User code that adds screens, and what happens when it goes wrong.

A hook answers a question pycangui already knows to ask. A plugin adds
something that was not there. Two properties matter more than any of the
features, and most of this file is about them: a plugin that fails takes only
itself down, and reloading one really does remove what the last version added
rather than leaving a second copy beside it.
"""

import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QMessageBox

from pycangui.core import plugin_package
from pycangui.core.hooks import registry
from pycangui.core.plugins import API_VERSION, Plugins
from pycangui.ui import folders, plugin_manager
from pycangui.ui.main_window import MainWindow

PANE = """
from PySide6.QtWidgets import QLabel

NAME = "Demo"
DESCRIPTION = "A screen that says something."


def register(app):
    app.add_pane("screen", "Demo screen", lambda name: QLabel("{what}"))
"""

EVERYTHING = """
from PySide6.QtWidgets import QLabel

NAME = "Demo"


def register(app):
    app.add_pane("screen", "Demo screen", lambda name: QLabel("hello"))
    app.add_menu_action("Say hello", lambda: app.log("hello"), "a tooltip")
    app.add_toolbar_button("Demo", lambda: None)
    app.add_trace_labeller(lambda frame: "Demo" if frame.can_id == 0x123 else None)
    app.add_field_widget("gauge", QLabel)
"""

QUIET = """
NAME = "Quiet"
DESCRIPTION = "A pane only."


def register(app):
    pass
"""


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


def write_plugin(window, name: str, source: str) -> Path:
    folder = window.ctx.workspace_dir / "plugins" / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "plugin.py").write_text(source, encoding="utf-8")
    return folder


# --- finding them ----------------------------------------------------------------------
def test_a_folder_with_an_entry_file_is_a_plugin(tmp_path):
    (tmp_path / "demo").mkdir()
    (tmp_path / "demo" / "plugin.py").write_text("NAME = 'Demo'", encoding="utf-8")
    assert set(Plugins(folder=tmp_path).found()) == {"demo"}


def test_a_loose_file_is_not(tmp_path):
    """A folder, so a plugin can bring its own modules and data with it."""
    (tmp_path / "plugin.py").write_text("NAME = 'Nope'", encoding="utf-8")
    (tmp_path / "empty").mkdir()
    assert Plugins(folder=tmp_path).found() == {}


def test_folders_that_are_not_meant_to_be_plugins_are_skipped(tmp_path):
    for name in ("__pycache__", ".git"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "plugin.py").write_text("NAME = 'x'", encoding="utf-8")
    assert Plugins(folder=tmp_path).found() == {}


def test_a_new_workspace_loads_no_plugins(app, window):
    """A screen nobody asked for in every window is exactly what installing
    is there to prevent."""
    assert window.plugins.loaded == {}


# --- loading them -----------------------------------------------------------------------
def test_a_plugin_gets_to_add_things(app, window):
    write_plugin(window, "demo", EVERYTHING)
    window._reload_plugins()
    settle(app)

    assert "Demo" in [r.label for r in window.plugins.working()]
    assert "demo:screen" in window.panes.docks
    assert window.panes.docks["demo:screen"].windowTitle() == "Demo screen"


def test_a_plugin_pane_starts_hidden_and_is_in_the_view_menu(app, window):
    """One more pane among a dozen; opening every one on top of whatever
    somebody was doing is how a tool becomes a wall."""
    write_plugin(window, "demo", PANE.format(what="hello"))
    window._reload_plugins()
    settle(app)
    assert not window.panes.docks["demo:screen"].isVisible()
    assert "Demo screen" in [a.text() for a in window.view_menu.actions()]


def test_the_view_menu_groups_plugin_and_custom_panes_under_headings(app, window):
    """pycangui's own panes, then the plugins', then yours -- each group under
    a heading that says what it is, and a heading only when there is a group."""
    headings = [a.text() for a in window.view_menu.actions() if not a.isEnabled()]
    assert "Plugins" not in headings and "Custom panes" not in headings

    write_plugin(window, "demo", PANE.format(what="hello"))
    window._reload_plugins()
    window.open_custom_pane("Battery limits")
    settle(app)
    names = [a.text() for a in window.view_menu.actions()]
    headings = {a.text() for a in window.view_menu.actions() if not a.isEnabled()}

    assert {"Plugins", "Custom panes"} <= headings, "unclickable, so they read as headings"
    assert names.index("CAN Trace") < names.index("Plugins") < names.index("Demo screen")
    assert names.index("Demo screen") < names.index("Custom panes")
    assert names.index("Custom panes") < names.index("Battery limits")


def entries(menu):
    return [a.text() for a in menu.actions() if not a.isSeparator()]


def test_plugins_get_a_menu_of_their_own(app, window):
    """A plugin adds screens and commands; Tools is where the tool's own
    settings live."""
    assert "&Plugins" in [m.text() for m in window.menuBar().actions()]
    tools = next(m.menu() for m in window.menuBar().actions() if m.text() == "&Tools")
    assert not any("plugin" in text.lower() for text in entries(tools))


def test_the_menu_says_what_is_installed(app, window):
    """Also the answer to "what have I got", which is not a question Tools
    would ever be asked."""
    write_plugin(window, "demo", EVERYTHING)
    write_plugin(window, "quiet", QUIET)
    window._reload_plugins()
    settle(app)
    listed = entries(window.plugins_menu)
    assert {"Demo", "Quiet"} <= set(listed)
    assert listed.index("Demo") < listed.index("Quiet"), "by name, whatever else is installed"


def test_a_plugin_that_adds_no_entries_is_still_listed(app, window):
    write_plugin(window, "quiet", QUIET)
    window._reload_plugins()
    settle(app)
    assert entries(window.plugin_menu("quiet")) == ["A pane only."]
    assert not window.plugin_menu("quiet").actions()[0].isEnabled()


def test_one_that_failed_is_listed_too_rather_than_silently_absent(app, window):
    """A plugin that is silently missing is the hardest kind to notice."""
    write_plugin(window, "broken", "raise ValueError('deliberate')\n")
    window._reload_plugins()
    settle(app)
    failed = [a for a in window.plugins_menu.actions() if "failed to load" in a.text()]
    assert failed and not failed[0].isEnabled()


def test_with_nothing_installed_the_menu_still_shows_the_way_in(app, window):
    """Which is a fresh workspace: nothing is installed until somebody says so,
    so this menu is the first thing anybody sees of plugins."""
    assert entries(window.plugins_menu) == [
        "No plugins installed",
        "Install plugin...",
        "Manage plugins...",
        "Reload plugins",
        "Open plugins folder",
    ]


def test_reloading_and_the_folder_are_in_the_plugins_menu(app, window):
    """In the same order as the hook pair in Tools: the thing, then its folder."""
    assert entries(window.plugins_menu)[-2:] == ["Reload plugins", "Open plugins folder"]


def test_a_plugin_menu_entry_says_whose_it_is(app, window):
    write_plugin(window, "demo", EVERYTHING)
    window._reload_plugins()
    settle(app)
    assert entries(window.plugins_menu)[0] == "Demo"
    assert entries(window.plugin_menu("demo")) == ["Demo screen", "Say hello"]


def test_a_plugins_panes_are_under_its_name_as_in_the_view_menu(app, window):
    """Ticked while on screen, and picking one does what the View menu does."""
    write_plugin(window, "demo", PANE.format(what="hi"))
    window._reload_plugins()
    settle(app)
    menu = window.plugin_menu("demo")
    window.plugins_menu.aboutToShow.emit()
    [screen] = [a for a in menu.actions() if a.text() == "Demo screen"]
    assert screen.isCheckable() and not screen.isChecked()
    assert "A screen that says something." not in entries(menu), "not empty, so no stand-in"
    assert menu.menuAction().toolTip() == "A screen that says something."

    screen.trigger()
    settle(app)
    assert window.panes.on_screen("demo:screen")
    window.plugins_menu.aboutToShow.emit()
    assert next(a for a in menu.actions() if a.text() == "Demo screen").isChecked()
    assert entries(menu).count("Demo screen") == 1, "made again, not added again"


def test_with_a_pane_and_an_entry_the_panes_come_first(app, window):
    write_plugin(window, "demo", EVERYTHING)
    window._reload_plugins()
    settle(app)
    window.plugins_menu.aboutToShow.emit()
    actions = window.plugin_menu("demo").actions()
    assert [a.text() for a in actions] == ["Demo screen", "", "Say hello"]
    assert actions[1].isSeparator()


def test_a_plugin_can_name_frames_in_every_trace(app, window):
    """A second trace showing different names from the first would be a puzzle
    rather than a feature."""
    write_plugin(window, "demo", EVERYTHING)
    window._reload_plugins()
    settle(app)
    later = window.panes.view(window.panes.add("trace"))
    assert len(later.classifiers) == len(window.trace.classifiers)
    assert len(window.trace.classifiers) == 6, "the five built in, plus the plugin's"


def test_a_plugin_can_add_a_way_to_show_an_object(app, window):
    """The seven built in cover every screen we know of, which is not the same
    as every screen there will ever be."""
    from pycangui.ui import field_widgets

    write_plugin(window, "demo", EVERYTHING)
    window._reload_plugins()
    settle(app)
    assert "gauge" in field_widgets.BY_KIND


# --- when one goes wrong -------------------------------------------------------------------
def test_a_plugin_that_will_not_import_takes_only_itself_down(app, window):
    write_plugin(window, "broken", "import a_module_that_is_not_there\n")
    write_plugin(window, "demo", PANE.format(what="hello"))
    window._reload_plugins()
    settle(app)

    assert "broken" in window.plugins.errors()
    assert "Demo" in [r.label for r in window.plugins.working()], "the good one still loaded"
    assert "broken" in window.log.toPlainText()


def test_the_traceback_goes_where_somebody_will_see_it(app, window):
    """pycangui runs under pythonw, which has no console at all."""
    write_plugin(window, "broken", "raise ValueError('deliberate')\n")
    window._reload_plugins()
    settle(app)
    assert "deliberate" in window.log.toPlainText()


def test_a_plugin_with_nothing_to_register_is_told_so(app, window):
    write_plugin(window, "empty", "NAME = 'Empty'\n")
    window._reload_plugins()
    settle(app)
    assert "no register(app) function" in window.plugins.errors()["empty"]


def test_a_plugin_from_the_future_is_refused_rather_than_half_run(app, window):
    """Asking for methods that do not exist yet fails in the middle of doing
    something, which is a worse way to find out."""
    write_plugin(
        window,
        "ahead",
        f"API_VERSION = {API_VERSION + 1}\nNAME = 'Ahead'\ndef register(app):\n    pass\n",
    )
    window._reload_plugins()
    settle(app)
    assert "newer pycangui" in window.plugins.errors()["ahead"]


def test_half_of_a_plugin_that_failed_is_taken_back(app, window):
    """Otherwise the window keeps a menu entry that raises whenever it is used."""
    write_plugin(
        window,
        "halfway",
        "from PySide6.QtWidgets import QLabel\n"
        "NAME = 'Halfway'\n"
        "def register(app):\n"
        "    app.add_pane('screen', 'Half a screen', lambda name: QLabel('x'))\n"
        "    raise RuntimeError('and then it fell over')\n",
    )
    window._reload_plugins()
    settle(app)

    assert "halfway" in window.plugins.errors()
    assert "halfway:screen" not in window.panes.docks, "the pane it managed to add is gone"


# --- reloading them ---------------------------------------------------------------------------
def test_reloading_replaces_rather_than_repeats(app, window):
    """Without this a plugin edited and loaded again leaves its old pane, its
    old menu entries and its old buttons beside the new ones."""
    from pycangui.ui import field_widgets

    write_plugin(window, "demo", EVERYTHING)
    window._reload_plugins()
    settle(app)
    before = (
        [n for n in window.panes.names() if n.startswith("demo")],
        entries(window.plugins_menu),
        len(window.trace.classifiers),
        "gauge" in field_widgets.BY_KIND,
    )

    window._reload_plugins()
    settle(app)
    after = (
        [n for n in window.panes.names() if n.startswith("demo")],
        entries(window.plugins_menu),
        len(window.trace.classifiers),
        "gauge" in field_widgets.BY_KIND,
    )
    assert before == after


def test_reloading_actually_runs_the_new_code(app, window):
    """The reason for all of it: editing a plugin and pressing reload is how
    one gets written."""
    write_plugin(window, "demo", PANE.format(what="one"))
    window._reload_plugins()
    settle(app)
    assert window.panes.view("demo:screen").text() == "one"

    write_plugin(window, "demo", PANE.format(what="two"))
    window._reload_plugins()
    settle(app)
    assert window.panes.view("demo:screen").text() == "two"


def test_a_plugin_that_is_deleted_takes_its_pane_with_it(app, window):
    write_plugin(window, "demo", PANE.format(what="hello"))
    window._reload_plugins()
    settle(app)
    assert "demo:screen" in window.panes.docks

    (window.ctx.workspace_dir / "plugins" / "demo" / "plugin.py").unlink()
    window._reload_plugins()
    settle(app)
    assert "demo:screen" not in window.panes.docks
    assert "Demo screen" not in [a.text() for a in window.view_menu.actions()]


def test_unregistering_removes_the_first_instance_too(app, window):
    """remove() protects the first of a kind because that one is the pane.
    That does not apply when the kind itself is going."""
    write_plugin(window, "demo", PANE.format(what="hello"))
    window._reload_plugins()
    settle(app)
    window.panes.unregister("demo:screen")
    assert "demo:screen" not in window.panes.docks
    assert "demo:screen" not in window.panes.kinds


# --- and it belongs to the workspace --------------------------------------------------------------
def test_a_plugin_belongs_to_the_workspace_that_holds_it(app, window):
    """A plugin is code that gives a product's objects meaning, the same as a
    hook, so it travels with that product."""
    from pycangui.core import workspaces

    write_plugin(window, "demo", PANE.format(what="hello"))
    window._reload_plugins()
    settle(app)
    assert "demo" in window.plugins.found()

    workspaces.create("other")
    workspaces.set_active("other")
    second = MainWindow()
    assert "demo" not in second.plugins.found()
    second.close()


def test_a_plugin_pane_comes_back_where_it_was_left(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    first = MainWindow()
    first.show()
    settle(app)
    write_plugin(first, "demo", PANE.format(what="hello"))
    first._reload_plugins()
    settle(app)
    first.panes.docks["demo:screen"].setVisible(True)
    settle(app)
    first.close()
    settle(app)

    second = MainWindow()
    second.show()
    settle(app)
    assert second.panes.docks["demo:screen"].isVisible(), "open when it was left open"
    second.close()


# --- installing one ------------------------------------------------------------------------
def package_of(source: str, tmp_path, name: str = "demo") -> Path:
    """A plugin package, as somebody would send you one."""
    folder = tmp_path / "sent" / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "plugin.py").write_text(source, encoding="utf-8")
    return plugin_package.pack(folder, tmp_path / f"{name}.zip")


@pytest.fixture
def agrees(monkeypatch):
    """Somebody who reads the question and says yes."""
    asked = []

    def answer(_parent, title, text, *_a, **_k):
        asked.append((title, text))
        return QMessageBox.Yes

    monkeypatch.setattr(plugin_manager.messages, "warning", answer)
    return asked


@pytest.fixture
def refuses(monkeypatch):
    monkeypatch.setattr(plugin_manager.messages, "warning", lambda *_a, **_k: QMessageBox.Cancel)


def chooses(monkeypatch, path):
    monkeypatch.setattr(folders, "open_file", lambda *_a, **_k: str(path))


def test_a_package_is_unpacked_into_the_workspace_and_loaded(
    app, window, tmp_path, agrees, monkeypatch
):
    """The whole point of the format: one file, and no instructions about where
    to put it."""
    chooses(monkeypatch, package_of(PANE.format(what="hello"), tmp_path))
    window.plugin_actions.install_file()
    settle(app)

    assert (window.ctx.workspace_dir / "plugins" / "demo" / "plugin.py").is_file()
    assert "Demo" in [r.label for r in window.plugins.working()]
    assert "demo:screen" in window.panes.docks


def test_what_it_is_about_to_run_is_said_before_it_runs_it(
    app, window, tmp_path, agrees, monkeypatch
):
    """The one thing pycangui does that runs somebody else's code on purpose."""
    chooses(monkeypatch, package_of(PANE.format(what="hello"), tmp_path))
    window.plugin_actions.install_file()
    _title, text = agrees[0]
    assert text


def test_saying_no_installs_nothing(app, window, tmp_path, refuses, monkeypatch):
    chooses(monkeypatch, package_of(PANE.format(what="hello"), tmp_path))
    window.plugin_actions.install_file()
    settle(app)
    assert not (window.ctx.workspace_dir / "plugins" / "demo").exists()
    assert window.plugins.loaded == {}


def test_the_pane_of_one_just_installed_is_shown(app, window, tmp_path, agrees, monkeypatch):
    """Every other pane opens hidden, because a plugin's screen is one among a
    dozen. The one you have this second asked for is the exception."""
    chooses(monkeypatch, package_of(PANE.format(what="hello"), tmp_path))
    window.plugin_actions.install_file()
    settle(app)
    assert window.panes.docks["demo:screen"].isVisible()


def test_a_file_that_is_not_a_plugin_is_refused_with_a_reason(
    app, window, tmp_path, agrees, monkeypatch
):
    (tmp_path / "notes.txt").write_text("hello", encoding="utf-8")
    chooses(monkeypatch, tmp_path / "notes.txt")
    window.plugin_actions.install_file()
    settle(app)
    assert "not a zip" in window.log.toPlainText()
    assert window.plugins.loaded == {}


def test_a_plugin_can_be_written_back_out_as_a_package(app, window, tmp_path, agrees, monkeypatch):
    """Which is how one of yours gets to somebody else, and the only way the
    format exists in both directions."""
    write_plugin(window, "demo", PANE.format(what="hello"))
    window._reload_plugins()
    monkeypatch.setattr(folders, "save_file", lambda *_a, **_k: str(tmp_path / "out.zip"))
    window.plugin_actions.export("demo")

    assert plugin_package.inspect(tmp_path / "out.zip").info.title == "Demo"


def test_removing_one_takes_its_folder_and_its_pane(app, window, agrees):
    write_plugin(window, "demo", PANE.format(what="hello"))
    window._reload_plugins()
    settle(app)
    assert "demo:screen" in window.panes.docks

    window.plugin_actions.uninstall("demo")
    settle(app)
    assert not (window.ctx.workspace_dir / "plugins" / "demo").exists()
    assert "demo:screen" not in window.panes.docks
    assert window.plugins.loaded == {}


# --- and switching one off ---------------------------------------------------------------------
def test_switched_off_means_not_loaded_at_all(app, window):
    """Not merely hidden. Off should leave the window exactly as it would be
    if the plugin were not there."""
    write_plugin(window, "demo", EVERYTHING)
    window._reload_plugins()
    settle(app)

    window.plugin_actions.set_active("demo", False)
    settle(app)
    assert "demo:screen" not in window.panes.docks
    assert window.plugins.working() == []
    assert "Demo screen" not in [a.text() for a in window.view_menu.actions()]
    assert len(window.trace.classifiers) == 5, "and its trace labeller went too"


def test_one_switched_off_is_still_listed(app, window):
    """A plugin somebody turned off six months ago should be findable, not
    mysteriously absent."""
    write_plugin(window, "demo", QUIET)
    window._reload_plugins()
    window.plugin_actions.set_active("demo", False)
    settle(app)
    assert [r.label for r in window.plugins.inactive()] == ["Quiet"]


def test_switching_it_back_on_brings_everything_back(app, window):
    write_plugin(window, "demo", PANE.format(what="hello"))
    window._reload_plugins()
    window.plugin_actions.set_active("demo", False)
    settle(app)
    window.plugin_actions.set_active("demo", True)
    settle(app)
    assert window.panes.view("demo:screen").text() == "hello"


def test_a_plugin_switched_off_stays_off_next_time(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    first = MainWindow()
    first.show()
    settle(app)
    write_plugin(first, "demo", PANE.format(what="hello"))
    first._reload_plugins()
    first.plugin_actions.set_active("demo", False)
    settle(app)
    first.close()

    second = MainWindow()
    second.show()
    settle(app)
    assert "demo:screen" not in second.panes.docks
    assert [r.label for r in second.plugins.inactive()] == ["Demo"]
    second.close()


WATCHES = """
from PySide6.QtWidgets import QLabel

NAME = "Watcher"
SEEN = []


def register(app):
    app.add_pane("screen", "Watched", lambda name: QLabel("hi"))
    app.on_pane_shown(lambda name, on: SEEN.append((name, on)))
"""


def test_a_plugin_can_be_told_when_its_pane_is_put_away(app, window):
    """A pane that polls a bus should stop while nobody can see it: every read
    is a round trip on somebody's equipment."""
    import sys

    write_plugin(window, "watcher", WATCHES)
    window._reload_plugins()
    settle(app)
    seen = sys.modules["pycangui_plugins.watcher"].SEEN

    window.panes.show("watcher:screen")
    settle(app)
    window.panes.docks["watcher:screen"].hide()
    settle(app)
    assert ("watcher:screen", True) in seen
    assert seen[-1] == ("watcher:screen", False)


def test_it_hears_about_its_own_panes_and_no_others(app, window):
    """So that a plugin need not filter out the ones it has never heard of."""
    import sys

    write_plugin(window, "watcher", WATCHES)
    window._reload_plugins()
    settle(app)
    seen = sys.modules["pycangui_plugins.watcher"].SEEN
    seen.clear()

    window.panes.docks["log"].hide()
    settle(app)
    assert seen == []


# --- a plugin that is more than one file ---------------------------------------------------------
SPLIT_ENTRY = """
from PySide6.QtWidgets import QLabel

from . import helper

NAME = "Split"


def register(app):
    app.add_pane("screen", "Split", lambda name: QLabel(helper.WHAT))
"""


def test_a_plugin_can_be_split_across_files(app, window):
    """A folder rather than a single file is the whole reason a plugin is a
    folder, and it is worth nothing if the second file cannot be reached."""
    folder = write_plugin(window, "split", SPLIT_ENTRY)
    (folder / "helper.py").write_text("WHAT = 'from the helper'\n", encoding="utf-8")
    window._reload_plugins()
    settle(app)

    assert window.plugins.errors() == {}
    assert window.panes.view("split:screen").text() == "from the helper"


def test_reloading_picks_up_an_edit_to_the_second_file_too(app, window):
    """Otherwise the entry file is reloaded into the old version of its own
    modules, which is the opposite of what reload is for."""
    folder = write_plugin(window, "split", SPLIT_ENTRY)
    (folder / "helper.py").write_text("WHAT = 'first'\n", encoding="utf-8")
    window._reload_plugins()
    settle(app)
    assert window.panes.view("split:screen").text() == "first"

    (folder / "helper.py").write_text("WHAT = 'second'\n", encoding="utf-8")
    window._reload_plugins()
    settle(app)
    assert window.panes.view("split:screen").text() == "second"


# --- versions ---------------------------------------------------------------------------
def test_the_versions_are_compared_not_the_names(app):
    from pycangui.ui.plugin_manager import _older

    assert _older("1.0", "1.1"), "installed 1.0, offered 1.1"
    assert not _older("1.1", "1.0")
    assert not _older("1.0", "1.0")
    assert not _older("dated", "dated-2"), "and what cannot be compared is not remarked on"


# --- pycangui's own traffic: paused, and stopped -----------------------------------------
PAUSING = """
NAME = "Pausing"
API_VERSION = 2

heard = []


def register(app):
    app.on_pause(lambda on: heard.append(("pause", on)))
    app.on_stop_all(lambda: heard.append(("stop",)))
    app.add_menu_action("Flash", lambda: app.hold_traffic("Flashing"), "")
    app.add_menu_action("Done", lambda: app.release_traffic("Flashing"), "")
    app.heard = heard
"""


def test_a_plugin_hears_of_a_pause_and_of_stop_all_and_can_hold_one(app, window):
    write_plugin(window, "pausing", PAUSING)
    window._reload_plugins()
    settle(app)
    record = window.plugins.loaded["pausing"]
    assert record.ok, record.error
    heard = record.app.heard

    pause = window.ctx.pause
    pause.set_by_hand(True)
    pause.set_by_hand(False)
    pause.stop_all()
    assert heard == [("pause", True), ("pause", False), ("stop",)]

    record.app.hold_traffic("Flashing")
    assert pause.holders == ["Flashing"]
    assert not window.pause_status.pause_action.isEnabled(), "not lifted by hand mid-flash"
    window._reload_plugins()  # unloaded holding it
    settle(app)
    assert not pause.paused, "a plugin gone gives back the pause it held"
    count = len(heard)
    pause.stop_all()
    assert len(heard) == count, "and the old code is no longer called"


# --- what a plugin needs of pycangui without importing it (API 3) -----------------------
NOTHING_IMPORTED = """
NAME = "Nothing imported"
API_VERSION = 3

from PySide6.QtWidgets import QPushButton


def register(app):
    app.button = app.with_icon(QPushButton("Go"), "start")
    app.font_given = app.mono_font()
    app.emcy = app.emcy_text(0x2310)
    app.polling = app.poller()
"""


def test_a_plugin_has_icons_the_font_emcy_text_and_polling_from_the_app(app, window):
    from pycangui.canopen.emcy import describe_code
    from pycangui.custom_panes.polling import DEFAULT_HZ, MAX_HZ, MIN_HZ
    from pycangui.ui import fonts, icons

    write_plugin(window, "nothing_imported", NOTHING_IMPORTED)
    window._reload_plugins()
    settle(app)
    record = window.plugins.loaded["nothing_imported"]
    assert record.ok, record.error
    given = record.app
    assert given.button.property(icons.PROPERTY) == "start", "redrawn with the theme"
    assert given.font_given.family() == fonts.mono().family()
    assert given.emcy == describe_code(0x2310)
    polling = given.polling
    assert (polling.MIN_HZ, polling.DEFAULT_HZ, polling.MAX_HZ) == (MIN_HZ, DEFAULT_HZ, MAX_HZ)
    seen = []
    polling.asked.connect(seen.append)
    polling.set_objects([(0x6041, 0)])
    polling.start()
    settle(app)
    assert seen and seen[0] == (0x6041, 0)
    assert polling.rate_text(), "something to show while it runs"
    polling.stop()
    assert polling.rate_text() == ""


DEVICES = """
NAME = "Devices"
API_VERSION = 3

heard = []


def register(app):
    app.on_devices_changed(lambda: heard.append([d.key for d in app.devices()]))
    app.heard = heard
"""


def test_a_plugin_is_told_of_devices_and_stops_being_told_once_unloaded(app, window, monkeypatch):
    write_plugin(window, "devices", DEVICES)
    window._reload_plugins()
    settle(app)
    record = window.plugins.loaded["devices"]
    assert record.ok, record.error
    heard = record.app.heard
    assert record.app.device(5) is window.devices.device(5)

    monkeypatch.setattr(window.canopen, "nodes", lambda: [5])
    window.canopen.node_seen.emit(5, "OPERATIONAL")
    assert heard == [[5]]

    window._reload_plugins()
    settle(app)
    monkeypatch.setattr(window.canopen, "nodes", lambda: [5, 6])
    window.canopen.node_seen.emit(6, "OPERATIONAL")
    assert heard == [[5]], "the unloaded plugin's callback is not called"


PROVIDER = """
NAME = "Provider"
API_VERSION = 3

from PySide6.QtCore import QObject, Signal


class Thing(QObject):
    changed = Signal()
    state_changed = Signal()
    leaving = Signal()
    label = "Thing"
    states = ()

    def __init__(self, key):
        super().__init__()
        self.key = key
        self.up = True

    def available(self):
        return self.up


class Provider(QObject):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.things = [Thing(("provider", 1))]

    def devices(self):
        return list(self.things)

    def device(self, key):
        return next((thing for thing in self.things if thing.key == key), None)


def register(app):
    app.provider = Provider()
    app.add_device_provider(app.provider)
"""


def provided(app, window):
    write_plugin(window, "provider", PROVIDER)
    window._reload_plugins()
    settle(app)
    record = window.plugins.loaded["provider"]
    assert record.ok, record.error
    return record.app.provider


def test_a_plugins_devices_come_after_the_nodes_and_are_found_by_key(app, window, monkeypatch):
    monkeypatch.setattr(window.canopen, "nodes", lambda: [5])
    provider = provided(app, window)
    thing = provider.things[0]
    assert window.devices.devices() == [window.devices.device(5), thing]
    assert window.devices.device(("provider", 1)) is thing
    assert window.devices.device(("provider", 2)) is None

    heard = []
    window.devices.changed.connect(lambda: heard.append(len(window.devices.devices())))
    provider.things.append(type(thing)(("provider", 2)))
    provider.changed.emit()
    assert heard == [3], "the provider's changed is the list's"


def test_a_plugins_device_with_a_node_id_for_a_key_is_left_out(app, window):
    provider = provided(app, window)
    provider.things.append(type(provider.things[0])(5))
    assert window.devices.devices() == provider.things[:1]
    assert window.devices.device(5) is not provider.things[1], "5 is a CANopen node"


def test_unloading_the_plugin_says_leaving_while_the_device_is_still_there(app, window):
    provider = provided(app, window)
    thing = provider.things[0]
    gone = provider.things.append(type(thing)(("provider", 2))) or provider.things[1]
    gone.up = False
    said = []
    thing.leaving.connect(lambda: said.append(window.devices.device(thing.key) is thing))
    gone.leaving.connect(lambda: said.append("not reachable, so not said"))
    heard = []
    window.devices.changed.connect(lambda: heard.append(window.devices.devices()))

    window.plugins.unload("provider")
    settle(app)
    assert said == [True], "said once, while it was still in the list"
    assert heard == [[]] and window.devices.device(thing.key) is None


NAMED = """
NAME = "Named"
API_VERSION = 3


class Thing:
    pass


def register(app):
    app.thing = Thing()
    app.add_console_name("thing", app.thing)
    app.settings.set("rate", 5)
"""


def test_a_plugin_has_settings_of_its_own_in_the_workspace(app, window):
    write_plugin(window, "named", NAMED)
    window._reload_plugins()
    settle(app)
    record = window.plugins.loaded["named"]
    assert record.ok, record.error
    assert record.app.settings.get("rate") == 5
    assert record.app.settings.keys() == ["rate"]
    assert window.ctx.settings.get("plugins.named.rate") == 5, "under its own name"
    record.app.settings.remove("rate")
    assert window.ctx.settings.get("plugins.named.rate") is None


def test_a_plugins_console_name_is_there_until_it_is_unloaded(app, window):
    write_plugin(window, "named", NAMED)
    window._reload_plugins()
    settle(app)
    record = window.plugins.loaded["named"]
    assert record.ok, record.error
    first = record.app.thing
    assert window.console.namespace["thing"] is first
    assert window._console_namespace()["thing"] is first, "and in scripts"

    window._reload_plugins()
    settle(app)
    again = window.plugins.loaded["named"].app.thing
    assert again is not first
    assert window.console.namespace["thing"] is again, "the old object is gone with the old code"


@pytest.mark.parametrize("name", ["canopen", "not a name", "class"])
def test_a_console_name_pycangui_has_or_python_cannot_take_is_refused(app, window, name):
    write_plugin(window, "named", NAMED.replace('"thing", app.thing', f"{name!r}, app.thing"))
    window._reload_plugins()
    settle(app)
    record = window.plugins.loaded["named"]
    assert not record.ok
    assert window._console_namespace()["canopen"] is window.canopen


def test_two_plugins_cannot_take_one_console_name(app, window):
    write_plugin(window, "named", NAMED)
    write_plugin(window, "named_too", NAMED.replace('NAME = "Named"', 'NAME = "Named too"'))
    window._reload_plugins()
    settle(app)
    loaded = window.plugins.loaded
    assert sorted(record.ok for record in (loaded["named"], loaded["named_too"])) == [False, True]


# --- a plugin's own hooks (API 3) ---------------------------------------------------------
HOOKED = """
NAME = "Hooked"
API_VERSION = 3


def register(app):
    app.add_hooks("hooked", "hooks.py")
    app.answer = lambda: app.hooks.call("hooked", "greeting", "Ada")
"""

HOOK_DEFAULTS = '''
def greeting(name, *, ctx):
    """What to call somebody."""
    return f"Hello {name}"


def _helper():
    return "not a hook"
'''


def hooked(window, defaults=HOOK_DEFAULTS, name="hooked", source=HOOKED):
    folder = write_plugin(window, name, source)
    (folder / "hooks.py").write_text(defaults, encoding="utf-8")
    return folder


def test_a_plugins_hooks_are_in_the_workspace_and_called_like_pycanguis(app, window):
    hooked(window)
    window._reload_plugins()
    settle(app)
    record = window.plugins.loaded["hooked"]
    assert record.ok, record.error
    copy = window.ctx.hooks_dir / "hooked.py"
    assert copy.exists(), "beside canopen.py and uds.py"
    assert record.app.answer() == "Hello Ada"
    assert set(registry()["hooked"]) == {"greeting"}, "only what is public is a hook"

    copy.write_text(
        copy.read_text(encoding="utf-8").replace('f"Hello {name}"', 'f"Hi {name}"'),
        encoding="utf-8",
    )
    window.hooks.reload()
    assert record.app.answer() == "Hi Ada", "the user's copy is what runs"


def test_an_edited_copy_is_kept_and_given_a_hook_the_plugin_adds(app, window):
    folder = hooked(window)
    window._reload_plugins()
    settle(app)
    copy = window.ctx.hooks_dir / "hooked.py"
    copy.write_text(copy.read_text(encoding="utf-8") + "\n# mine\n", encoding="utf-8")

    grown = (
        HOOK_DEFAULTS
        + """

def farewell(name, *, ctx):
    return f"Bye {name}"
"""
    )
    (folder / "hooks.py").write_text(grown, encoding="utf-8")
    window._reload_plugins()
    settle(app)
    assert window.plugins.loaded["hooked"].ok
    text = copy.read_text(encoding="utf-8")
    assert "# mine" in text and "def farewell" in text
    assert window.hooks.call("hooked", "farewell", "Ada") == "Bye Ada"


def test_unloading_the_plugin_forgets_its_hooks_and_leaves_the_file(app, window):
    folder = hooked(window)
    window._reload_plugins()
    settle(app)
    (folder / "plugin.py").unlink()
    window._reload_plugins()
    settle(app)
    assert "hooked" not in registry()
    assert (window.ctx.hooks_dir / "hooked.py").exists(), "the user's edits, kept"


def test_a_hook_module_pycangui_or_another_plugin_has_is_refused(app, window):
    hooked(window, source=HOOKED.replace('add_hooks("hooked"', 'add_hooks("canopen"'))
    hooked(window, name="hooked_too")
    hooked(window, name="hooked_three")
    window._reload_plugins()
    settle(app)
    loaded = window.plugins.loaded
    assert not loaded["hooked"].ok, "pycangui's own canopen hooks"
    assert sorted(loaded[n].ok for n in ("hooked_too", "hooked_three")) == [False, True]
    assert "eds_for_node" in registry()["canopen"], "and pycangui's are untouched"


# --- packages a plugin needs (API 3) -------------------------------------------------------
WANTING = """
NAME = "Wanting"
API_VERSION = 3
REQUIRES = ["no-such-package-here>=1"]


def register(app):
    pass
"""


def test_what_a_plugin_needs_is_offered_with_pip_and_it_is_loaded_again(app, window, monkeypatch):
    write_plugin(window, "wanting", WANTING)
    window._reload_plugins()
    settle(app)
    actions = window.plugin_actions
    assert actions.plugins.loaded["wanting"].missing == ["no-such-package-here>=1"]

    monkeypatch.setattr(plugin_manager.installation, "how_installed", lambda: "pip")
    monkeypatch.setattr(plugin_manager.messages, "question", lambda *a, **k: QMessageBox.Yes)
    reloaded = []
    actions.changed.connect(reloaded.append)
    pip = [sys.executable, "-c", "pass"]  # standing in for pip, which succeeds
    assert actions.offer_packages("wanting", window, command=pip)
    assert reloaded == ["wanting"], "loaded again, now that it is there"


def test_the_build_with_no_pip_says_what_is_missing_instead(app, window, monkeypatch):
    write_plugin(window, "wanting", WANTING)
    window._reload_plugins()
    settle(app)
    monkeypatch.setattr(
        plugin_manager.installation, "how_installed", lambda: plugin_manager.installation.FROZEN
    )
    told = []
    monkeypatch.setattr(plugin_manager.messages, "information", lambda *a, **k: told.append(a))
    asked = []
    monkeypatch.setattr(plugin_manager.messages, "question", lambda *a, **k: asked.append(a))
    assert not window.plugin_actions.offer_packages("wanting", window)
    assert told and not asked, "nothing to offer: told, not asked"


# --- a program of its own beside pycangui (API 3) ------------------------------------------
FAKE_HELPER = """
import sys
from pathlib import Path

mode = sys.argv[1]
if mode == "silent":
    sys.stdin.read()
    sys.exit(0)
print(4711 if mode != "garbled" else "hello", flush=True)
print("listening", flush=True)
print("a warning", file=sys.stderr, flush=True)
if mode == "dies":
    sys.exit(3)
sys.stdin.read()  # until pycangui closes it: the polite way to be asked to stop
Path("stopped").write_text("asked", encoding="utf-8")
"""

HELPED = """
NAME = "Helped"
API_VERSION = 3


def register(app):
    app.ports = []
    app.exits = []
    app.made = app.helper("fake_helper.py", "{mode}", on_ready=app.ports.append{extra})
"""


def helped(app, window, mode="serves", extra=""):
    folder = write_plugin(window, "helped", HELPED.format(mode=mode, extra=extra))
    (folder / "fake_helper.py").write_text(FAKE_HELPER, encoding="utf-8")
    window._reload_plugins()
    settle(app)
    record = window.plugins.loaded["helped"]
    assert record.ok, record.error
    return folder, record.app


def wait_for(app, condition, seconds=10.0):
    import time

    end = time.monotonic() + seconds
    while not condition() and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    return condition()


def test_a_helper_hands_over_its_port_and_its_output_goes_to_the_event_log(app, window):
    levels = []
    window.ctx.events.posted.connect(lambda _message, level: levels.append(level))
    folder, plugin = helped(app, window)
    assert wait_for(app, lambda: plugin.ports == [4711])
    assert plugin.made.port == 4711
    assert wait_for(app, lambda: "warning" in levels), "what it wrote to stderr"
    window._reload_plugins()  # unloading it stops its helper
    settle(app)
    assert not plugin.made.running
    assert (folder / "stopped").exists(), "asked by its stdin closing, not killed"


CLOSING_WITH_A_HELPER = """
NAME = "Closing"
API_VERSION = 3


def register(app):
    app.ports = []
    app.running_then = []
    app.made = app.helper("fake_helper.py", "serves", on_ready=app.ports.append)
    app.on_closing(lambda: app.running_then.append(app.made.running))
"""


def test_a_helper_is_still_running_for_every_plugins_last_word_on_closing(app, window):
    """Its own, and another plugin's: a drive another plugin provides is
    stopped through this helper, on ``closing``."""
    folder = write_plugin(window, "closing", CLOSING_WITH_A_HELPER)
    (folder / "fake_helper.py").write_text(FAKE_HELPER, encoding="utf-8")
    window._reload_plugins()
    settle(app)
    plugin = window.plugins.loaded["closing"].app
    assert wait_for(app, lambda: plugin.ports == [4711])
    window.close()
    settle(app)
    assert plugin.running_then == [True]
    assert not plugin.made.running


def test_a_helper_is_stopped_when_pycangui_closes(app, window):
    folder, plugin = helped(app, window)
    assert wait_for(app, lambda: plugin.ports == [4711])
    window.close()
    settle(app)
    assert not plugin.made.running
    assert (folder / "stopped").exists()


def test_a_helper_that_stops_on_its_own_is_offered_a_restart(app, window, monkeypatch):
    from pycangui.ui import plugin_helper

    asked = []

    def answer(*_args, **_kwargs):
        asked.append(True)
        return QMessageBox.Yes if len(asked) == 1 else QMessageBox.No

    monkeypatch.setattr(plugin_helper.messages, "question", answer)
    _folder, plugin = helped(app, window, mode="dies")
    assert wait_for(app, lambda: len(asked) == 2), "asked, restarted, and asked again"
    assert plugin.ports == [4711, 4711]
    assert not plugin.made.running


def test_a_plugin_can_deal_with_its_helper_stopping_itself(app, window, monkeypatch):
    from pycangui.ui import plugin_helper

    monkeypatch.setattr(plugin_helper.messages, "question", lambda *_a, **_k: pytest.fail("asked"))
    _folder, plugin = helped(app, window, mode="dies", extra=", on_exit=app.exits.append")
    assert wait_for(app, lambda: plugin.exits == [3])


def test_a_helper_that_prints_no_port_is_stopped(app, window):
    _folder, plugin = helped(app, window, mode="silent", extra=", timeout=0.3")
    assert wait_for(app, lambda: not plugin.made.running)
    assert plugin.ports == []
    _folder, plugin = helped(app, window, mode="garbled")
    assert wait_for(app, lambda: not plugin.made.running)
    assert plugin.ports == [] and plugin.made.port is None


def test_a_helper_that_serves_nothing_is_ready_once_started(app, window):
    _folder, plugin = helped(app, window, mode="silent", extra=", port=False")
    assert wait_for(app, lambda: plugin.ports == [None])
    assert plugin.made.running


def test_a_python_helper_is_refused_in_the_build_without_python(tmp_path, monkeypatch):
    from pycangui.core import installation
    from pycangui.ui import plugin_helper

    (tmp_path / "helper.py").write_text("", encoding="utf-8")
    (tmp_path / "helper.exe").write_bytes(b"")
    assert plugin_helper.command(tmp_path, "helper.py")[1:] == [str(tmp_path / "helper.py")]
    monkeypatch.setattr(installation, "how_installed", lambda: installation.FROZEN)
    with pytest.raises(ValueError):
        plugin_helper.command(tmp_path, "helper.py")
    with pytest.raises(FileNotFoundError):
        plugin_helper.command(tmp_path, "nothing_here")
