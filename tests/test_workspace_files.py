# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Files a workspace points at: kept where people keep them, and offered a way in.

A path to somebody's download folder means nothing on the computer an exported
workspace is imported on. pycangui does not move anybody's files to fix that:
a file in the workspace is remembered relative to it, a file elsewhere is
offered a copy in -- when it is chosen, and again on export -- and otherwise
left exactly where it is.
"""

import zipfile
from pathlib import Path
from shutil import copyfile

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QMainWindow, QMessageBox

from pycangui import resources
from pycangui.canopen import NodeIdentity
from pycangui.core import workspace_files, workspaces
from pycangui.core.context import Context
from pycangui.core.settings import Settings
from pycangui.ui import folders, keep_file, messages
from pycangui.ui.main_window import MainWindow
from pycangui.ui.workspace_menu import WorkspaceMenu

IDENTITY = NodeIdentity(5, vendor_id=1, product_code=2, revision=3)


@pytest.fixture
def home(tmp_path, monkeypatch):
    folder = tmp_path / "home"
    monkeypatch.setenv("PYCANGUI_HOME", str(folder))
    QSettings().clear()
    return folder


@pytest.fixture
def elsewhere(tmp_path):
    folder = tmp_path / "my files"
    folder.mkdir()
    return folder


def a_file(folder, name, text="contents"):
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def answering(monkeypatch, *buttons):
    """Answer each messages.question in turn, and keep what was asked."""
    queue = list(buttons)
    asked = []

    def question(_parent, title, text, *_args, **_kwargs):
        asked.append((title, text))
        return queue.pop(0)

    monkeypatch.setattr(messages, "question", question)
    return asked


def never_asked(monkeypatch):
    def question(*_args, **_kwargs):
        raise AssertionError("nothing should have been asked")

    monkeypatch.setattr(messages, "question", question)


# --- where a file is remembered --------------------------------------------------------------
def test_a_file_in_the_workspace_is_remembered_relative_to_it(home):
    workspace = workspaces.active_dir()
    path = a_file(workspace, "dbc/product.dbc")
    assert workspace_files.stored(path, workspace) == "dbc/product.dbc"
    assert workspace_files.resolve("dbc/product.dbc", workspace) == path


def test_a_file_elsewhere_is_remembered_where_it_was_chosen(home, elsewhere):
    workspace = workspaces.active_dir()
    path = a_file(elsewhere, "product.dbc")
    assert workspace_files.stored(path, workspace) == str(path)
    assert workspace_files.resolve(str(path), workspace) == path


# --- naming one in a message ------------------------------------------------------------------
def test_a_file_in_the_workspace_is_named_by_its_place_in_it(home):
    """The rest of its path is the same for every file there and says nothing."""
    workspace = workspaces.active_dir()
    path = a_file(workspace, "dbc/product.dbc")
    assert workspace_files.shown(path, workspace) == "dbc/product.dbc in the workspace"
    assert workspace_files.shown("dbc/product.dbc", workspace) == "dbc/product.dbc in the workspace"


def test_a_file_elsewhere_is_named_in_full(home, elsewhere):
    """Where somebody keeps a file is what tells them it is the one they meant."""
    workspace = workspaces.active_dir()
    path = a_file(elsewhere, "product.dbc")
    assert workspace_files.shown(path, workspace) == str(path)


# --- copying one in ------------------------------------------------------------------------------
def test_a_copy_goes_in_the_folder_for_its_kind(home, elsewhere):
    workspace = workspaces.active_dir()
    path = a_file(elsewhere, "drive.eds", "[FileInfo]")
    copy = workspace_files.copy_in(path, workspace_files.EDS, workspace)
    assert copy == workspace / "eds" / "drive.eds"
    assert copy.read_text(encoding="utf-8") == "[FileInfo]"
    assert path.exists(), "the original stays where it was"


def test_the_same_file_copied_twice_is_one_copy(home, elsewhere):
    workspace = workspaces.active_dir()
    path = a_file(elsewhere, "drive.eds")
    first = workspace_files.copy_in(path, workspace_files.EDS, workspace)
    assert workspace_files.copy_in(path, workspace_files.EDS, workspace) == first


def test_a_different_file_with_the_same_name_is_kept_beside_the_first(home, tmp_path):
    """Two products' drive.eds are not the same file because they share a name."""
    workspace = workspaces.active_dir()
    one = a_file(tmp_path / "one", "drive.eds", "one")
    two = a_file(tmp_path / "two", "drive.eds", "two")
    workspace_files.copy_in(one, workspace_files.EDS, workspace)
    second = workspace_files.copy_in(two, workspace_files.EDS, workspace)
    assert second.name == "drive (2).eds"
    assert (workspace / "eds" / "drive.eds").read_text(encoding="utf-8") == "one"


