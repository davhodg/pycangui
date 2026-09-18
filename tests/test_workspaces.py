# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""One workspace per product, and one called default nobody has to meet.

The load-bearing test here is the migration. Somebody who never asked for
workspaces, and who will never open the menu, must not be able to tell that
this was built: their settings, their hooks and their EDS files become the
``default`` workspace in place, and everything carries on. Done any other way
the feature announces itself by losing an existing setup.
"""

import json
import shutil

import pytest
from PySide6.QtCore import QSettings, Qt

from pycangui import APP_NAME, __version__
from pycangui.core import paths, workspaces
from pycangui.core.context import Context
from pycangui.core.layout import Layout
from pycangui.ui import messages


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    return tmp_path


def older_setup(home):
    """A pycangui folder as it was before workspaces existed."""
    (home / "settings.json").write_text('{"dbc.paths": ["a.dbc"]}', encoding="utf-8")
    (home / "hooks").mkdir()
    (home / "hooks" / "canopen.py").write_text("# mine\n", encoding="utf-8")
    (home / "eds").mkdir()
    (home / "eds" / "drive.eds").write_text("[FileInfo]\n", encoding="utf-8")
    (home / "backends").mkdir()
    (home / "backends" / "mine.py").write_text("# a back end\n", encoding="utf-8")


# --- the setup that came before ---------------------------------------------------------
def test_an_existing_setup_becomes_the_default_workspace(home):
    older_setup(home)
    moved = workspaces.migrate()

    assert set(moved) == {"settings.json", "hooks", "eds"}
    default = workspaces.dir_for(workspaces.DEFAULT)
    assert json.loads((default / "settings.json").read_text())["dbc.paths"] == ["a.dbc"]
    assert (default / "hooks" / "canopen.py").read_text() == "# mine\n"
    assert (default / "eds" / "drive.eds").exists()


def test_nothing_is_left_behind_to_disagree_with_it(home):
    """Moved rather than copied: two of a settings file is one too many."""
    older_setup(home)
    workspaces.migrate()
    assert not (home / "settings.json").exists()
    assert not (home / "hooks").exists()
    assert not (home / "eds").exists()


def test_the_back_ends_stay_where_they_are(home):
    """A back end is about this machine's ability to talk to a bus at all,
    not about the product being worked on, so every workspace shares them."""
    older_setup(home)
    workspaces.migrate()
    assert (home / "backends" / "mine.py").exists()
    assert not (workspaces.dir_for(workspaces.DEFAULT) / "backends").exists()


def test_it_happens_once(home):
    older_setup(home)
    assert workspaces.migrate()
    (home / "settings.json").write_text("{}", encoding="utf-8")  # something new, later
    assert workspaces.migrate() == [], "already done"
    assert (home / "settings.json").exists(), "and the later file was not swept up"


def test_a_fresh_install_just_gets_a_default(home):
    assert workspaces.active() == workspaces.DEFAULT
    assert workspaces.names() == [workspaces.DEFAULT]
    assert workspaces.dir_for(workspaces.DEFAULT).is_dir()


def test_the_context_reads_the_migrated_settings(home):
    older_setup(home)
    ctx = Context(log=print)
    assert ctx.settings.get("dbc.paths") == ["a.dbc"]
    assert ctx.workspace == workspaces.DEFAULT
    assert ctx.hooks_dir == workspaces.dir_for(workspaces.DEFAULT) / "hooks"


# --- which one is in use -------------------------------------------------------------------
def test_default_when_nothing_says_otherwise(home):
    assert workspaces.active() == workspaces.DEFAULT


def test_the_choice_is_remembered(home):
    workspaces.create("Pump controller")
    workspaces.set_active("Pump controller")
    assert workspaces.active() == "Pump controller"


def test_a_pointer_to_a_workspace_that_is_gone_falls_back(home):
    """Deleted by hand, or a file typed wrong: still a working tool."""
    workspaces.create("gone")
    workspaces.set_active("gone")
    shutil.rmtree(workspaces.dir_for("gone"))  # somebody tidying up in Explorer
    assert workspaces.active() == workspaces.DEFAULT


def test_a_damaged_pointer_falls_back_too(home):
    workspaces._ensure()
    (home / workspaces.POINTER).write_text("not json at all", encoding="utf-8")
    assert workspaces.active() == workspaces.DEFAULT


def test_default_is_listed_first(home):
    for name in ("zebra", "alpha"):
        workspaces.create(name)
    assert workspaces.names() == [workspaces.DEFAULT, "alpha", "zebra"]


# --- naming one ------------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["Pump controller", "drive-2", "rev_1.4", "A"])
def test_names_people_would_type(home, name):
    assert workspaces.why_not(name) == ""


@pytest.mark.parametrize(
    "name, because",
    [
        ("", "needs a name"),
        ("   ", "needs a name"),
        ("../escape", "folder name"),
        ("drive/2", "folder name"),
        (".hidden", "folder name"),
        ("con", "Windows"),
        ("LPT1", "Windows"),
        ("x" * 65, "at most"),
    ],
)
def test_names_that_would_not_survive_being_a_folder(home, name, because):
    """A reason rather than a silent tidy-up: they typed it, so tell them."""
    assert because in workspaces.why_not(name)


def test_the_space_around_a_typed_name_is_not_part_of_it(home):
    """Nobody types a trailing space on purpose, and a folder called
    "drive " is one that cannot be found again."""
    assert workspaces.why_not(" drive ") == ""
    workspaces.create(" drive ")
    assert workspaces.exists("drive")


def test_a_name_already_taken_is_refused(home):
    workspaces.create("drive")
    assert "already a workspace" in workspaces.why_not("drive")


def test_creating_a_bad_name_raises_rather_than_making_a_mess(home):
    with pytest.raises(ValueError):
        workspaces.create("../escape")


# --- making, renaming, removing ---------------------------------------------------------------
def test_save_as_keeps_what_was_on_screen(home):
    """Somebody asking for it means "keep this and call it something else"."""
    older_setup(home)
    workspaces._ensure()
    workspaces.create("Pump controller", copy_from=workspaces.DEFAULT)
    forked = workspaces.dir_for("Pump controller")
    assert json.loads((forked / "settings.json").read_text())["dbc.paths"] == ["a.dbc"]
    assert (forked / "hooks" / "canopen.py").exists()


def test_a_fork_is_a_copy_and_not_a_share(home):
    older_setup(home)
    workspaces._ensure()
    workspaces.create("other", copy_from=workspaces.DEFAULT)
    (workspaces.dir_for("other") / "settings.json").write_text("{}", encoding="utf-8")
    default = workspaces.dir_for(workspaces.DEFAULT)
    assert json.loads((default / "settings.json").read_text())["dbc.paths"] == ["a.dbc"]


def test_renaming_takes_the_pointer_with_it(home):
    workspaces.create("old name")
    workspaces.set_active("old name")
    workspaces.rename("old name", "new name")
    assert workspaces.active() == "new name"
    assert not workspaces.exists("old name")


def test_the_default_cannot_be_renamed_or_deleted(home):
    """It is the one that is always there, and something has to be."""
    with pytest.raises(ValueError):
        workspaces.rename(workspaces.DEFAULT, "something else")
    with pytest.raises(ValueError):
        workspaces.delete(workspaces.DEFAULT)


def test_the_one_in_use_cannot_be_deleted(home):
    """Pulling the settings out from under a running window is the worse answer."""
    workspaces.create("drive")
    workspaces.set_active("drive")
    with pytest.raises(ValueError, match="Switch to another one first"):
        workspaces.delete("drive")


def test_deleting_takes_the_folder_with_it(home):
    workspaces.create("scrap")
    workspaces.delete("scrap")
    assert not workspaces.exists("scrap")
    assert "scrap" not in workspaces.names()


# --- what belongs to which -------------------------------------------------------------------
def test_two_workspaces_do_not_share_settings_or_hooks(home):
    first = Context(log=print)
    first.settings.set("dbc.paths", ["one.dbc"])
    (first.hooks_dir / "canopen.py").write_text("# the first\n", encoding="utf-8")

    workspaces.create("second")
    workspaces.set_active("second")
    second = Context(log=print)
    assert second.settings.get("dbc.paths") is None
    assert not (second.hooks_dir / "canopen.py").exists()
    assert second.hooks_dir != first.hooks_dir


def test_the_layout_lives_in_the_workspace_folder(home):
    """So that a workspace is one thing that can be copied or sent on. In
    QSettings it would be the one part of it that could not travel."""
    ctx = Context(log=print)
    ctx.layout.set("window", b"\x01\x02\x03")
    assert (ctx.workspace_dir / "layout.json").exists()
    assert Layout(ctx.workspace_dir / "layout.json").get("window") == b"\x01\x02\x03"


def test_an_empty_layout_is_nothing_rather_than_empty_bytes(home):
    """Qt answers restoreState(b"") by leaving the window with no docks."""
    ctx = Context(log=print)
    assert ctx.layout.get("window") is None
    ctx.layout.set("window", b"")
    assert ctx.layout.get("window") is None


def test_a_damaged_layout_file_is_no_layout_rather_than_a_crash(home):
    ctx = Context(log=print)
    (ctx.workspace_dir / "layout.json").write_text("{ truncated", encoding="utf-8")
    assert Layout(ctx.workspace_dir / "layout.json").get("window") is None


def test_the_user_folder_itself_is_still_the_user_folder(home):
    """Recordings, A2Ls and the scripts people keep there are not disturbed."""
    (home / "capture.blf").write_text("x", encoding="utf-8")
    workspaces._ensure()
    assert (home / "capture.blf").exists()
    assert paths.user_dir() == home


# --- and the window that opens on it -----------------------------------------------------------
def test_an_existing_arrangement_survives_the_upgrade(app, home):
    """The dock layout used to live in QSettings. Somebody who had arranged
    their panes should find them arranged, not reset."""
    from pycangui.ui.main_window import MainWindow

    QSettings().clear()
    first = MainWindow()
    first.show()
    app.processEvents()
    first.panes.docks["canopen"].setVisible(True)
    first.close()  # writes windowState into the workspace
    before = first.ctx.layout.get("window")
    assert before is not None

    # Now pretend that state had been left in QSettings by an older version.
    QSettings().setValue("windowState", before)
    (workspaces.layout_path()).unlink()

    second = MainWindow()
    second.show()  # a dock of a window that was never shown reports itself hidden
    app.processEvents()
    assert second.panes.docks["canopen"].isVisible(), "found where it was left"
    second.close()


def test_a_new_workspace_does_not_inherit_the_old_layout(app, home):
    """A new one that opened with the last one's arrangement would not be new."""
    from pycangui.ui.main_window import MainWindow

    QSettings().clear()
    first = MainWindow()
    first.show()
    app.processEvents()
    first.panes.docks["canopen"].setVisible(True)
    first.close()
    QSettings().setValue("windowState", first.ctx.layout.get("window"))

    workspaces.create("fresh")
    workspaces.set_active("fresh")
    second = MainWindow()
    second.show()
    app.processEvents()
    assert not second.panes.docks["canopen"].isVisible(), "the default arrangement"
    second.close()


