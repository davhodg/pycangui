# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Noticing an A2L changed on disk, and reading it again."""

import os
import shutil
import time

import pytest
from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtWidgets import QMessageBox
from test_a2l_tables import demo, wait_until  # noqa: F401

from pycangui import resources
from pycangui.ui import file_watch


@pytest.fixture
def quick(monkeypatch):
    monkeypatch.setattr(file_watch, "SETTLE_MS", 50)
    monkeypatch.setattr(file_watch, "POLL_MS", 100)


def _settle(seconds=0.4):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        QCoreApplication.processEvents()
        time.sleep(0.01)


def _touched(path, text):
    path.write_text(text, encoding="utf-8")
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))


# --- the watch ----------------------------------------------------------------------------------
def test_a_change_is_said_once_it_has_settled(app, tmp_path, quick):
    path = tmp_path / "a.a2l"
    path.write_text("one", encoding="utf-8")
    watch = file_watch.FileWatch()
    said = []
    watch.changed.connect(said.append)
    watch.watch(path)
    _touched(path, "two")
    wait_until(lambda: said, timeout=3)
    _settle()
    assert said == [str(path)], "once, not once a burst"


def test_the_same_bytes_written_again_are_not_a_change(app, tmp_path, quick):
    path = tmp_path / "a.a2l"
    path.write_text("same", encoding="utf-8")
    watch = file_watch.FileWatch()
    said = []
    watch.changed.connect(said.append)
    watch.watch(path)
    _touched(path, "same")
    _settle(0.6)
    assert said == []


def test_a_file_replaced_by_another_of_the_same_name_is_still_watched(app, tmp_path, quick):
    path = tmp_path / "a.a2l"
    path.write_text("one", encoding="utf-8")
    watch = file_watch.FileWatch()
    said = []
    watch.changed.connect(said.append)
    watch.watch(path)
    new = tmp_path / "a.a2l.tmp"
    new.write_text("two", encoding="utf-8")
    os.replace(new, path)  # how a build tool or an editor saves
    wait_until(lambda: said, timeout=3)
    _touched(path, "three")
    wait_until(lambda: len(said) == 2, timeout=3)


def test_one_renamed_over_it_with_the_same_size_and_time_is_a_change(app, tmp_path, quick):
    """Windows's clock for file times moves only every few milliseconds, so a
    file written and renamed over the old one at once can have the same size
    and time: it is told apart by being another file."""
    path = tmp_path / "a.a2l"
    path.write_text("one", encoding="utf-8")
    stat = path.stat()
    watch = file_watch.FileWatch()
    said = []
    watch.changed.connect(said.append)
    watch.watch(path)
    new = tmp_path / "a.a2l.tmp"
    new.write_text("two", encoding="utf-8")
    os.replace(new, path)
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))  # the old one's time
    wait_until(lambda: said, timeout=3)


# --- the pane ------------------------------------------------------------------------------------
@pytest.fixture
def pane(demo, tmp_path, quick):  # noqa: F811
    from pycangui.ui.xcp_view import XcpView

    manager, _slave, values, _order = demo
    copy = tmp_path / "demo.a2l"
    shutil.copy(resources.path("demo.a2l"), copy)
    manager._ctx.settings.set("xcp.a2l", str(copy))
    manager.load_a2l(str(copy))
    view = XcpView(manager, manager._ctx)
    yield view, manager, values, copy
    view.watch.stop()
    view.deleteLater()


def test_the_a2l_is_chosen_above_the_tabs(pane):
    view, *_ = pane
    assert not view.tabs.isAncestorOf(view.a2l_btn), "both tabs use it"


def test_reading_it_again_keeps_the_ticks_and_the_open_tables(pane):
    view, manager, values, _copy = pane
    view._items["EngineSpeed"].setCheckState(5, Qt.Checked)
    view.open_table("IgnitionMap")
    assert view.reload_a2l()
    assert "EngineSpeed" in manager.polled
    assert view._items["EngineSpeed"].checkState(5) == Qt.Checked
    assert "IgnitionMap" in view._tables
    wait_until(lambda: "IgnitionMap[0][0]" in values)


def test_a_parameter_gone_from_the_new_file_is_no_longer_polled(pane):
    view, manager, _values, copy = pane
    view._items["EngineSpeed"].setCheckState(5, Qt.Checked)
    text = copy.read_text(encoding="utf-8").replace(
        'EngineSpeed "Engine speed"', 'Rpm "Engine speed"'
    )
    copy.write_text(text, encoding="utf-8")
    assert view.reload_a2l()
    assert "EngineSpeed" not in manager.polled


def test_a_change_on_disk_is_asked_about_and_read_again(pane, monkeypatch):
    _view, manager, _values, copy = pane
    from pycangui.ui import messages

    asked = []
    monkeypatch.setattr(messages, "question", lambda *a, **k: asked.append(a) or QMessageBox.Yes)
    loaded = []
    manager.a2l_loaded.connect(loaded.append)
    _touched(copy, copy.read_text(encoding="utf-8") + "\n/* a new build */\n")
    wait_until(lambda: loaded, timeout=3)
    assert len(asked) == 1


def test_no_is_not_asked_again_until_it_changes_again(pane, monkeypatch):
    _view, _manager, _values, copy = pane
    from pycangui.ui import messages

    asked = []
    monkeypatch.setattr(messages, "question", lambda *a, **k: asked.append(a) or QMessageBox.No)
    _touched(copy, copy.read_text(encoding="utf-8") + "\n/* one */\n")
    wait_until(lambda: asked, timeout=3)
    _settle(0.5)
    assert len(asked) == 1
    _touched(copy, copy.read_text(encoding="utf-8") + "\n/* two */\n")
    wait_until(lambda: len(asked) == 2, timeout=3)


def test_during_a_transfer_the_question_waits_for_it_to_end(pane, monkeypatch):
    view, manager, _values, copy = pane
    from pycangui.ui import messages

    asked = []
    monkeypatch.setattr(messages, "question", lambda *a, **k: asked.append(a) or QMessageBox.No)
    monkeypatch.setattr(type(manager), "is_transferring", property(lambda _self: True))
    _touched(copy, copy.read_text(encoding="utf-8") + "\n/* mid flash */\n")
    wait_until(lambda: view._change_waiting, timeout=3)
    assert asked == []
    monkeypatch.setattr(type(manager), "is_transferring", property(lambda _self: False))
    manager.transferring.emit(False)
    assert len(asked) == 1


def test_a_copy_in_the_workspace_is_copied_in_again_from_where_it_came_from(pane, tmp_path):
    view, manager, _values, copy = pane
    original = tmp_path / "build" / "demo.a2l"
    original.parent.mkdir()
    shutil.copy(copy, original)
    manager._ctx.settings.set("xcp.a2l_source", str(original))
    view._watch_a2l()
    assert view.watch.path == original, "the original is the one that changes"
    original.write_text(
        original.read_text(encoding="utf-8").replace("pycangui demo 1.0", "pycangui demo 1.1"),
        encoding="utf-8",
    )
    assert view.reload_a2l()
    assert manager.a2l.epk == "pycangui demo 1.1"
    assert copy.read_text(encoding="utf-8") == original.read_text(encoding="utf-8")
