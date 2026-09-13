# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Message boxes whose headline is in the box, not only in its title bar.

macOS does not show a message box's title: Apple's guidelines leave it out and
Qt follows them.  Everywhere pycangui used ``QMessageBox.warning`` and friends,
whatever the title said was lost there -- "Delete this workspace?" arrived as
an explanation with Yes and Cancel under it and no question anywhere.

These are drop-in replacements with the same arguments.  The title goes in the
title bar as before and is repeated as the box's text, which macOS shows in
bold; what was the text becomes the informative text beneath it.  Windows and
Linux gain the headline inside the box too, which reads just as well there.

tests/test_messages.py fails if a static ``QMessageBox`` call comes back.
"""

from __future__ import annotations

from PySide6.QtWidgets import QMessageBox, QWidget

Button = QMessageBox.StandardButton


def show(
    parent: QWidget | None,
    icon: QMessageBox.Icon,
    title: str,
    text: str,
    buttons: Button,
    default: Button = QMessageBox.NoButton,
) -> Button:
    """Build, show and wait.  Returns the button that was pressed."""
    box = QMessageBox(icon, title, title, buttons, parent)
    box.setInformativeText(text)
    if default != QMessageBox.NoButton:
        box.setDefaultButton(default)
    return Button(box.exec())


def information(
    parent: QWidget | None,
    title: str,
    text: str,
    buttons: Button = QMessageBox.Ok,
    default: Button = QMessageBox.NoButton,
) -> Button:
    return show(parent, QMessageBox.Information, title, text, buttons, default)


def warning(
    parent: QWidget | None,
    title: str,
    text: str,
    buttons: Button = QMessageBox.Ok,
    default: Button = QMessageBox.NoButton,
) -> Button:
    return show(parent, QMessageBox.Warning, title, text, buttons, default)


def critical(
    parent: QWidget | None,
    title: str,
    text: str,
    buttons: Button = QMessageBox.Ok,
    default: Button = QMessageBox.NoButton,
) -> Button:
    return show(parent, QMessageBox.Critical, title, text, buttons, default)


def question(
    parent: QWidget | None,
    title: str,
    text: str,
    buttons: Button = QMessageBox.Yes | QMessageBox.No,
    default: Button = QMessageBox.NoButton,
) -> Button:
    return show(parent, QMessageBox.Question, title, text, buttons, default)
