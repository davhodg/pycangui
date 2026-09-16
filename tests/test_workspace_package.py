# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""A workspace as one file: exported, imported, and what is refused on the way.

A workspace was always meant to be handed to someone else whole.  Export writes
it to a zip and import makes a new workspace from one, so the load-bearing test
is the round trip: what goes out is what comes back in.

The rest is mostly refusals, because import takes a file from somebody else and
turns it into a folder that holds code which runs -- and because it must never
take the place of a workspace somebody already has.
"""

import json
import zipfile

import pytest
from PySide6.QtWidgets import QInputDialog, QMainWindow, QMessageBox

from pycangui.core import workspace_package, workspaces
from pycangui.core.context import Context
from pycangui.core.plugin_package import PackageError
from pycangui.core.settings import Settings
from pycangui.ui import folders, messages
from pycangui.ui.workspace_menu import ManageWorkspaces, WorkspaceMenu

PRODUCT_FILES = {
    "hooks/canopen.py": "# what this maker's objects mean\n",
    "nodes/pump.py": "# a simulated pump\n",
    "eds/drive.eds": "[FileInfo]\n",
    "custom_panes/motor.json": "{}",
    "plugins/demo/plugin.py": "NAME = 'Demo'\n",
}


@pytest.fixture
def home(tmp_path, monkeypatch):
    folder = tmp_path / "home"
    monkeypatch.setenv("PYCANGUI_HOME", str(folder))
    return folder


def make_workspace(name="drive"):
    """A workspace with something of everything a product's workspace holds."""
    if not workspaces.exists(name):
        workspaces.create(name)
    folder = workspaces.dir_for(name)
    settings = Settings(folder / "settings.json")
    settings.set("dbc.paths", ["product.dbc"])
    settings.set("plugins.disabled", ["demo"])
    (folder / "layout.json").write_text('{"window": "AQID"}', encoding="utf-8")
    for inside, text in PRODUCT_FILES.items():
        (folder / inside).parent.mkdir(parents=True, exist_ok=True)
        (folder / inside).write_text(text, encoding="utf-8")
    return folder


def files_in(folder) -> dict[str, bytes]:
    return {
        item.relative_to(folder).as_posix(): item.read_bytes()
        for item in folder.rglob("*")
        if item.is_file()
    }


def zip_of(path, members: dict):
    with zipfile.ZipFile(path, "w") as archive:
        for name, text in members.items():
            archive.writestr(name, text)
    return path


def leftovers(home) -> list[str]:
    """Anything an import staged and did not tidy away."""
    return [item.name for item in home.iterdir() if item.name.startswith(".importing")]


# --- there and back -------------------------------------------------------------------------
def test_what_is_exported_is_what_is_imported(home, tmp_path):
    folder = make_workspace("drive")
    packed = workspace_package.pack("drive", tmp_path / "drive.zip")
    made = workspace_package.install(packed, "drive copy")
    assert made == workspaces.dir_for("drive copy")
    assert files_in(made) == files_in(folder)
    assert leftovers(home) == []


def test_the_workspace_folder_is_the_top_level_inside_the_file(home, tmp_path):
    """So that unzipping it by hand gives the folder rather than its contents
    scattered into wherever somebody was standing."""
    make_workspace("drive")
    with zipfile.ZipFile(workspace_package.pack("drive", tmp_path / "d.zip")) as archive:
        assert {name.split("/")[0] for name in archive.namelist()} == {"drive"}


