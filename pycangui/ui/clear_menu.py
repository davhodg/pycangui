# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""The right-click of a text pane: the usual Copy and Select All, Save all and Clear all.

*Save all* and *Clear all* rather than *Save* and *Clear*: Copy acts on what is
selected, and these take the whole pane whatever is selected.

Qt makes Copy and Select All, and gives them the system's own icons, which are
not the ones beside every other word in pycangui and stay when icons are
switched off. They are given pycangui's here, by the name Qt gives each.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QMenu, QPlainTextEdit

from pycangui.core.context import Context
from pycangui.ui import folders, icons

TITLE = "Clear all"
SAVE_TITLE = "Save all..."

#: Qt's own items in a text's menu, by the object name Qt gives each.
QT_ITEMS = {"edit-copy": icons.COPY, "select-all": icons.SELECT_ALL}


def menu(text: QPlainTextEdit, ctx: Context | None = None, name: str = "") -> QMenu:
    """The standard menu for the text, then Save all, and Clear all below a line.
    Made, not shown. Save all is there when ``ctx`` is given, with ``name`` the
    start of the file name offered.

    Save all comes after Select All, Qt's last: the two that take the whole
    pane together, after Copy, which takes what is selected.
    """
    built = text.createStandardContextMenu()
    for action in built.actions():
        if (picture := QT_ITEMS.get(action.objectName())) is not None:
            icons.beside(action, picture)
        elif not action.icon().isNull():
            action.setIcon(QIcon())  # one of Qt's with no picture of ours: the word alone
    if ctx is not None:
        save = icons.beside(built.addAction(SAVE_TITLE), icons.SAVE)
        save.setToolTip("Write everything in this pane to a text file.")
        save.triggered.connect(lambda: save_text(text, ctx, name))
        save.setEnabled(bool(text.toPlainText()))
    built.addSeparator()
    icons.beside(built.addAction(TITLE, text.clear), icons.CLEAR)
    return built


def save_text(text: QPlainTextEdit, ctx: Context, name: str) -> None:
    """Ask where, and write the whole pane there as UTF-8 text."""
    path = folders.save_file(
        text,
        ctx,
        folders.TEXT,
        f"Save {name}" if name else "Save",
        "Text (*.txt);;All files (*)",
        ctx.user_dir,
        suggested=folders.stamped(name.lower().replace(" ", "-") or "pycangui", ".txt"),
    )
    if not path:
        return
    try:
        Path(path).write_text(text.toPlainText() + "\n", encoding="utf-8")
    except OSError as exc:
        ctx.error(f"Could not write {path}: {exc}")
        return
    ctx.log(f"{name or 'Text'} written to {path}")


def add(text: QPlainTextEdit, ctx: Context | None = None, name: str = "") -> QPlainTextEdit:
    """Give the text pane the menu above on its right-click."""

    def show(at: QPoint) -> None:
        shown = menu(text, ctx, name)
        shown.exec(text.viewport().mapToGlobal(at))
        shown.deleteLater()

    text.setContextMenuPolicy(Qt.CustomContextMenu)
    text.customContextMenuRequested.connect(show)
    return text
