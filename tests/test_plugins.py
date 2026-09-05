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

from pycangui.core.plugins import API_VERSION, Plugins
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
    assert set(Plugins(folders=[tmp_path]).found()) == {"demo"}


def test_a_loose_file_is_not(tmp_path):
    """A folder, so a plugin can bring its own modules and data with it."""
    (tmp_path / "plugin.py").write_text("NAME = 'Nope'", encoding="utf-8")
    (tmp_path / "empty").mkdir()
    assert Plugins(folders=[tmp_path]).found() == {}


def test_folders_that_are_not_meant_to_be_plugins_are_skipped(tmp_path):
    for name in ("__pycache__", ".git"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "plugin.py").write_text("NAME = 'x'", encoding="utf-8")
    assert Plugins(folders=[tmp_path]).found() == {}


def test_a_users_plugin_replaces_a_shipped_one_of_the_same_name(tmp_path):
    """The same rule as a hook file: yours wins."""
    shipped, mine = tmp_path / "shipped", tmp_path / "mine"
    for folder in (shipped, mine):
        (folder / "demo").mkdir(parents=True)
        (folder / "demo" / "plugin.py").write_text("NAME = 'x'", encoding="utf-8")
    found = Plugins(folders=[shipped, mine]).found()
    assert found["demo"] == mine / "demo" / "plugin.py"


# --- loading them -----------------------------------------------------------------------
def test_a_plugin_gets_to_add_things(app, window):
    write_plugin(window, "demo", EVERYTHING)
    window._reload_plugins()
    settle(app)

    assert [r.label for r in window.plugins.working()] == ["Demo"]
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
    assert entries(window.plugins_menu)[:2] == ["Demo", "Quiet"]


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
    assert entries(window.plugins_menu) == [
        "No plugins installed",
        "Reload plugins",
        "Open plugins folder",
    ]


def test_reloading_and_the_folder_are_in_the_plugins_menu(app, window):
    assert entries(window.plugins_menu)[-2:] == ["Reload plugins", "Open plugins folder"]


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
    assert [r.label for r in window.plugins.working()] == ["Demo"], "the good one still loaded"
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
