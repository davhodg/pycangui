"""Clear on the right-click of a text pane, under the usual Copy and Select All."""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QMenu, QPlainTextEdit

from pycangui.ui import icons


def menu(text: QPlainTextEdit) -> QMenu:
    """The standard menu for the text, with Clear at the bottom. Built apart from being shown."""
    built = text.createStandardContextMenu()
    built.addSeparator()
    icons.beside(built.addAction("Clear", text.clear), icons.CLEAR)
    return built


def add(text: QPlainTextEdit) -> QPlainTextEdit:
    """Give the text pane the menu above on its right-click."""

    def show(at: QPoint) -> None:
        shown = menu(text)
        shown.exec(text.viewport().mapToGlobal(at))
        shown.deleteLater()

    text.setContextMenuPolicy(Qt.CustomContextMenu)
    text.customContextMenuRequested.connect(show)
    return text
