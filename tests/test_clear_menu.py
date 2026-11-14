# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""The right-click shared by the Event Log, the Python Console and an ASCII log."""

from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QPlainTextEdit

from pycangui.ui import clear_menu, folders, icons


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings

    from pycangui.ui.main_window import MainWindow

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    win = MainWindow()
    win.show()
    yield win
    win.close()


def _ctx(tmp_path, said):
    return SimpleNamespace(
        log=lambda m: said.append(("log", m)),
        error=lambda m: said.append(("error", m)),
        user_dir=tmp_path,
    )


def test_copy_and_select_all_are_given_ours_and_clear_all_empties_it(app):
    text = QPlainTextEdit("some text")
    menu = clear_menu.menu(text)
    given = {a.objectName(): a.property(icons.PROPERTY) for a in menu.actions()}
    assert given["edit-copy"] == icons.COPY
    assert given["select-all"] == icons.SELECT_ALL
    assert menu.actions()[-1].property(icons.PROPERTY) == icons.CLEAR
    menu.actions()[-1].trigger()
    assert text.toPlainText() == ""


def test_save_all_comes_after_select_all_and_writes_the_whole_pane(app, tmp_path, monkeypatch):
    said = []
    text = QPlainTextEdit("first line\nsecond line")
    text.setReadOnly(True)
    cursor = text.textCursor()
    cursor.setPosition(0)
    cursor.setPosition(5, cursor.MoveMode.KeepAnchor)  # "first" selected: Save all ignores it
    text.setTextCursor(cursor)
    menu = clear_menu.menu(text, _ctx(tmp_path, said), "Event Log")
    shown = [a for a in menu.actions() if not a.isSeparator()]
    assert shown[-3].objectName() == "select-all"
    assert [a.property(icons.PROPERTY) for a in shown[-2:]] == [icons.SAVE, icons.CLEAR]

    path = tmp_path / "log.txt"
    asked = []
    monkeypatch.setattr(folders, "save_file", lambda *a, **k: asked.append((a, k)) or str(path))
    shown[-2].trigger()
    assert path.read_text(encoding="utf-8") == "first line\nsecond line\n"
    assert asked[0][0][2] == folders.TEXT, "text files have their own folder"
    assert asked[0][1]["suggested"].startswith("event-log_")
    assert [level for level, _ in said] == ["log"]


def test_save_all_cancelled_writes_nothing(app, tmp_path, monkeypatch):
    monkeypatch.setattr(folders, "save_file", lambda *a, **k: "")
    said = []
    clear_menu.save_text(QPlainTextEdit("a line"), _ctx(tmp_path, said), "Console output")
    assert said == [] and list(tmp_path.iterdir()) == []


def test_without_somewhere_to_save_there_is_no_save_all(app):
    text = QPlainTextEdit("x")
    pictures = [a.property(icons.PROPERTY) for a in clear_menu.menu(text).actions()]
    assert icons.SAVE not in pictures and icons.CLEAR in pictures


def test_the_event_log_console_and_ascii_log_all_offer_save_all(window, monkeypatch):
    """One right-click for all three, each with somewhere to save to."""
    asked = []

    class _Shown:
        def exec(self, _at):
            pass

        def deleteLater(self):
            pass

    monkeypatch.setattr(
        clear_menu,
        "menu",
        lambda text, ctx=None, name="": asked.append((text, ctx, name)) or _Shown(),
    )
    for text in (window.log, window.console.output, window.ascii.text):
        text.customContextMenuRequested.emit(text.rect().center())
    assert [t for t, _ctx, _name in asked] == [
        window.log,
        window.console.output,
        window.ascii.text,
    ]
    assert all(ctx is not None and name for _t, ctx, name in asked)
