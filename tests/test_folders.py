# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Where a file dialog opens, and how to get back to where it started.

Windows remembers a last-used folder per application, which is no help here:
an EDS, a firmware image and a captured log live in three different places,
and one shared memory means every dialog opens where the last unrelated one
left off. So the folder is remembered per sort of file.
"""

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QFileDialog

from pycangui.core import workspaces
from pycangui.core.context import Context
from pycangui.ui import folders, messages


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def ctx(home):
    return Context(log=print)


@pytest.fixture
def picked(monkeypatch):
    """Answer every dialog with a chosen file, and record where it opened."""
    opened: list[str] = []
    answer: list[str] = [""]

    def fake(_parent, _caption, directory, _filter=""):
        opened.append(directory)
        return (answer[0], "")

    monkeypatch.setattr(QFileDialog, "getOpenFileName", fake)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", fake)
    return opened, answer


# --- one folder per sort of file -----------------------------------------------------
def test_the_first_time_it_opens_where_it_is_told(app, ctx, picked, tmp_path):
    opened, answer = picked
    answer[0] = ""
    folders.open_file(None, ctx, folders.EDS, "EDS", "*.eds", ctx.eds_dir)
    assert opened == [str(ctx.eds_dir)]


def test_the_next_time_it_opens_where_the_last_one_was_found(app, ctx, picked, tmp_path):
    opened, answer = picked
    elsewhere = tmp_path / "customer drop"
    elsewhere.mkdir()
    answer[0] = str(elsewhere / "drive.eds")

    folders.open_file(None, ctx, folders.EDS, "EDS", "*.eds", ctx.eds_dir)
    folders.open_file(None, ctx, folders.EDS, "EDS", "*.eds", ctx.eds_dir)
    assert opened[1] == str(elsewhere)


def test_one_sort_of_file_does_not_move_another(app, ctx, picked, tmp_path):
    """The whole point: an EDS and a firmware image live in different places."""
    opened, answer = picked
    eds_folder = tmp_path / "eds here"
    eds_folder.mkdir()
    answer[0] = str(eds_folder / "drive.eds")
    folders.open_file(None, ctx, folders.EDS, "EDS", "*.eds", ctx.eds_dir)

    answer[0] = ""
    folders.open_file(None, ctx, folders.IMAGE, "Image", "*.hex", ctx.user_dir)
    assert opened[-1] == str(ctx.user_dir), "the image dialog was not moved by the EDS"


def test_a_file_that_was_not_chosen_moves_nothing(app, ctx, picked):
    """Cancelling is not a choice about anything."""
    _opened, answer = picked
    answer[0] = ""
    folders.open_file(None, ctx, folders.EDS, "EDS", "*.eds", ctx.eds_dir)
    assert folders.remembered(ctx, folders.EDS) is None


def test_saving_remembers_too_and_shares_with_opening(app, ctx, picked, tmp_path):
    """A DCF saved next to its EDS should be found next to it as well."""
    opened, answer = picked
    where = tmp_path / "configs"
    where.mkdir()
    answer[0] = str(where / "node5.dcf")
    folders.save_file(None, ctx, folders.EDS, "Save", "*.dcf", ctx.eds_dir, suggested="node5.dcf")

    answer[0] = ""
    folders.open_file(None, ctx, folders.EDS, "EDS", "*.eds", ctx.eds_dir)
    assert opened[-1] == str(where)


def test_a_save_dialog_still_suggests_a_name(app, ctx, picked, tmp_path):
    opened, answer = picked
    answer[0] = ""
    folders.save_file(None, ctx, folders.LOG, "Record", "*.blf", tmp_path, suggested="capture.blf")
    assert opened[0].endswith("capture.blf")


# --- and back to the default ------------------------------------------------------------
def test_a_folder_that_is_no_longer_there_is_ignored(app, ctx, picked, tmp_path):
    """A memory stick that has been unplugged, or a folder somebody deleted."""
    opened, answer = picked
    gone = tmp_path / "gone"
    gone.mkdir()
    answer[0] = str(gone / "drive.eds")
    folders.open_file(None, ctx, folders.EDS, "EDS", "*.eds", ctx.eds_dir)
    gone.rmdir()

    answer[0] = ""
    folders.open_file(None, ctx, folders.EDS, "EDS", "*.eds", ctx.eds_dir)
    assert opened[-1] == str(ctx.eds_dir), "back to the default rather than nowhere"


def test_forgetting_puts_every_dialog_back(app, ctx, picked, tmp_path):
    opened, answer = picked
    for kind in (folders.EDS, folders.IMAGE):
        where = tmp_path / kind
        where.mkdir()
        answer[0] = str(where / "a.file")
        folders.open_file(None, ctx, kind, kind, "*", ctx.user_dir)

    assert folders.forget_all(ctx) == 2
    answer[0] = ""
    folders.open_file(None, ctx, folders.EDS, "EDS", "*.eds", ctx.eds_dir)
    assert opened[-1] == str(ctx.eds_dir)


def test_forgetting_leaves_the_rest_of_the_settings_alone(app, ctx, picked, tmp_path):
    _opened, answer = picked
    ctx.settings.set("dbc.paths", ["mine.dbc"])
    answer[0] = str(tmp_path / "a.eds")
    folders.open_file(None, ctx, folders.EDS, "EDS", "*.eds", ctx.eds_dir)

    folders.forget_all(ctx)
    assert ctx.settings.get("dbc.paths") == ["mine.dbc"]


def test_forgetting_nothing_is_not_an_error(app, ctx):
    assert folders.forget_all(ctx) == 0


# --- it belongs to the workspace -----------------------------------------------------------
def test_another_product_has_its_files_somewhere_else(app, home, picked, tmp_path):
    opened, answer = picked
    first = Context(log=print)
    where = tmp_path / "product one"
    where.mkdir()
    answer[0] = str(where / "drive.eds")
    folders.open_file(None, first, folders.EDS, "EDS", "*.eds", first.eds_dir)

    workspaces.create("second")
    workspaces.set_active("second")
    second = Context(log=print)
    answer[0] = ""
    folders.open_file(None, second, folders.EDS, "EDS", "*.eds", second.eds_dir)
    assert opened[-1] == str(second.eds_dir)


# --- and the way to it from the window --------------------------------------------------------
def test_a_dialog_in_the_window_goes_through_the_same_memory(
    app, home, picked, tmp_path, monkeypatch
):
    """Picked the file, so that is where your databases are -- whether or not
    this particular one turned out to parse."""
    from PySide6.QtWidgets import QMessageBox

    from pycangui.ui.main_window import MainWindow

    QSettings().clear()
    window = MainWindow()
    # The file does not exist, so cantools refuses it and the window offers to
    # load it unchecked. Say no: this test is about the folder, not the file.
    monkeypatch.setattr(messages, "question", lambda *a, **k: QMessageBox.Cancel)
    _opened, answer = picked
    where = tmp_path / "somewhere else"
    where.mkdir()
    answer[0] = str(where / "a.dbc")

    window._load_dbc_dialog()
    assert folders.remembered(window.ctx, folders.DBC) == where
    window.close()


def test_the_tools_menu_can_forget_them(app, home, tmp_path):
    from pycangui.ui.main_window import MainWindow

    QSettings().clear()
    window = MainWindow()
    where = tmp_path / "somewhere else"
    where.mkdir()
    folders.remember(window.ctx, folders.DBC, where / "a.dbc")

    window._forget_folders()
    assert folders.remembered(window.ctx, folders.DBC) is None
    assert "Forgot 1 remembered folder" in window.log.toPlainText()
    window.close()


def test_the_window_says_when_there_was_nothing_to_forget(app, home):
    from pycangui.ui.main_window import MainWindow

    QSettings().clear()
    window = MainWindow()
    before = window.log.toPlainText()
    window._forget_folders()
    assert window.log.toPlainText() != before
    window.close()