def test_what_belongs_to_this_machine_is_left_out(home, tmp_path):
    folder = make_workspace("drive")
    local = {
        "hooks/__pycache__/canopen.cpython-312.pyc": "bytecode",
        "hooks/canopen.py.bak": "an edit somebody kept",
        "hooks/canopen.py.bak2": "and an older one",
        "plugins/.demo.installing/plugin.py": "an install that stopped part way",
        "plugins/.demo.packing.zip": "and a pack that did",
    }
    for inside, text in local.items():
        (folder / inside).parent.mkdir(parents=True, exist_ok=True)
        (folder / inside).write_text(text, encoding="utf-8")
    (folder / "hooks" / "bakery.py").write_text("# a real hook\n", encoding="utf-8")
    settings = Settings(folder / "settings.json")
    settings.set("folders.eds", str(tmp_path))
    settings.set("replay.recent", [str(tmp_path / "capture.blf")])

    with zipfile.ZipFile(workspace_package.pack("drive", tmp_path / "d.zip")) as archive:
        names = {name.removeprefix("drive/") for name in archive.namelist()}
        carried = json.loads(archive.read("drive/settings.json"))

    assert names == {"settings.json", "layout.json", "hooks/bakery.py", *PRODUCT_FILES}
    assert "folders.eds" not in carried and "replay.recent" not in carried, "paths on this disc"
    assert carried["dbc.paths"] == ["product.dbc"], "and everything about the product stays"


def test_a_file_saved_inside_the_workspace_does_not_contain_itself(home):
    folder = make_workspace("drive")
    with zipfile.ZipFile(workspace_package.pack("drive", folder / "drive.zip")) as archive:
        assert "drive/drive.zip" not in archive.namelist()


def test_a_workspace_nothing_has_been_changed_in_still_exports(home, tmp_path):
    """It has no settings file yet, and the settings file is how an import
    recognises a workspace."""
    workspaces.create("fresh")
    made = workspace_package.install(
        workspace_package.pack("fresh", tmp_path / "fresh.zip"), "fresh copy"
    )
    assert (made / "settings.json").read_text(encoding="utf-8") == "{}"


def test_exporting_a_workspace_that_is_not_there(home, tmp_path):
    with pytest.raises(PackageError, match="no workspace called"):
        workspace_package.pack("never made", tmp_path / "x.zip")
    assert not (tmp_path / "x.zip").exists()


# --- the shapes people actually make ------------------------------------------------------------
def test_a_zip_of_the_folder_contents_imports_too(home, tmp_path):
    """Somebody who selected the files rather than the folder, and zipped those."""
    flat = zip_of(tmp_path / "pump.zip", {"settings.json": "{}", "hooks/canopen.py": "# x\n"})
    package = workspace_package.inspect(flat)
    assert (package.name, package.root) == ("pump", "")
    made = workspace_package.install(flat, package.name)
    assert (made / "hooks" / "canopen.py").read_text(encoding="utf-8") == "# x\n"


def test_what_sits_beside_the_top_folder_is_not_part_of_it(home, tmp_path):
    """The __MACOSX folder one archiver adds, for one."""
    package = workspace_package.inspect(
        zip_of(
            tmp_path / "drive.zip",
            {"drive/settings.json": "{}", "__MACOSX/drive/._settings.json": "x"},
        )
    )
    assert package.files == ("settings.json",)


def test_a_zip_made_by_hand_arrives_as_an_exported_one_would(home, tmp_path):
    by_hand = zip_of(
        tmp_path / "drive.zip",
        {
            "drive/settings.json": json.dumps({"folders.log": "C:/logs", "dbc.strict": True}),
            "drive/hooks/canopen.py": "# mine\n",
            "drive/hooks/canopen.py.bak": "# old\n",
            "drive/hooks/__pycache__/canopen.cpython-312.pyc": "x",
        },
    )
    made = workspace_package.install(by_hand, "drive")
    assert files_in(made).keys() == {"settings.json", "hooks/canopen.py"}
    assert json.loads((made / "settings.json").read_text(encoding="utf-8")) == {"dbc.strict": True}


# --- and what it will not take -------------------------------------------------------------------
@pytest.mark.parametrize(
    "escape", ["../escape.py", "drive/../../escape.py", "/etc/escape.py", "C:/escape.py"]
)
def test_a_zip_that_writes_outside_the_workspace_is_refused(home, tmp_path, escape):
    """Refused whole rather than repaired, and before anything is written."""
    bad = zip_of(tmp_path / "evil.zip", {"drive/settings.json": "{}", escape: "boom"})
    with pytest.raises(PackageError, match="outside"):
        workspace_package.inspect(bad)
    with pytest.raises(PackageError, match="outside"):
        workspace_package.install(bad, "evil")
    assert not workspaces.exists("evil")
    assert not (home / "escape.py").exists() and not (tmp_path / "escape.py").exists()
    assert leftovers(home) == []