# --- the menu, the title and the switch ----------------------------------------------------
@pytest.fixture
def window(app, home):
    from pycangui.ui.main_window import MainWindow

    QSettings().clear()
    win = MainWindow()
    win.show()
    app.processEvents()
    yield win
    win.close()


def switch_menu(window):
    """The Switch to submenu, rebuilt as opening the menu would rebuild it."""
    window.workspace_menu._build()
    return window.workspace_menu.menu.actions()[1].menu()


def test_a_single_product_never_meets_the_word(window):
    """The title says nothing while the workspace is the one everybody has."""
    assert window.windowTitle() == f"{APP_NAME} {__version__}"
    assert [a.text() for a in switch_menu(window).actions()] == [workspaces.DEFAULT]


def test_the_title_names_a_workspace_that_is_not_the_default(app, home):
    from pycangui.ui.main_window import MainWindow

    workspaces.create("Pump controller")
    workspaces.set_active("Pump controller")
    win = MainWindow()
    assert win.windowTitle().endswith(" - Pump controller")
    win.close()


def test_switch_to_lists_them_all_and_ticks_the_one_in_use(window):
    workspaces.create("drive")
    actions = switch_menu(window).actions()
    assert [a.text() for a in actions] == [workspaces.DEFAULT, "drive"]
    assert [a.isChecked() for a in actions] == [True, False]


