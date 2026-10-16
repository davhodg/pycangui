# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Light, dark, or whatever the operating system is set to.

Qt follows the system's choice by itself; this only adds a way to override it,
under *Tools > Settings > Theme*. The choice is kept per person on this machine, in
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
from PySide6.QtWidgets import QApplication, QItemEditorFactory, QMenu, QWidget

from pycangui import resources

SETTING = "appearance/theme"
SYSTEM, LIGHT, DARK = "system", "light", "dark"
#: In the order the menu offers them.
CHOICES = ((SYSTEM, "Match the system"), (LIGHT, "Light"), (DARK, "Dark"))
#: Native styles that stay light whatever the colour scheme says.
LIGHT_ONLY = {"windowsvista", "windows"}
#: Qt's own style, which draws dark as well as light on every platform.
FUSION = "Fusion"
#: An outline for a tick box in a list or on a group box, for Fusion in the
#: dark. There an unticked one is drawn a shade darker than what is behind
#: it, which is all but nothing: a column of boxes to tick was a blank
#: column until one of them was. Ticked ones have the outline too, so the
#: two are plainly the same thing in two states -- and the tick is then
#: ours to draw, since a box described here is no longer drawn by the style.
TICK_BOX_OUTLINE = (
    "QAbstractItemView::indicator, QGroupBox::indicator "
    "{ border: 1px solid #9a9a9a; border-radius: 2px; } "
    "QAbstractItemView::indicator:checked, QGroupBox::indicator:checked "
    f'{{ image: url("{resources.path("tick.svg").as_posix()}"); }}'
)
#: Whether the style in use is Fusion, noted before a style sheet is set:
#: with one set, the application's style is no longer the one to ask.
_fusion = False
#: The applications whose colour scheme is already being followed.
_followed: set[int] = set()
#: The editor factory in use, kept here: Qt holds it, and Python must too.
_editors: QItemEditorFactory | None = None
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
    global _fusion
    if not app.styleSheet():  # see _fusion
        if app.style().name().lower() in LIGHT_ONLY:
            app.setStyle(FUSION)
        _fusion = app.style().name().lower() == FUSION.lower()
    hints = app.styleHints()
    if choice == LIGHT:
        hints.setColorScheme(Qt.ColorScheme.Light)
    elif choice == DARK:
        hints.setColorScheme(Qt.ColorScheme.Dark)
    else:
        hints.unsetColorScheme()
    outline_tick_boxes(app)
    opaque_cell_editors()
    if id(app) not in _followed:
        # Matching the system, the scheme changes when the system's does.
        _followed.add(id(app))
        hints.colorSchemeChanged.connect(lambda _scheme: outline_tick_boxes(app))


def outline_tick_boxes(app: QApplication) -> None:
    """Give unticked boxes an outline where the style draws them invisibly."""
    dark = app.styleHints().colorScheme() == Qt.ColorScheme.Dark
    wanted = TICK_BOX_OUTLINE if (dark and _fusion) else ""
    if app.styleSheet() != wanted:
        app.setStyleSheet(wanted)


class _OpaqueEditors(QItemEditorFactory):
    """Qt's own cell editors, each painting its own background.

    The Windows 11 style paints none behind the box typed into over a cell,
    so the cell's old value showed through under the new one.
    """

    def __init__(self, qt_own: QItemEditorFactory) -> None:
        super().__init__()
        self._qt_own = qt_own

    def createEditor(self, user_type, parent):
        editor = self._qt_own.createEditor(user_type, parent)
        if editor is not None:
            editor.setAutoFillBackground(True)
        return editor


def opaque_cell_editors() -> None:
    """Have every cell typed into hide the value it is over, once."""
    global _editors
    if _editors is None:
        _editors = _OpaqueEditors(QItemEditorFactory.defaultFactory())
        QItemEditorFactory.setDefaultFactory(_editors)


def choose(choice: str, settings: QSettings | None = None, app: QApplication | None = None) -> None:
    """Remember a choice and apply it straight away."""
    (settings or QSettings()).setValue(SETTING, choice)
    apply(choice, app)


def menu(parent: QWidget, settings: QSettings | None = None) -> QMenu:
    """*Tools > Settings > Theme*: the three choices, the current one ticked."""
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
