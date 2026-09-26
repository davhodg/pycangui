# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""A menu entry for a source folder, starting its launcher."""

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


# --- in the Tools menu ------------------------------------------------------------------
def test_the_tools_entry_is_there_only_for_a_source_folder(app, tmp_path, monkeypatch):
    from pycangui.ui.main_window import MainWindow

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    made = []
    monkeypatch.setattr(shortcut, "launcher", lambda platform=None: tmp_path / "pycangui.cmd")
    monkeypatch.setattr(shortcut, "create", lambda: made.append(1) or tmp_path / "entry")
    win = MainWindow()
    try:
        win.shortcut_action.trigger()
        assert made == [1]
    finally:
        win.close()

    monkeypatch.setattr(shortcut, "launcher", lambda platform=None: None)
    win = MainWindow()
    try:
        assert win.shortcut_action is None
    finally:
        win.close()
