# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Offering pycangui for .dcf and .eds files, however it was installed."""

import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings

from pycangui.core import file_types, shortcut

EXTENSIONS = [extension for extension, _prog_id, _description in file_types.TYPES]


@pytest.fixture
def no_launcher(monkeypatch):
    monkeypatch.setattr(shortcut, "launcher", lambda platform=None: None)


def test_an_installed_build_is_started_by_its_own_program(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert file_types.command("win32") == [sys.executable]


def test_a_source_folder_is_started_by_its_launcher(tmp_path, monkeypatch):
    (tmp_path / "pycangui.cmd").write_text("@echo off\n")
    monkeypatch.setattr(shortcut, "root", lambda: tmp_path)
    assert file_types.command("win32") == [str(tmp_path / "pycangui.cmd")]


def test_a_pip_installation_is_started_by_its_python(no_launcher):
    start = file_types.command("linux")
    assert start[0] == sys.executable and start[1:] == ["-m", "pycangui"]
    start = file_types.command("win32")
    assert Path(start[0]).parent == Path(sys.executable).parent and start[1:] == ["-m", "pycangui"]


def test_there_is_nothing_to_register_with_on_macos(no_launcher):
    assert file_types.command("darwin") is None
    with pytest.raises(OSError):
        file_types.register("darwin")


def test_windows_offers_pycangui_without_taking_the_files_over():
    start = [r"C:\Program Files\pycangui\pycangui.exe"]
    entries = file_types.windows_entries(start, Path(start[0]))
    values = {(key, name): value for key, name, value in entries}
    for extension, prog_id, _description in file_types.TYPES:
        assert (rf"Software\Classes\{extension}\OpenWithProgids", prog_id) in values
        opens = values[(rf"Software\Classes\{prog_id}\shell\open\command", "")]
        assert opens.startswith('"C:\\Program Files\\pycangui\\pycangui.exe"'), "quoted: a space"
        assert opens.endswith('"%1"')
        assert (rf"Software\Classes\{extension}", "") not in values, "the default is left alone"


def test_linux_says_what_the_files_are_and_who_opens_them(tmp_path):
    start = ["/opt/py thon/bin/python", "-m", "pycangui"]
    where = file_types._register_linux(start, {"XDG_DATA_HOME": str(tmp_path)})
    types = (tmp_path / "mime" / "packages" / file_types.MIME_PACKAGE).read_text(encoding="utf-8")
    entry = Path(where).read_text(encoding="utf-8")
    for extension in EXTENSIONS:
        assert f'"*{extension}"' in types and file_types.MIME[extension] in types
        assert file_types.MIME[extension] in entry
    exec_line = next(line for line in entry.splitlines() if line.startswith("Exec="))
    assert '"/opt/py thon/bin/python"' in exec_line and exec_line.endswith("%f")


def test_the_tools_menu_registers_them(app, tmp_path, monkeypatch):
    from pycangui.ui.main_window import MainWindow

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    asked = []
    monkeypatch.setattr(file_types, "register", lambda: asked.append(1) or "somewhere")
    window = MainWindow()
    try:
        assert window.file_types_action is not None
        window.file_types_action.trigger()
        assert asked == [1]
    finally:
        window.close()
