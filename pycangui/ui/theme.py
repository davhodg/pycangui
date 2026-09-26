# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Light, dark, or whatever the operating system is set to.

Qt follows the system's choice by itself; this only adds a way to override it,
under *Tools > Theme*. The choice is kept per person on this machine, in
``QSettings`` beside the window's position, rather than in the workspace: a
workspace handed to somebody else should not change their colours.

Applied through Qt's own colour scheme rather than a palette of pycangui's, so
the native style draws both, and anything that reads the palette -- the Event
Log's colours among them -- follows the change as it happens.
"""

from __future__ import annotations

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QApplication, QMenu, QWidget

SETTING = "appearance/theme"
SYSTEM, LIGHT, DARK = "system", "light", "dark"
#: In the order the menu offers them.
CHOICES = ((SYSTEM, "Match the system"), (LIGHT, "Light"), (DARK, "Dark"))
TITLE = "Theme"
TIP = "Light, dark, or whichever the operating system is set to. Kept for you on this computer."


def chosen(settings: QSettings | None = None) -> str:
    """The choice remembered, or the system's when there is none or it is not one we know."""
    value = str((settings or QSettings()).value(SETTING, SYSTEM) or SYSTEM)
    return value if value in dict(CHOICES) else SYSTEM


def apply(choice: str, app: QApplication | None = None) -> None:
    """Ask Qt for this colour scheme, or give the choice back to the system."""
    app = app or QApplication.instance()
    if app is None:
        return
    hints = app.styleHints()
    if choice == LIGHT:
        hints.setColorScheme(Qt.ColorScheme.Light)
    elif choice == DARK:
        hints.setColorScheme(Qt.ColorScheme.Dark)
    else:
        hints.unsetColorScheme()


def choose(choice: str, settings: QSettings | None = None, app: QApplication | None = None) -> None:
    """Remember a choice and apply it straight away."""
    (settings or QSettings()).setValue(SETTING, choice)
    apply(choice, app)


def menu(parent: QWidget, settings: QSettings | None = None) -> QMenu:
    """*Tools > Theme*: the three choices, the current one ticked."""
    out = QMenu(TITLE, parent)
    out.setToolTipsVisible(True)
    out.menuAction().setToolTip(TIP)
    group = QActionGroup(out)
    group.setExclusive(True)
    current = chosen(settings)
    for value, label in CHOICES:
        action = QAction(label, out, checkable=True)
        action.setData(value)
        action.setChecked(value == current)
        action.triggered.connect(lambda _=False, v=value: choose(v, settings))
        group.addAction(action)
        out.addAction(action)
    return out
