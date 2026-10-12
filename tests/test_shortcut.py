# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""A menu or desktop entry, starting a source folder's launcher or pip's program."""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings

from pycangui.core import shortcut


@pytest.fixture
def clone(tmp_path, monkeypatch):
    """A source folder with both launchers in it, standing in for this one."""
    folder = tmp_path / "my clone"
    folder.mkdir()
    (folder / "pycangui.cmd").write_text("@echo off\n")
    (folder / "pycangui.sh").write_text("#!/bin/sh\n")
    monkeypatch.setattr(shortcut, "root", lambda: folder)
    # Whatever pip has put in the environment the tests run in is not this clone's.
    monkeypatch.setattr(shortcut, "interpreter", lambda platform=None: None)
    return folder


def test_the_entry_points_at_the_launcher_for_the_system(clone):
    assert shortcut.launcher("win32") == clone / "pycangui.cmd"
    assert shortcut.launcher("linux") == clone / "pycangui.sh"


def test_there_is_no_entry_to_make_without_a_launcher(clone, monkeypatch):
    (clone / "pycangui.sh").unlink()
    assert shortcut.launcher("linux") is None
    assert shortcut.launcher("darwin") is None
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert shortcut.launcher("win32") is None
    with pytest.raises(OSError):
        shortcut.create("linux", {"XDG_DATA_HOME": str(clone)})


def test_a_linux_entry_runs_the_launcher_in_a_terminal(clone, tmp_path):
    made = shortcut.create("linux", {"XDG_DATA_HOME": str(tmp_path / "data")})
    assert made == tmp_path / "data" / "applications" / "pycangui.desktop"
    lines = dict(line.split("=", 1) for line in made.read_text().splitlines()[1:])
    quoted = lines["Exec"]
    assert quoted[0] == quoted[-1] == '"'
    assert re.sub(r"\\(.)", r"\1", quoted[1:-1]) == str(clone / "pycangui.sh")
    assert lines["Path"] == str(clone)
    assert lines["Terminal"] == "true"
    assert Path(lines["Icon"]) == clone / "pycangui" / "resources" / "pycangui.png"


def test_characters_the_desktop_format_reserves_are_escaped():
    assert shortcut._desktop_quoted("/home/a$b/c") == '"/home/a\\$b/c"'


def test_making_it_again_replaces_the_one_before(clone, tmp_path):
    environ = {"XDG_DATA_HOME": str(tmp_path / "data")}
    first = shortcut.create("linux", environ)
    first.write_text("stale")
    assert shortcut.create("linux", environ).read_text().startswith("[Desktop Entry]")


@pytest.mark.skipif(sys.platform != "win32", reason="makes a real Windows shortcut")
def test_a_windows_entry_starts_the_cmd_in_its_folder(clone, tmp_path):
    made = shortcut.create("win32", {"APPDATA": str(tmp_path / "appdata")})
    assert made.name == "pycangui.lnk"
    read = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:LINK);"
            "$s.TargetPath; $s.WorkingDirectory",
        ],
        env={**os.environ, "LINK": str(made)},
        capture_output=True,
        text=True,
    )
    target, folder = read.stdout.splitlines()[:2]
    assert target == str(clone / "pycangui.cmd")
    assert folder == str(clone)


# --- a pip installation ---------------------------------------------------------------------
def test_without_a_launcher_the_entry_starts_the_python_it_is_installed_in(
    clone, tmp_path, monkeypatch
):
    """A pip installation had no entry to make at all: only a clone did."""
    (clone / "pycangui.sh").unlink()
    python = tmp_path / "bin" / "python"
    monkeypatch.setattr(shortcut, "interpreter", lambda platform=None: python)

    assert shortcut.start("linux") == (python, False, ("-m", "pycangui"))
    made = shortcut.create("linux", {"XDG_DATA_HOME": str(tmp_path / "data")})
    lines = dict(line.split("=", 1) for line in made.read_text().splitlines()[1:])
    quoted, *rest = lines["Exec"].rsplit('" ', 1)
    assert re.sub(r"\\(.)", r"\1", quoted[1:]) == str(python) and rest == ["-m pycangui"]
    assert lines["Terminal"] == "false"  # nothing to install, so nothing to watch
    # Not in a folder that might hold a pycangui of its own, which -m would take.
    assert Path(lines["Path"]) == Path.home()


def test_the_launcher_is_what_a_clone_starts_whatever_pip_has_installed(
    clone, tmp_path, monkeypatch
):
    """A clone is installed too, editable, and it is the launcher that notices
    a library added by a pull."""
    monkeypatch.setattr(shortcut, "interpreter", lambda platform=None: tmp_path / "pythonw.exe")
    assert shortcut.start("win32") == (clone / "pycangui.cmd", True, ())


