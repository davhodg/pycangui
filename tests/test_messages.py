# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Message boxes that say their headline inside the box.

macOS shows no title on a message box, so a question kept only there is a
question nobody on a Mac is asked.  The first macOS run found exactly that.
"""

import re
import sys
from pathlib import Path

import pytest
from PySide6.QtWidgets import QMessageBox

from pycangui.ui import messages

PACKAGE = Path(__file__).resolve().parent.parent / "pycangui"


@pytest.fixture
def shown(monkeypatch):
    """Answer the box with a scripted button, keeping what it showed."""
    seen: dict = {}

    def fake_exec(box):
        seen.update(
            title=box.windowTitle(),
            text=box.text(),
            detail=box.informativeText(),
            icon=box.icon(),
            default=box.defaultButton() and box.standardButton(box.defaultButton()),
            buttons=box.standardButtons(),
        )
        return seen.get("answer", QMessageBox.Cancel)

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    return seen


def test_the_headline_is_in_the_box_as_well_as_the_title_bar(app, shown):
    messages.warning(None, "Delete this workspace?", "It cannot be undone.")
    if sys.platform != "darwin":  # macOS clears a message box's title: the reason for all this
        assert shown["title"] == "Delete this workspace?"
    assert shown["text"] == "Delete this workspace?", "what macOS shows, in bold"
    assert shown["detail"] == "It cannot be undone."


@pytest.mark.parametrize(
    ("function", "icon"),
    [
        (messages.information, QMessageBox.Information),
        (messages.warning, QMessageBox.Warning),
        (messages.critical, QMessageBox.Critical),
        (messages.question, QMessageBox.Question),
    ],
)
def test_each_kind_keeps_its_icon(app, shown, function, icon):
    function(None, "Title", "text")
    assert shown["icon"] == icon


def test_buttons_default_and_answer_are_what_the_static_calls_gave(app, shown):
    shown["answer"] = QMessageBox.Yes
    answer = messages.warning(
        None, "Remove plugin", "text", QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel
    )
    assert answer == QMessageBox.Yes
    assert shown["buttons"] == QMessageBox.Yes | QMessageBox.Cancel
    assert shown["default"] == QMessageBox.Cancel

    messages.question(None, "Title", "text")
    assert shown["buttons"] == QMessageBox.Yes | QMessageBox.No, "as QMessageBox.question"


def test_no_static_message_box_is_left_in_pycangui():
    """A new one would lose its title on macOS again, quietly."""
    static = re.compile(r"QMessageBox\.(information|warning|critical|question)\(")
    found = [
        f"{path.relative_to(PACKAGE.parent)}:{number}"
        for path in PACKAGE.rglob("*.py")
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if static.search(line)
    ]
    assert found == [], "use pycangui.ui.messages instead"