# --- what the settings point at ------------------------------------------------------------------
def settings_file(tmp_path, **values):
    settings = Settings(tmp_path / "settings.json")
    for key, value in values.items():
        settings.set(key, value)
    return settings


def test_the_files_are_the_eds_map_the_databases_and_the_a2l(home, tmp_path):
    settings = settings_file(tmp_path)
    settings.set("canopen.eds_map", {IDENTITY.key: "C:/x/drive.eds", "bad": 5})
    settings.set("dbc.paths", ["a.dbc", 7])
    settings.set("xcp.a2l", "ecu.a2l")
    found = [(r.kind, r.value) for r in workspace_files.references(settings)]
    assert found == [("eds", "C:/x/drive.eds"), ("dbc", "a.dbc"), ("a2l", "ecu.a2l")]


def test_a_full_path_into_the_workspace_is_tidied_without_asking(home, elsewhere, tmp_path):
    """Nothing is lost, and a full path is exactly what breaks on the next computer."""
    workspace = workspaces.active_dir()
    inside = a_file(workspace, "dbc/product.dbc")
    kept = a_file(elsewhere, "other.dbc")
    settings = settings_file(tmp_path)
    settings.set("dbc.paths", [str(inside), str(kept)])
    assert workspace_files.tidy(settings, workspace) == 1
    assert settings.get("dbc.paths") == ["dbc/product.dbc", str(kept)]


def test_bringing_files_in_copies_them_and_points_the_settings_at_the_copies(
    home, elsewhere, tmp_path
):
    workspace = workspaces.active_dir()
    eds = a_file(elsewhere, "drive.eds")
    dbc = a_file(elsewhere, "product.dbc")
    gone = elsewhere / "not there.a2l"
    settings = settings_file(tmp_path)
    settings.set("canopen.eds_map", {IDENTITY.key: str(eds)})
    settings.set("dbc.paths", [str(dbc), "dbc/already.dbc"])
    settings.set("xcp.a2l", str(gone))

    copied, missing = workspace_files.bring_in(settings, workspace)
    assert sorted(p.name for p in copied) == ["drive.eds", "product.dbc"]
    assert missing == [gone]
    assert settings.get("canopen.eds_map") == {IDENTITY.key: "eds/drive.eds"}
    assert settings.get("dbc.paths") == ["dbc/product.dbc", "dbc/already.dbc"]
    assert settings.get("xcp.a2l") == str(gone), "a broken link is left where it pointed"


# --- asked when a file is chosen -----------------------------------------------------------------
def test_a_file_already_in_the_workspace_is_not_asked_about(app, home, monkeypatch):
    ctx = Context(log=print)
    path = a_file(ctx.workspace_dir, "dbc/product.dbc")
    never_asked(monkeypatch)
    assert keep_file.offer(None, ctx, path, workspace_files.DBC) == "dbc/product.dbc"


def test_yes_copies_the_file_in(app, home, elsewhere, monkeypatch):
    ctx = Context(log=print)
    path = a_file(elsewhere, "product.dbc")
    asked = answering(monkeypatch, QMessageBox.Yes)
    assert keep_file.offer(None, ctx, path, workspace_files.DBC) == "dbc/product.dbc"
    assert (ctx.workspace_dir / "dbc" / "product.dbc").exists()
    assert "product.dbc" in asked[0][0]


def test_no_keeps_using_the_file_where_it_is(app, home, elsewhere, monkeypatch):
    ctx = Context(log=print)
    path = a_file(elsewhere, "product.dbc")
    answering(monkeypatch, QMessageBox.No)
    assert keep_file.offer(None, ctx, path, workspace_files.DBC) == str(path)
    assert not (ctx.workspace_dir / "dbc").exists()


@pytest.fixture
def window(app, home, monkeypatch):
    win = MainWindow()
    monkeypatch.setattr(win.canopen, "identify", lambda _node_id: None)
    yield win
    win.close()


def test_loading_a_database_offers_to_copy_it_in(window, elsewhere, monkeypatch):
    path = elsewhere / "demo.dbc"
    copyfile(resources.path("demo.dbc"), path)
    monkeypatch.setattr(folders, "open_file", lambda *_a, **_k: str(path))
    answering(monkeypatch, QMessageBox.Yes)
    window._load_dbc_dialog()
    assert window.ctx.settings.get("dbc.paths") == ["dbc/demo.dbc"]