def test_the_interpreter_is_the_one_with_no_console_and_only_where_installed(tmp_path, monkeypatch):
    """Python itself, which is signed, and not the pycangui.exe pip writes, which is not."""
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python.exe"))
    (tmp_path / "pycangui.exe").write_text("")
    assert shortcut.interpreter("win32") == tmp_path / "python.exe"
    (tmp_path / "pythonw.exe").write_text("")
    assert shortcut.interpreter("win32") == tmp_path / "pythonw.exe"
    assert shortcut.interpreter("linux") == tmp_path / "python.exe"

    def missing(name):
        raise shortcut.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(shortcut.metadata, "distribution", missing)
    assert shortcut.interpreter("win32") is None


@pytest.mark.skipif(sys.platform != "win32", reason="makes a real Windows shortcut")
def test_a_windows_entry_for_pip_starts_python_with_the_module(clone, tmp_path, monkeypatch):
    (clone / "pycangui.cmd").unlink()
    python = tmp_path / "pythonw.exe"
    python.write_text("")
    monkeypatch.setattr(shortcut, "interpreter", lambda platform=None: python)
    made = shortcut.create("win32", {"APPDATA": str(tmp_path / "appdata")})
    read = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:LINK);"
            "$s.TargetPath; $s.Arguments; $s.WorkingDirectory",
        ],
        env={**os.environ, "LINK": str(made)},
        capture_output=True,
        text=True,
    )
    target, arguments, folder = read.stdout.splitlines()[:3]
    assert (target, arguments) == (str(python), "-m pycangui")
    assert Path(folder) == Path.home()


# --- the desktop ----------------------------------------------------------------------------
def test_the_desktop_is_offered_on_windows_only(clone):
    assert shortcut.places("win32") == (shortcut.IN_MENU, shortcut.ON_DESKTOP)
    assert shortcut.places("linux") == (shortcut.IN_MENU,)
    with pytest.raises(OSError):
        shortcut.create("linux", {"XDG_DATA_HOME": str(clone)}, shortcut.ON_DESKTOP)


@pytest.mark.skipif(sys.platform != "win32", reason="makes a real Windows shortcut")
def test_a_desktop_entry_is_the_same_link_on_the_desktop(clone, tmp_path):
    environ = {"APPDATA": str(tmp_path / "appdata"), "USERPROFILE": str(tmp_path / "me")}
    made = shortcut.create("win32", environ, shortcut.ON_DESKTOP)
    assert made == tmp_path / "me" / "Desktop" / "pycangui.lnk" and made.is_file()
    assert not shortcut.entry_path("win32", environ).exists()  # the menu was not asked for


# --- in the Tools menu ------------------------------------------------------------------
@pytest.fixture
def no_window_yet(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    monkeypatch.setattr(shortcut, "made", lambda platform=None: False)


def test_the_tools_entries_are_there_only_with_something_to_start(
    no_window_yet, tmp_path, monkeypatch
):
    from pycangui.ui.main_window import MainWindow

    made = []
    monkeypatch.setattr(shortcut, "places", lambda platform=None: ("menu", "desktop"))
    monkeypatch.setattr(
        shortcut, "start", lambda platform=None: shortcut.Start(tmp_path / "pycangui.cmd", True)
    )
    monkeypatch.setattr(shortcut, "create", lambda place: made.append(place) or tmp_path / "entry")
    win = MainWindow()
    try:
        win.shortcut_action.trigger()
        win.desktop_action.trigger()
        assert made == ["menu", "desktop"]
    finally:
        win.close()

    monkeypatch.setattr(shortcut, "start", lambda platform=None: None)
    win = MainWindow()
    try:
        assert win.shortcut_action is None and win.desktop_action is None
    finally:
        win.close()


def _said(tmp_path, monkeypatch, times=2):
    """What the Event Log is sent when a person new to this machine starts pycangui."""
    from pycangui.ui.main_window import SHORTCUT_TOLD_KEY, MainWindow

    monkeypatch.setattr(
        shortcut, "start", lambda platform=None: shortcut.Start(tmp_path / "pycangui.cmd", True)
    )
    win = MainWindow()
    try:
        QSettings().remove(SHORTCUT_TOLD_KEY)  # building the window was a start too
        said = []
        win.events.posted.connect(lambda message, level: said.append(message))
        for _ in range(times):
            win._say_how_to_add_a_shortcut()
        return said
    finally:
        win.close()


def test_where_a_shortcut_is_made_is_said_once(no_window_yet, tmp_path, monkeypatch):
    """The launcher used to ask, at the end of a long download with nobody
    there. Nothing asks now, so the first start says where it is done."""
    assert len(_said(tmp_path, monkeypatch)) == 1


def test_it_is_not_said_with_a_shortcut_there_already(no_window_yet, tmp_path, monkeypatch):
    monkeypatch.setattr(shortcut, "made", lambda platform=None: True)
    assert _said(tmp_path, monkeypatch) == []


def test_neither_launcher_asks_anything_at_the_end_of_setup():
    """Nothing outside the folder is written unasked, and nothing waits for a key."""
    root = Path(__file__).resolve().parent.parent
    for name in ("pycangui.cmd", "pycangui.sh"):
        text = (root / name).read_text(encoding="utf-8")
        assert "pycangui.core.shortcut" not in text
        assert "choice " not in text and "read -r answer" not in text
