# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""User code that adds screens, and what happens when it goes wrong.

A hook answers a question pycangui already knows to ask.  A plugin adds
something that was not there.  Two properties matter more than any of the
features, and most of this file is about them: a plugin that fails takes only
itself down, and reloading one really does remove what the last version added
rather than leaving a second copy beside it.
"""

from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QMessageBox

from pycangui.core import plugin_package
from pycangui.core.plugins import API_VERSION, Plugins, supplied
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


def test_nothing_pycangui_ships_is_loaded_until_it_is_installed(app, window):
    """The catalogue is not a load path.  A screen nobody asked for in every
    window is exactly what installing is there to prevent."""
    assert window.plugins.loaded == {}
    assert [s.name for s in supplied()], "and yet there are some to install"


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
        "Supplied with pycangui",
        "Manage plugins...",
        "Open plugins folder",
        "Reload plugins",
    ]


def test_reloading_and_the_folder_are_in_the_plugins_menu(app, window):
    """In the same order as the hook pair in Tools: open, edit, reload."""
    assert entries(window.plugins_menu)[-2:] == ["Open plugins folder", "Reload plugins"]


def test_a_plugin_menu_entry_says_whose_it_is(app, window):
    write_plugin(window, "demo", EVERYTHING)
    window._reload_plugins()
    settle(app)
    assert entries(window.plugins_menu)[0] == "Demo"
    assert entries(window.plugin_menu("demo")) == ["Say hello"]


def test_a_plugin_can_name_frames_in_every_trace(app, window):
    """A second trace showing different names from the first would be a puzzle
    rather than a feature."""
    write_plugin(window, "demo", EVERYTHING)
    window._reload_plugins()
    settle(app)
    later = window.panes.view(window.panes.add("trace"))
    assert len(later.classifiers) == len(window.trace.classifiers)
    assert len(window.trace.classifiers) == 5, "the four built in, plus the plugin's"


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

    monkeypatch.setattr(plugin_manager.QMessageBox, "warning", answer)
    return asked


@pytest.fixture
def refuses(monkeypatch):
    monkeypatch.setattr(plugin_manager.QMessageBox, "warning", lambda *_a, **_k: QMessageBox.Cancel)


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
    assert "Python that runs as part of pycangui" in text


def test_saying_no_installs_nothing(app, window, tmp_path, refuses, monkeypatch):
    chooses(monkeypatch, package_of(PANE.format(what="hello"), tmp_path))
    window.plugin_actions.install_file()
    settle(app)
    assert not (window.ctx.workspace_dir / "plugins" / "demo").exists()
    assert window.plugins.loaded == {}


def test_the_pane_of_one_just_installed_is_shown(app, window, tmp_path, agrees, monkeypatch):
    """Every other pane opens hidden, because a plugin's screen is one among a
    dozen.  The one you have this second asked for is the exception."""
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


def test_one_pycangui_ships_can_be_installed_from_the_menu(app, window, agrees):
    """And is then an ordinary plugin in the workspace, which is the copy that
    runs -- so editing it is editing yours rather than the installation."""
    window.plugin_actions.install_supplied("firmware")
    settle(app)
    assert (window.ctx.workspace_dir / "plugins" / "firmware" / "program.py").is_file()
    assert "CANopen firmware (CiA 302-3)" in [r.label for r in window.plugins.working()]


def test_what_is_already_installed_is_not_offered_again(app, window, agrees):
    assert "firmware" in [s.name for s in window.plugin_actions.not_installed()]
    window.plugin_actions.install_supplied("firmware")
    settle(app)
    assert "firmware" not in [s.name for s in window.plugin_actions.not_installed()]


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
    """Not merely hidden.  Off should leave the window exactly as it would be
    if the plugin were not there."""
    write_plugin(window, "demo", EVERYTHING)
    window._reload_plugins()
    settle(app)

    window.plugin_actions.set_active("demo", False)
    settle(app)
    assert "demo:screen" not in window.panes.docks
    assert window.plugins.working() == []
    assert "Demo screen" not in [a.text() for a in window.view_menu.actions()]
    assert len(window.trace.classifiers) == 4, "and its trace labeller went too"


def test_one_switched_off_is_still_listed(app, window):
    """A plugin somebody turned off six months ago should be findable, not
    mysteriously absent."""
    write_plugin(window, "demo", QUIET)
    window._reload_plugins()
    window.plugin_actions.set_active("demo", False)
    settle(app)
    assert "Quiet (switched off)" in entries(window.plugins_menu)
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


def test_the_copy_it_reaches_is_the_one_beside_it(app, window, agrees):
    """The point of installing into the workspace: the copy you edit is the
    copy that runs.  Named absolutely, an installed plugin would reach back
    into the one pycangui ships and editing your own would do nothing."""
    window.plugin_actions.install_supplied("firmware")
    settle(app)
    installed = window.ctx.workspace_dir / "plugins" / "firmware" / "program.py"
    installed.write_text(
        installed.read_text(encoding="utf-8").replace("BLOCK = 1024", "BLOCK = 7"),
        encoding="utf-8",
    )
    window._reload_plugins()
    settle(app)

    import sys

    assert sys.modules["pycangui_plugins.firmware.program"].BLOCK == 7
    from pycangui.plugins.firmware import program

    assert program.BLOCK == 1024, "and the one pycangui ships is untouched"


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