def test_save_as_forks_what_is_on_screen_and_asks_to_open_it(window, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    window.ctx.settings.set("dbc.paths", ["mine.dbc"])
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Pump controller", True))
    asked: list[str] = []
    window.workspace_menu.switch_requested.connect(asked.append)

    window.workspace_menu._save_as()

    assert asked == ["Pump controller"], "and switching to it is somebody else's job"
    forked = workspaces.dir_for("Pump controller") / "settings.json"
    assert json.loads(forked.read_text())["dbc.paths"] == ["mine.dbc"]


def test_save_as_with_a_name_that_will_not_do_makes_nothing(window, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("../escape", True))
    said: list[str] = []
    monkeypatch.setattr(messages, "warning", lambda _p, _t, text, *a, **k: said.append(text))
    asked: list[str] = []
    window.workspace_menu.switch_requested.connect(asked.append)

    window.workspace_menu._save_as()

    assert asked == []
    assert said and "folder name" in said[0], "told why, rather than quietly repaired"
    assert workspaces.names() == [workspaces.DEFAULT]


def test_cancelling_save_as_does_nothing(window, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("drive", False))
    window.workspace_menu._save_as()
    assert workspaces.names() == [workspaces.DEFAULT]


def test_switching_to_the_one_you_are_in_does_nothing(window):
    asked: list[str] = []
    window.reopen_requested.connect(asked.append)
    window._switch_workspace(workspaces.DEFAULT)
    assert asked == []


def test_switching_with_nothing_connected_does_not_ask(window, monkeypatch):

    monkeypatch.setattr(
        messages, "warning", lambda *a, **k: pytest.fail("nothing to lose, nothing to ask")
    )
    workspaces.create("drive")
    asked: list[str] = []
    window.reopen_requested.connect(asked.append)
    window._switch_workspace("drive")
    assert asked == ["drive"]


def test_switching_off_a_live_bus_asks_first(window, app, monkeypatch):
    """A workspace holds which channels at what bitrate, so opening another
    one drops the bus this one is on."""
    from PySide6.QtWidgets import QMessageBox

    bus = window.channels.active_bus()
    bus.connect_bus("virtual", "vcan_ws", 500_000, False)
    app.processEvents()
    assert window.channels.any_connected

    workspaces.create("drive")
    seen: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "exec",
        lambda box: (seen.append(box.text() + " " + box.informativeText()), QMessageBox.Cancel)[1],
    )
    asked: list[str] = []
    window.reopen_requested.connect(asked.append)

    window._switch_workspace("drive")
    assert asked == [], "cancelled means stay"
    assert seen and "drive" in seen[0]

    monkeypatch.setattr(QMessageBox, "exec", lambda _box: QMessageBox.Yes)
    window._switch_workspace("drive")
    assert asked == ["drive"]
    bus.disconnect_bus()