def test_a_plugin_package_is_not_a_workspace(home, tmp_path):
    plugin = zip_of(tmp_path / "demo.zip", {"demo/plugin.py": "NAME = 'Demo'\n"})
    with pytest.raises(PackageError, match="not a pycangui workspace"):
        workspace_package.inspect(plugin)


def test_a_settings_file_buried_deeper_than_one_folder_is_not_hunted_for(home, tmp_path):
    deep = zip_of(tmp_path / "d.zip", {"backup/drive/settings.json": "{}"})
    with pytest.raises(PackageError, match="not a pycangui workspace"):
        workspace_package.inspect(deep)


def test_something_that_is_not_a_zip_at_all(home, tmp_path):
    (tmp_path / "notes.zip").write_text("hello", encoding="utf-8")
    with pytest.raises(PackageError, match="not a zip"):
        workspace_package.inspect(tmp_path / "notes.zip")


def test_a_file_holding_two_workspaces_is_refused(home, tmp_path):
    two = zip_of(tmp_path / "both.zip", {"one/settings.json": "{}", "two/settings.json": "{}"})
    with pytest.raises(PackageError, match="more than one workspace"):
        workspace_package.inspect(two)


def test_a_file_with_too_many_entries_is_refused(home, tmp_path, monkeypatch):
    monkeypatch.setattr(workspace_package, "MAX_ENTRIES", 2)
    many = zip_of(
        tmp_path / "many.zip", {"drive/settings.json": "{}", "drive/a": "", "drive/b": ""}
    )
    with pytest.raises(PackageError, match="3 files"):
        workspace_package.inspect(many)


def test_a_file_far_too_big_to_be_a_workspace_is_refused(home, tmp_path, monkeypatch):
    monkeypatch.setattr(workspace_package, "MAX_BYTES", 8)
    big = zip_of(tmp_path / "big.zip", {"drive/settings.json": "{}", "drive/eds/x.eds": "x" * 64})
    with pytest.raises(PackageError, match="more than a workspace should be"):
        workspace_package.inspect(big)


def test_an_existing_workspace_is_never_imported_over(home, tmp_path):
    folder = make_workspace("drive")
    packed = workspace_package.pack("drive", tmp_path / "drive.zip")
    (folder / "hooks" / "canopen.py").write_text("# changed since\n", encoding="utf-8")
    with pytest.raises(PackageError, match="already a workspace"):
        workspace_package.install(packed, "drive")
    assert (folder / "hooks" / "canopen.py").read_text(encoding="utf-8") == "# changed since\n"


def test_an_import_that_fails_part_way_leaves_nothing_behind(home, tmp_path, monkeypatch):
    make_workspace("drive")
    packed = workspace_package.pack("drive", tmp_path / "drive.zip")
    real = workspace_package._copy
    calls = []

    def fail_on_the_third(*args):
        calls.append(1)
        if len(calls) == 3:
            raise OSError("the disc is full")
        return real(*args)

    monkeypatch.setattr(workspace_package, "_copy", fail_on_the_third)
    with pytest.raises(OSError):
        workspace_package.install(packed, "half")
    assert not workspaces.exists("half"), "no half-made workspace in the Switch to menu"
    assert leftovers(home) == []


# --- naming what comes in -------------------------------------------------------------------------
def test_the_next_free_name_is_offered_for_one_that_is_taken(home):
    workspaces.create("drive")
    assert workspaces.next_free("drive") == "drive 2"
    workspaces.create("drive 2")
    assert workspaces.next_free("drive") == "drive 3"
    assert workspaces.next_free("pump") == "pump", "and a free one as it is"