@pytest.mark.parametrize("inside", [True, False])
def test_a_loaded_database_says_it_is_a_dbc_and_which_file(window, elsewhere, monkeypatch, inside):
    """Reported: the DBC line gave a full path and no file type, while the
    A2L one gave the type and no path. Both now say both."""
    folder = window.ctx.workspace_dir / "dbc" if inside else elsewhere
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "demo.dbc"
    copyfile(resources.path("demo.dbc"), path)
    monkeypatch.setattr(folders, "open_file", lambda *_a, **_k: str(path))
    answering(monkeypatch, QMessageBox.No)  # asked only when it is elsewhere
    said = []
    monkeypatch.setattr(window.events, "information", said.append)
    window._load_dbc_dialog()
    line = next(text for text in said if text.startswith("DBC loaded: "))
    where = "dbc/demo.dbc in the workspace" if inside else str(path)
    assert line.startswith(f"DBC loaded: {where} (")


def test_a_loaded_a2l_says_which_file(window, elsewhere):
    path = elsewhere / "demo.a2l"
    copyfile(resources.path("demo.a2l"), path)
    said = []
    window.xcp.result.connect(said.append)
    window.xcp.load_a2l(str(path))
    assert any(text.startswith(f"A2L loaded: {path} (") for text in said)


# --- a copy, once made, is the file in use ----------------------------------------------------
def test_a_database_copied_in_is_the_one_loaded(window, elsewhere, monkeypatch):
    """Reported: yes to the copy still loaded the original, so the two
    disagreed until the next start."""
    path = elsewhere / "demo.dbc"
    copyfile(resources.path("demo.dbc"), path)
    monkeypatch.setattr(folders, "open_file", lambda *_a, **_k: str(path))
    answering(monkeypatch, QMessageBox.Yes)
    said = []
    monkeypatch.setattr(window.events, "information", said.append)
    window._load_dbc_dialog()
    copy = window.ctx.workspace_dir / "dbc" / "demo.dbc"
    assert [Path(p).resolve() for p in window.dbc.databases] == [copy.resolve()]
    assert any(t.startswith("DBC loaded: dbc/demo.dbc in the workspace (") for t in said)


def test_a_database_copied_in_can_be_removed_in_the_same_session(window, elsewhere, monkeypatch):
    """What the disagreement broke: Remove DBC unloads by the remembered
    path, which was the copy, while the database was held under the original."""
    path = elsewhere / "demo.dbc"
    copyfile(resources.path("demo.dbc"), path)
    monkeypatch.setattr(folders, "open_file", lambda *_a, **_k: str(path))
    answering(monkeypatch, QMessageBox.Yes)
    window._load_dbc_dialog()
    remembered = window.ctx.settings.get("dbc.paths")[0]
    resolved = str(workspace_files.resolve(remembered, window.ctx.workspace_dir))
    window._remove_dbc(remembered, resolved)
    assert not window.dbc.databases


def test_a_database_that_will_not_load_leaves_no_copy_behind(window, elsewhere, monkeypatch):
    path = a_file(elsewhere, "broken.dbc", "this is not a CAN database\n")
    monkeypatch.setattr(folders, "open_file", lambda *_a, **_k: str(path))
    # Yes to the copy, then yes to loading it without the strict checks.
    answering(monkeypatch, QMessageBox.Yes, QMessageBox.Yes)
    window._load_dbc_dialog()
    assert not (window.ctx.workspace_dir / "dbc" / "broken.dbc").exists()
    assert window.ctx.settings.get("dbc.paths", []) == []


def test_an_a2l_copied_in_is_the_one_loaded(window, elsewhere, monkeypatch):
    path = elsewhere / "demo.a2l"
    copyfile(resources.path("demo.a2l"), path)
    monkeypatch.setattr(folders, "open_file", lambda *_a, **_k: str(path))
    answering(monkeypatch, QMessageBox.Yes)
    said = []
    window.xcp.result.connect(said.append)
    window.xcp_view._load_a2l()
    assert any(t.startswith("A2L loaded: a2l/demo.a2l in the workspace (") for t in said)
    assert window.ctx.settings.get("xcp.a2l") == "a2l/demo.a2l"


def test_a_database_remembered_in_the_workspace_loads_at_startup(app, home):
    workspace = workspaces.active_dir()
    (workspace / "dbc").mkdir(parents=True, exist_ok=True)
    copyfile(resources.path("demo.dbc"), workspace / "dbc" / "demo.dbc")
    Context(log=print).settings.set("dbc.paths", ["dbc/demo.dbc"])
    win = MainWindow()
    try:
        assert win.dbc.databases, "found relative to the workspace"
    finally:
        win.close()


def test_remembering_a_chosen_eds_offers_to_copy_it_in(window, elsewhere, monkeypatch):
    eds = a_file(elsewhere, "drive.eds")
    monkeypatch.setattr(folders, "open_file", lambda *_a, **_k: str(eds))
    answering(monkeypatch, QMessageBox.Yes, QMessageBox.Yes)  # remember it, and copy it in
    used = window.canopen_view._ask_for_eds(IDENTITY)
    assert used == str(window.ctx.workspace_dir / "eds" / "drive.eds"), "the copy is used"
    assert window.ctx.settings.get("canopen.eds_map") == {IDENTITY.key: "eds/drive.eds"}


