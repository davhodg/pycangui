# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""The Python Console: what is typed there, and what it says back."""

import sys

import pytest

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
