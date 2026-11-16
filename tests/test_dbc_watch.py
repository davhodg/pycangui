# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Noticing a CAN database changed on disk, and reading it again."""

import os
import shutil
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QSettings
from PySide6.QtWidgets import QDialog, QMessageBox
from test_a2l_tables import wait_until

from pycangui.ui import database_edit, dbc_watch, file_watch, folders, messages

FILES = Path(__file__).parent / "dbc"


def _settle(seconds=0.5):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        QCoreApplication.processEvents()
        time.sleep(0.01)


def _rewrite(path, old, new):
    """Change the file as another tool would, its time moved on so a coarse clock sees it."""
    path.write_text(path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))


class Answers:
    """messages.question, answered from a list: what was asked is counted."""

    def __init__(self, monkeypatch, *replies):
        self.replies = list(replies)
        self.asked = 0
        monkeypatch.setattr(messages, "question", self)

    def __call__(self, *_a, **_k):
        self.asked += 1
        return self.replies.pop(0) if self.replies else QMessageBox.No


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    from pycangui.ui.main_window import MainWindow

    monkeypatch.setattr(file_watch, "SETTLE_MS", 50)
    monkeypatch.setattr(file_watch, "POLL_MS", 100)
    monkeypatch.setattr(dbc_watch, "GATHER_MS", 100)
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path / "home"))
    QSettings().clear()
    win = MainWindow()
    yield win
    win.database_view._edit = None
    win.dbc_watch.stop()
    if not win._closing:
        win.close()


@pytest.fixture
def network(tmp_path):
    folder = tmp_path / "build"
    folder.mkdir()
    for name in ("tables_and_comments.dbc", "attributes.dbc"):
        shutil.copy(FILES / name, folder / name)
    return folder / "tables_and_comments.dbc", folder / "attributes.dbc"


def _load(window, monkeypatch, path, copy=False):
    """File > Load DBC: kept where it is, or copied into the workspace."""
    Answers(monkeypatch, QMessageBox.Yes if copy else QMessageBox.No)
    assert window.load_dbc_file(str(path))


def test_a_database_loaded_is_watched_and_one_a_hook_loaded_is_not(window, network, monkeypatch):
    first, second = network
    _load(window, monkeypatch, first)
    assert window._load_dbc(str(second))  # as a hook would
    watched = [Path(p).resolve() for p in window.dbc_watch.watched().values()]
    assert watched == [first.resolve()]


def test_a_change_is_asked_about_and_read_again(window, network, monkeypatch):
    first, _ = network
    _load(window, monkeypatch, first)
    answers = Answers(monkeypatch, QMessageBox.Yes)
    _rewrite(first, "GearboxData", "Gearbox")
    wait_until(lambda: window.dbc.message_by_name("Gearbox") is not None, timeout=3)
    assert window.dbc.message_by_name("GearboxData") is None
    _settle()
    assert answers.asked == 1


def test_databases_changed_together_are_asked_about_together(window, network, monkeypatch):
    first, second = network
    _load(window, monkeypatch, first)
    _load(window, monkeypatch, second)
    order = list(window.dbc.databases)
    answers = Answers(monkeypatch, QMessageBox.Yes)
    _rewrite(first, "GearboxData", "Gearbox")
    _rewrite(second, "PumpStatus", "Pump")
    wait_until(lambda: window.dbc.message_by_name("Pump") is not None, timeout=3)
    wait_until(lambda: window.dbc.message_by_name("Gearbox") is not None, timeout=3)
    _settle()
    assert answers.asked == 1
    assert list(window.dbc.databases) == order, "each read again in its place"


def test_no_leaves_it_and_is_not_asked_again_until_it_changes_again(window, network, monkeypatch):
    first, _ = network
    _load(window, monkeypatch, first)
    answers = Answers(monkeypatch, QMessageBox.No)
    _rewrite(first, "GearboxData", "Gearbox")
    wait_until(lambda: answers.asked, timeout=3)
    _settle()
    assert answers.asked == 1
    assert window.dbc.message_by_name("GearboxData") is not None, "the version there was"
    _rewrite(first, "Gearbox", "Box")
    wait_until(lambda: answers.asked == 2, timeout=3)


def test_a_copy_is_watched_through_its_original_and_copied_in_again(window, network, monkeypatch):
    first, _ = network
    _load(window, monkeypatch, first, copy=True)
    (key, watched), *_ = window.dbc_watch.watched().items()
    assert Path(key).resolve() != first.resolve(), "the copy is what is loaded"
    assert watched.resolve() == first.resolve(), "the original is what changes"
    Answers(monkeypatch, QMessageBox.Yes)
    _rewrite(first, "GearboxData", "Gearbox")
    wait_until(lambda: window.dbc.message_by_name("Gearbox") is not None, timeout=3)
    assert Path(key).read_bytes() == first.read_bytes()


def test_removing_it_stops_the_watch(window, network, monkeypatch):
    first, _ = network
    _load(window, monkeypatch, first, copy=True)
    remembered = window.ctx.settings.get("dbc.paths")[0]
    key = next(iter(window.dbc.databases))
    window._remove_dbc(remembered, key)
    assert window.dbc_watch.watched() == {}
    assert not window.ctx.settings.get("dbc.sources")


def _edit_cycle(window, monkeypatch):
    """Change GearboxData's cycle time in the Database pane, unsaved."""
    view = window.database_view
    assert view.select_message("GearboxData")

    def exec_(dialog):
        dialog.cycle.setValue(25)
        dialog._accept()
        return QDialog.Accepted

    monkeypatch.setattr(database_edit.MessageDialog, "exec", exec_)
    view.edit_message()
    assert view.editing().changed
    return view


def test_saving_over_a_watched_file_is_not_a_change_to_ask_about(window, network, monkeypatch):
    first, second = network
    _load(window, monkeypatch, first)
    _load(window, monkeypatch, second)
    view = _edit_cycle(window, monkeypatch)
    monkeypatch.setattr(folders, "save_file", lambda *a, **k: str(second))
    answers = Answers(monkeypatch, QMessageBox.No)  # not used in place of the first
    assert view.save_as()
    _settle(0.8)
    assert answers.asked == 1, "only whether to use the file saved"


def test_unsaved_changes_to_it_are_asked_about_first(window, network, monkeypatch):
    first, _ = network
    _load(window, monkeypatch, first)
    view = _edit_cycle(window, monkeypatch)
    Answers(monkeypatch, QMessageBox.Yes, QMessageBox.Cancel)
    _rewrite(first, "GearboxData", "Gearbox")
    wait_until(lambda: not window.dbc_watch._pending and not window.dbc_watch._gather.isActive())
    _settle()
    assert view.editing() is not None and view.editing().changed, "Cancel keeps them"
    assert window.dbc.message_by_name("GearboxData") is not None

    Answers(monkeypatch, QMessageBox.Yes, QMessageBox.Discard)
    _rewrite(first, "Gearbox", "Box")
    wait_until(lambda: window.dbc.message_by_name("Box") is not None, timeout=3)
    assert view.editing() is None


def test_an_edit_with_nothing_changed_is_not_kept_from_the_old_file(window, network, monkeypatch):
    first, _ = network
    _load(window, monkeypatch, first)
    view = window.database_view
    view._session(next(iter(window.dbc.databases)))
    assert view.editing() is not None and not view.editing().changed
    Answers(monkeypatch, QMessageBox.Yes)
    _rewrite(first, "GearboxData", "Gearbox")
    wait_until(lambda: window.dbc.message_by_name("Gearbox") is not None, timeout=3)
    assert view.editing() is None