def test_a_remembered_eds_that_is_not_there_falls_back_to_the_search(window, elsewhere):
    """Rather than stopping at a link made on another computer."""
    window.ctx.settings.set("canopen.eds_map", {IDENTITY.key: str(elsewhere / "gone.eds")})
    assert window.canopen_view._remembered_eds(IDENTITY) is None
    assert "not there" in window.log.toPlainText()

    a_file(window.ctx.workspace_dir, "eds/drive.eds")
    window.ctx.settings.set("canopen.eds_map", {IDENTITY.key: "eds/drive.eds"})
    assert (
        window.canopen_view._remembered_eds(IDENTITY)
        == window.ctx.workspace_dir / "eds" / "drive.eds"
    )


# --- asked again on export -----------------------------------------------------------------------
@pytest.fixture
def menu(app, home):
    owner = QMainWindow()
    yield WorkspaceMenu(owner, Context(log=lambda _message: None))
    owner.deleteLater()


def exported_to(tmp_path, monkeypatch):
    target = tmp_path / "out.zip"
    monkeypatch.setattr(folders, "save_file", lambda *_a, **_k: str(target))
    return target


def test_export_offers_to_bring_in_files_kept_outside(menu, elsewhere, tmp_path, monkeypatch):
    dbc = a_file(elsewhere, "product.dbc")
    menu.ctx.settings.set("dbc.paths", [str(dbc)])
    asked = answering(monkeypatch, QMessageBox.Yes)
    target = exported_to(tmp_path, monkeypatch)

    menu.export()
    assert str(dbc) in asked[0][1], "it says which files"
    assert menu.ctx.settings.get("dbc.paths") == ["dbc/product.dbc"]
    assert "default/dbc/product.dbc" in zipfile.ZipFile(target).namelist()


def test_no_exports_with_the_links_as_they_are(menu, elsewhere, tmp_path, monkeypatch):
    dbc = a_file(elsewhere, "product.dbc")
    menu.ctx.settings.set("dbc.paths", [str(dbc)])
    answering(monkeypatch, QMessageBox.No)
    target = exported_to(tmp_path, monkeypatch)

    menu.export()
    assert menu.ctx.settings.get("dbc.paths") == [str(dbc)]
    assert not any("product.dbc" in name for name in zipfile.ZipFile(target).namelist())


def test_cancel_exports_nothing(menu, elsewhere, tmp_path, monkeypatch):
    menu.ctx.settings.set("dbc.paths", [str(a_file(elsewhere, "product.dbc"))])
    answering(monkeypatch, QMessageBox.Cancel)
    target = exported_to(tmp_path, monkeypatch)
    menu.export()
    assert not target.exists()


def test_a_workspace_with_nothing_outside_it_is_exported_without_a_question(
    menu, tmp_path, monkeypatch
):
    a_file(menu.ctx.workspace_dir, "dbc/product.dbc")
    menu.ctx.settings.set("dbc.paths", [str(menu.ctx.workspace_dir / "dbc" / "product.dbc")])
    never_asked(monkeypatch)
    target = exported_to(tmp_path, monkeypatch)
    menu.export()
    assert target.exists()
    assert menu.ctx.settings.get("dbc.paths") == ["dbc/product.dbc"], "and tidied on the way"


def test_a_file_that_has_gone_is_left_pointing_where_it_was(menu, elsewhere, tmp_path, monkeypatch):
    gone = str(elsewhere / "gone.dbc")
    menu.ctx.settings.set("dbc.paths", [gone])
    answering(monkeypatch, QMessageBox.Yes)
    target = exported_to(tmp_path, monkeypatch)
    menu.export()
    assert target.exists()
    assert menu.ctx.settings.get("dbc.paths") == [gone]


def test_another_workspace_s_files_are_offered_too(menu, elsewhere, tmp_path, monkeypatch):
    """Exported from Manage, where the settings are that workspace's own file."""
    workspaces.create("drive")
    folder = workspaces.dir_for("drive")
    dbc = a_file(elsewhere, "product.dbc")
    Settings(folder / "settings.json").set("dbc.paths", [str(dbc)])
    answering(monkeypatch, QMessageBox.Yes)
    exported_to(tmp_path, monkeypatch)

    menu.export("drive")
    assert Settings(folder / "settings.json").get("dbc.paths") == ["dbc/product.dbc"]
    assert (folder / "dbc" / "product.dbc").exists()
