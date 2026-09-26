# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Light, dark, or whatever the operating system is set to.

Qt follows the system's choice by itself; this only adds a way to override it,
under *Tools > Theme*. The choice is kept per person on this machine, in
``QSettings`` beside the window's position, rather than in the workspace: a
workspace handed to somebody else should not change their colours.

Applied through Qt's own colour scheme rather than a palette of pycangui's, so
anything that reads the palette -- the Event Log's colours among them --
follows the change as it happens.

**Not every native style can be dark.** Windows 11's can; the one Qt uses on
Windows 10 (``windowsvista``) draws light whatever is asked for -- which is why
pycangui stayed light on a Windows 10 set to dark, and why choosing Dark there
did nothing. So where the native style cannot draw dark, Qt's own Fusion style
is used for both, from the start. Switching to it only when dark was chosen
would change the controls' sizes as well as their colours on every switch, and
the whole window would reflow; with one style, light and dark are colours only.
"""

from __future__ import annotations

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QApplication, QMenu, QWidget

SETTING = "appearance/theme"
SYSTEM, LIGHT, DARK = "system", "light", "dark"
#: In the order the menu offers them.
CHOICES = ((SYSTEM, "Match the system"), (LIGHT, "Light"), (DARK, "Dark"))
#: Native styles that stay light whatever the colour scheme says.
LIGHT_ONLY = {"windowsvista", "windows"}
#: Qt's own style, which draws dark as well as light on every platform.
FUSION = "Fusion"
TITLE = "Theme"
TIP = "Light, dark, or whichever the operating system is set to. Kept for you on this computer."


def chosen(settings: QSettings | None = None) -> str:
    """The choice remembered, or the system's when there is none or it is not one we know."""
    value = str((settings or QSettings()).value(SETTING, SYSTEM) or SYSTEM)
    return value if value in dict(CHOICES) else SYSTEM


def apply(choice: str, app: QApplication | None = None) -> None:
    """Ask Qt for this colour scheme, or give the choice back to the system,
    in a style that can draw both."""
    app = app or QApplication.instance()
    if app is None:
        return
    if app.style().name().lower() in LIGHT_ONLY:
        app.setStyle(FUSION)
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