def test_agreeing_once_covers_the_session(window, app, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    bus = window.channels.active_bus()
    bus.connect_bus("virtual", "vcan_ws2", 500_000, False)
    app.processEvents()
    workspaces.create("drive")
    workspaces.create("other")
    times: list[int] = []
    monkeypatch.setattr(QMessageBox, "exec", lambda _box: (times.append(1), QMessageBox.Yes)[1])
    window._switch_workspace("drive")
    window._switch_workspace("other")
    assert len(times) == 1, "it is the same loss each time"
    bus.disconnect_bus()


def test_what_a_switch_actually_does(app, home):
    """The window is replaced rather than persuaded: the settings, the hooks
    and the layout are all read once, while it is being built."""
    from pycangui.ui.main_window import MainWindow
    from pycangui.ui.session import Session

    QSettings().clear()
    session = Session(MainWindow)
    first = session.open()
    first.ctx.settings.set("dbc.paths", ["first.dbc"])
    workspaces.create("second")

    second = session.reopen("second")
    assert second is not first
    assert not first.isVisible(), "the one that asked is the one that goes"
    assert second.ctx.workspace == "second"
    assert second.ctx.settings.get("dbc.paths") is None, "a workspace of its own"
    assert second.windowTitle().endswith(" - second")

    back = session.reopen(workspaces.DEFAULT)
    assert back.ctx.settings.get("dbc.paths") == ["first.dbc"], "and the first is as it was"
    back.close()


def test_asking_the_window_to_switch_reaches_the_session(app, home):
    """The signal is the whole of the connection between the two."""
    from pycangui.ui.main_window import MainWindow
    from pycangui.ui.session import Session

    QSettings().clear()
    session = Session(MainWindow)
    first = session.open()
    workspaces.create("drive")

    first.reopen_requested.emit("drive")
    app.processEvents()
    assert session.window is not first
    assert session.window.ctx.workspace == "drive"
    assert workspaces.active() == "drive", "and the choice is remembered"
    session.window.close()


def test_a_switch_writes_the_old_layout_where_it_belongs(app, home):
    """Closed before the pointer moves, or the arrangement somebody was
    looking at lands in the workspace they were leaving for."""
    from pycangui.ui.main_window import MainWindow
    from pycangui.ui.session import Session

    QSettings().clear()
    session = Session(MainWindow)
    first = session.open()
    app.processEvents()
    first.panes.docks["canopen"].setVisible(True)
    workspaces.create("drive")

    second = session.reopen("drive")
    app.processEvents()
    assert second.ctx.layout.get("window") is None, "nothing was written into the new one"
    assert Layout(workspaces.dir_for(workspaces.DEFAULT) / "layout.json").get("window")
    second.close()


def test_switching_to_a_workspace_that_is_not_there_stays_put(app, home):
    from pycangui.ui.main_window import MainWindow
    from pycangui.ui.session import Session

    QSettings().clear()
    session = Session(MainWindow)
    session.open()
    session.reopen("never made")
    assert session.window.ctx.workspace == workspaces.DEFAULT
    session.window.close()


# --- managing them ----------------------------------------------------------------------------
def test_manage_will_not_offer_to_remove_the_ground_you_stand_on(app, home):
    from pycangui.ui.workspace_menu import ManageWorkspaces

    workspaces.create("drive")
    workspaces.create("spare")
    dialog = ManageWorkspaces(None, current="drive")

    rows = {dialog.list.item(i).data(Qt.UserRole): i for i in range(dialog.list.count())}
    for name, enabled, why in (
        (workspaces.DEFAULT, False, "always there"),
        ("drive", False, "Switch to another one first"),
        ("spare", True, ""),
    ):
        dialog.list.setCurrentRow(rows[name])
        assert dialog.delete.isEnabled() == enabled, name
        assert dialog.rename.isEnabled() == enabled, name
        assert why in dialog.rename.toolTip()
    dialog.deleteLater()


def test_manage_deletes_one_that_is_not_in_use(app, home, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from pycangui.ui.workspace_menu import ManageWorkspaces

    workspaces.create("scrap")
    dialog = ManageWorkspaces(None, current=workspaces.DEFAULT)
    rows = {dialog.list.item(i).data(Qt.UserRole): i for i in range(dialog.list.count())}
    dialog.list.setCurrentRow(rows["scrap"])
    monkeypatch.setattr(messages, "warning", lambda *a, **k: QMessageBox.Yes)
    dialog._delete()
    assert not workspaces.exists("scrap")
    dialog.deleteLater()


def test_manage_renames_and_the_list_follows(app, home, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    from pycangui.ui.workspace_menu import ManageWorkspaces

    workspaces.create("old")
    dialog = ManageWorkspaces(None, current=workspaces.DEFAULT)
    rows = {dialog.list.item(i).data(Qt.UserRole): i for i in range(dialog.list.count())}
    dialog.list.setCurrentRow(rows["old"])
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("new", True))
    dialog._rename()
    assert workspaces.exists("new") and not workspaces.exists("old")
    assert "new" in {dialog.list.item(i).data(Qt.UserRole) for i in range(dialog.list.count())}
    dialog.deleteLater()


def test_reset_everything_is_a_new_empty_workspace(window, monkeypatch):
    """Not a reset in place: what somebody is escaping from is still there
    afterwards, which is the whole reason this is the answer."""
    from PySide6.QtWidgets import QInputDialog

    window.ctx.settings.set("dbc.paths", ["mine.dbc"])
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("clean", True))
    asked: list[str] = []
    window.workspace_menu.switch_requested.connect(asked.append)

    window.workspace_menu.new_empty()

    assert asked == ["clean"]
    assert not (workspaces.dir_for("clean") / "settings.json").exists(), "nothing carried over"
    old = workspaces.dir_for(workspaces.DEFAULT) / "settings.json"
    assert json.loads(old.read_text())["dbc.paths"] == ["mine.dbc"], "and the old one is intact"


def test_a_folder_is_made_once_and_left_alone_after(tmp_path, monkeypatch):
    """Making a directory is the expensive call on a watched or redirected
    profile, so it is asked about first; the folder still has to appear."""
    from pycangui.core import paths

    wanted = tmp_path / "deep" / "folder"
    assert paths.made(wanted).is_dir(), "it makes what is not there, parents and all"

    made = []
    monkeypatch.setattr(type(wanted), "mkdir", lambda self, **k: made.append(self))
    assert paths.made(wanted) == wanted
    assert made == [], "and does not ask the file system to make it again"
