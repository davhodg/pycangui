# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""File > Run script... and File > Recent scripts."""

import pytest
from PySide6.QtCore import QSettings

from pycangui.ui import folders, messages
from pycangui.ui.scripts_menu import MOST_RECENT


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    from pycangui.ui.main_window import MainWindow

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    win = MainWindow()
    yield win
    win.close()


def script(folder, name="test.py", text="print('ran', 6 * 7)\n"):
    path = folder / name
    path.write_text(text, encoding="utf-8")
    return path


def test_run_script_runs_it_in_the_console_and_shows_the_console(window, tmp_path, monkeypatch):
    path = script(tmp_path)
    monkeypatch.setattr(folders, "open_file", lambda *a, **k: str(path))
    window.scripts_menu.run_dialog()
    assert "ran 42" in window.console.output.toPlainText()
    assert not window.panes.docks["console"].isHidden(), "where what it printed is"


def test_what_was_run_is_listed_newest_first_once_each(window, tmp_path):
    first, second = script(tmp_path, "a.py"), script(tmp_path, "b.py")
    for path in (first, second, first):
        window.run_script_in_console(str(path))
    assert window.scripts_menu.recent() == [first, second]


def test_the_consoles_own_button_is_remembered_too(window, tmp_path):
    path = script(tmp_path)
    window.console.run_file(str(path))
    assert window.scripts_menu.recent() == [path]


def test_only_the_last_few_are_kept(window, tmp_path):
    paths = [script(tmp_path, f"s{n}.py") for n in range(MOST_RECENT + 2)]
    for path in paths:
        window.run_script_in_console(str(path))
    assert window.scripts_menu.recent() == list(reversed(paths))[:MOST_RECENT]


def test_a_recent_script_runs_from_the_menu(window, tmp_path):
    path = script(tmp_path)
    window.run_script_in_console(str(path))
    window.console.output.clear()
    menu = window.scripts_menu.recent_menu
    menu.aboutToShow.emit()
    menu.actions()[0].trigger()
    assert "ran 42" in window.console.output.toPlainText()


def test_one_that_has_gone_is_said_and_taken_off(window, tmp_path, monkeypatch):
    path = script(tmp_path)
    window.run_script_in_console(str(path))
    path.unlink()
    said = []
    monkeypatch.setattr(messages, "warning", lambda *a, **k: said.append(a))
    window.scripts_menu.run_recent(path)
    assert said and window.scripts_menu.recent() == []


def test_one_in_the_workspace_is_remembered_relative_to_it(window):
    """So a workspace taken to another computer still finds its own scripts."""
    folder = window.ctx.workspace_dir / "scripts"
    folder.mkdir(parents=True, exist_ok=True)
    path = script(folder)
    window.run_script_in_console(str(path))
    stored = window.ctx.settings.get("scripts.recent")
    assert not stored[0].startswith(str(window.ctx.workspace_dir))
    assert window.scripts_menu.recent() == [path.resolve()]