def test_a_name_that_could_never_be_a_folder_is_not_tidied_into_one(home):
    """A downloaded "drive (1)" offers plain workspace, rather than a guess."""
    assert workspaces.next_free("drive (1)") == "workspace"


def test_the_next_free_name_still_fits(home):
    longest = "x" * workspaces.MAX_NAME
    workspaces.create(longest)
    offered = workspaces.next_free(longest)
    assert workspaces.why_not(offered) == "" and len(offered) <= workspaces.MAX_NAME


# --- the menu ---------------------------------------------------------------------------------
@pytest.fixture
def menu(app, home):
    window = QMainWindow()
    workspace_menu = WorkspaceMenu(window, Context(log=lambda _message: None))
    yield workspace_menu
    window.deleteLater()


@pytest.fixture
def answers(monkeypatch):
    """Scripted answers to messages.question, keeping what each one asked."""
    script = {"answers": [], "asked": []}

    def question(_parent, _title, text, *_args, **_kwargs):
        script["asked"].append(text)
        if not script["answers"]:
            pytest.fail(f"a question nobody expected: {text}")
        return script["answers"].pop(0)

    monkeypatch.setattr(messages, "question", question)
    return script


@pytest.fixture
def warnings_shown(monkeypatch):
    said: list[str] = []
    monkeypatch.setattr(messages, "warning", lambda _p, _t, text, *a, **k: said.append(text))
    return said


def choose_file(monkeypatch, path):
    monkeypatch.setattr(folders, "open_file", lambda *_args: str(path))


def test_the_menu_offers_export_and_import_with_a_word_on_each(menu):
    actions = {action.text(): action for action in menu.menu.actions()}
    assert "Export..." in actions and "Import..." in actions


def test_export_writes_the_workspace_in_use(menu, tmp_path, monkeypatch):
    menu.ctx.settings.set("dbc.paths", ["mine.dbc"])
    target = tmp_path / "out" / "default.zip"
    offered: list[str] = []
    monkeypatch.setattr(
        folders, "save_file", lambda *args: (offered.append(args[-1]), str(target))[1]
    )
    arranged: list[int] = []
    menu.arrangement_wanted.connect(lambda: arranged.append(1))

    menu.export()

    assert offered == ["default.zip"], "named after the workspace"
    assert arranged == [1], "the panes as they are on screen, not as they were at the start"
    with zipfile.ZipFile(target) as archive:
        assert json.loads(archive.read("default/settings.json"))["dbc.paths"] == ["mine.dbc"]


def test_cancelling_the_export_dialog_writes_nothing(menu, monkeypatch, tmp_path):
    monkeypatch.setattr(folders, "save_file", lambda *args: "")
    arranged: list[int] = []
    menu.arrangement_wanted.connect(lambda: arranged.append(1))
    menu.export()
    assert arranged == []
    assert not list(tmp_path.rglob("*.zip"))


def test_manage_exports_the_one_selected(menu, tmp_path, monkeypatch):
    from PySide6.QtCore import Qt

    make_workspace("spare")
    dialog = ManageWorkspaces(None, current=workspaces.DEFAULT, export=menu.export)
    rows = {dialog.list.item(i).data(Qt.UserRole): i for i in range(dialog.list.count())}
    dialog.list.setCurrentRow(rows["spare"])
    assert dialog.export.isEnabled()
    monkeypatch.setattr(folders, "save_file", lambda *args: str(tmp_path / "spare.zip"))

    dialog._export_selected()

    with zipfile.ZipFile(tmp_path / "spare.zip") as archive:
        assert "spare/hooks/canopen.py" in archive.namelist()
    dialog.deleteLater()


