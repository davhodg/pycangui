# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""The Python Console: what is typed there, and what it says back."""

import sys
import time

import pytest
from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QKeyEvent

from pycangui.core.context import Context
from pycangui.ui.console_view import ConsoleView


@pytest.fixture
def console(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    # What pycangui's own hook would be called with, had the console used it.
    reported = []
    monkeypatch.setattr(sys, "excepthook", lambda *a: reported.append(a))
    view = ConsoleView({"window": object()}, Context(log=print))
    view.reported = reported
    return view


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


def type_in(view: ConsoleView, line: str) -> str:
    before = view.output.toPlainText()
    view.input.setText(line)
    view._on_enter()
    return view.output.toPlainText()[len(before) :]


def test_a_typing_mistake_is_shown_here_not_reported_as_a_bug(console):
    """A mistyped 'window' was announced as "a bug in pycangui"."""
    said = type_in(console, "indow")
    assert "NameError: name 'indow' is not defined" in said
    assert console.reported == [], "not handed to the hook that reports pycangui's own faults"


def test_a_syntax_error_is_shown_here_too(console):
    said = type_in(console, "1 +)")
    assert "SyntaxError" in said
    assert console.reported == []


def test_what_is_typed_still_runs(console):
    assert "2" in type_in(console, "1 + 1")
    assert "object" in type_in(console, "window")


def test_the_right_click_clears_the_output(console):
    from PySide6.QtCore import Qt

    from pycangui.ui import clear_menu, icons

    type_in(console, "print('hello')")
    assert console.output.toPlainText()
    assert console.output.contextMenuPolicy() == Qt.CustomContextMenu
    menu = clear_menu.menu(console.output)
    next(a for a in menu.actions() if a.property(icons.PROPERTY) == icons.CLEAR).trigger()
    assert console.output.document().isEmpty()


def test_a_block_over_several_lines_is_run_when_it_ends(console):
    console.input.setText("for n in range(2):")
    console.input.entered.emit()
    assert console.prompt.text() == "..."
    type_in(console, "    print('line', n)")
    shown = type_in(console, "")
    assert "line 0" in shown and "line 1" in shown
    assert console.prompt.text() == ">>>"


def test_several_lines_typed_or_pasted_run_together(console):
    shown = type_in(console, "a = 2\nfor n in range(2):\n    print('line', n)\na * 3")
    assert "line 0" in shown and "line 1" in shown
    assert shown.rstrip().endswith("6"), "a value is shown, as the prompt shows it"
    assert console.prompt.text() == ">>>"


def test_shift_enter_starts_another_line_indented_after_a_colon(console):
    console.input.setText("for n in range(2):")
    console.input.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.ShiftModifier))
    assert console.input.text() == "for n in range(2):\n    "
    console.input.insertPlainText("print(n)")
    console.output.clear()
    console.input.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier))
    assert "0\n1" in console.output.toPlainText()
    assert console.input.text() == ""


def test_a_mistake_in_several_lines_is_a_syntax_error_here(console):
    assert "SyntaxError" in type_in(console, "a = (\nb = 1")
    assert console.reported == []


def test_what_is_printed_is_shown_while_it_runs(console):
    seen = []
    console.namespace["peek"] = lambda: seen.append(console.output.toPlainText())
    console.run_source("print('first')\npeek()\n")
    assert "first" in seen[0], "in the pane before the script has finished"


def test_stop_ends_a_script_at_its_next_wait(console):
    QTimer.singleShot(100, console.stop)
    started = time.monotonic()
    console.run_source("while True:\n    wait(0.05)\n", "forever.py")
    assert time.monotonic() - started < 3
    assert "Stopped." in console.output.toPlainText()
    assert not console.running and console.input.isEnabled()
    assert not console.stop_button.isEnabled()


def test_stop_ends_a_script_that_only_prints(console, monkeypatch):
    from pycangui.ui import console_view

    monkeypatch.setattr(console_view, "REDRAW_EVERY", 0)
    QTimer.singleShot(100, console.stop)
    started = time.monotonic()
    console.run_source("while True:\n    print('x')\n", "chatty.py")
    assert time.monotonic() - started < 5
    assert not console.running


def test_stop_is_only_pressable_while_something_runs(console):
    seen = []
    console.namespace["peek"] = lambda: seen.append(
        (console.stop_button.isEnabled(), console.input.isEnabled())
    )
    assert not console.stop_button.isEnabled()
    type_in(console, "peek()")
    assert seen == [(True, False)]


def test_there_is_no_clear_button_beside_run_and_stop(console):
    from PySide6.QtWidgets import QPushButton

    from pycangui.ui import icons

    pictures = {b.property(icons.PROPERTY) for b in console.findChildren(QPushButton)}
    assert icons.CLEAR not in pictures and {icons.START, icons.STOP} <= pictures


def test_the_window_does_not_close_while_a_script_waits(window, monkeypatch):
    from pycangui.ui import messages

    boxes = []
    monkeypatch.setattr(messages, "warning", lambda _parent, title, text, *a: boxes.append(title))
    console = window.console
    console.run_source("window.close()\nprint('still open:', window.isVisible())\n")
    assert "still open: True" in console.output.toPlainText()
    assert len(boxes) == 1