def test_import_lists_what_it_will_write_then_makes_the_workspace(
    menu, home, tmp_path, monkeypatch, answers
):
    source = make_workspace("drive")
    choose_file(monkeypatch, workspace_package.pack("drive", tmp_path / "pump.zip"))
    answers["answers"] = [QMessageBox.Yes, QMessageBox.No]
    switched: list[str] = []
    menu.switch_requested.connect(switched.append)

    menu.import_file()

    assert files_in(workspaces.dir_for("pump")) == files_in(source), "named after the file"
    confirmation = answers["asked"][0]
    assert "pump" in confirmation and "pump.zip" in confirmation
    for inside in PRODUCT_FILES:
        assert inside in confirmation, f"{inside} is not listed"
    assert switched == [], "not opened, because they said not to"


def test_import_offers_to_open_what_it_made(menu, tmp_path, monkeypatch, answers):
    make_workspace("drive")
    choose_file(monkeypatch, workspace_package.pack("drive", tmp_path / "pump.zip"))
    answers["answers"] = [QMessageBox.Yes, QMessageBox.Yes]
    switched: list[str] = []
    menu.switch_requested.connect(switched.append)

    menu.import_file()

    # Through the same signal as Switch to, and so through the window's own
    # question when a bus is connected.
    assert switched == ["pump"]


def test_cancelling_the_confirmation_writes_nothing(menu, home, tmp_path, monkeypatch, answers):
    make_workspace("drive")
    choose_file(monkeypatch, workspace_package.pack("drive", tmp_path / "pump.zip"))
    before = workspaces.names()
    answers["answers"] = [QMessageBox.Cancel]

    menu.import_file()

    assert workspaces.names() == before
    assert not workspaces.dir_for("pump").exists()
    assert leftovers(home) == []


def test_a_name_that_is_taken_asks_for_another_and_offers_the_next_free_one(
    menu, tmp_path, monkeypatch, answers
):
    source = make_workspace("drive")
    choose_file(monkeypatch, workspace_package.pack("drive", tmp_path / "drive.zip"))
    (source / "hooks" / "canopen.py").write_text("# changed since\n", encoding="utf-8")
    asked: dict = {}

    def get_text(_parent, _title, label, text=""):
        asked.update(label=label, text=text)
        return text, True  # accepting what was offered

    monkeypatch.setattr(QInputDialog, "getText", get_text)
    answers["answers"] = [QMessageBox.Yes, QMessageBox.No]

    menu.import_file()

    assert "already a workspace called drive" in asked["label"]
    assert asked["text"] == "drive 2"
    assert workspaces.exists("drive 2")
    assert (source / "hooks" / "canopen.py").read_text(encoding="utf-8") == "# changed since\n"


def test_typing_another_taken_name_is_refused_and_writes_nothing(
    menu, tmp_path, monkeypatch, answers, warnings_shown
):
    make_workspace("drive")
    choose_file(monkeypatch, workspace_package.pack("drive", tmp_path / "drive.zip"))
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("default", True))
    before = workspaces.names()

    menu.import_file()

    assert answers["asked"] == [], "not asked to agree to something that will not happen"
    assert warnings_shown and "already a workspace" in warnings_shown[0]
    assert workspaces.names() == before


def test_cancelling_the_name_writes_nothing(menu, tmp_path, monkeypatch, answers):
    make_workspace("drive")
    choose_file(monkeypatch, workspace_package.pack("drive", tmp_path / "drive.zip"))
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("drive 2", False))
    before = workspaces.names()
    menu.import_file()
    assert workspaces.names() == before


def test_a_refused_file_is_reported_plainly_and_nothing_is_asked(
    menu, home, tmp_path, monkeypatch, answers, warnings_shown
):
    bad = zip_of(tmp_path / "evil.zip", {"evil/settings.json": "{}", "../escape.py": "boom"})
    choose_file(monkeypatch, bad)
    before = workspaces.names()

    menu.import_file()

    assert warnings_shown and "outside" in warnings_shown[0]
    assert answers["asked"] == []
    assert workspaces.names() == before


def test_cancelling_the_file_dialog_does_nothing(menu, monkeypatch, answers):
    monkeypatch.setattr(folders, "open_file", lambda *_args: "")
    before = workspaces.names()
    menu.import_file()
    assert workspaces.names() == before
