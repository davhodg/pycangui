# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""A standard icon beside the word, for the actions that have one.

Beside, never instead: the word stays, and the icon helps somebody who reads
little English find Clear, Save or Stop at a glance. Only actions whose
meaning is the standard one get an icon. A picture made up for "Scan for
nodes" would have to be learnt, which is the opposite of the point.

The icons are the system's own, the set Qt already draws on Copy and Select
All in a text box's right-click menu: Windows' icon font, the desktop's icon
theme on Linux. pycangui ships none. Where the system has no icon for one,
the item is the word alone, as it always was.

A few that Qt has no name for -- a pin, a pane going to a window of its own
-- are drawn straight from Windows' icon font, in the text colour of the
moment, so they follow a change of theme like the words beside them. Stop
is drawn from the font too, solid, and falls back to the theme's own where
the font is missing.
"""

from __future__ import annotations

from PySide6.QtCore import QRect, QSettings, QSize, Qt
from PySide6.QtGui import (
    QFont,
    QFontDatabase,
    QGuiApplication,
    QIcon,
    QIconEngine,
    QPainter,
    QPalette,
    QPixmap,
)
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QMessageBox

_Theme = QIcon.ThemeIcon

ADD = "add"
ATTACH = "attach"
BACK = "back"
BOOKMARK = "bookmark"
BOOKMARKED = "bookmarked"
BROWSE = "browse"
BUG = "bug"
CANCEL = "cancel"
CHECK = "check"
CLEAR = "clear"
CLOSE = "close"
CONNECT = "connect"
COPY = "copy"
DELETE = "delete"
DETACH = "detach"
DIAGNOSTICS = "diagnostics"
DISCARD = "discard"
DISCONNECT = "disconnect"
DOWN = "down"
DOWNLOAD = "download"
EDIT = "edit"
EXIT = "exit"
EXPORT = "export"
FILTER = "filter"
FOLDER = "folder"
HELP = "help"
HOME = "home"
INFO = "info"
KNOWN_IDS = "known ids"
LICENCE = "licence"
MANAGE = "manage"
NEW = "new"
LIST = "list"
LOGIN = "login"
OPEN = "open"
PAUSE = "pause"
PIN = "pin"
PINNED = "pinned"
PLOT = "plot"
READ = "read"
RECORD = "record"
RECOVER = "recover"
RESET = "reset"
RESTART = "restart"
SEARCH = "search"
RELOAD = "reload"
REMOVE = "remove"
SAVE = "save"
SAVE_AS = "save as"
SELECT_ALL = "select all"
SEND = "send"
SETTINGS = "settings"
START = "start"
STOP = "stop"
SWITCH = "switch"
UNDO = "undo"
UPDATES = "updates"
UNLOCK = "unlock"
UP = "up"
UPLOAD = "upload"
VIEW = "view"
WRITE = "write"

#: Each of pycangui's names for an action, and the system icon for it.
THEME: dict[str, QIcon.ThemeIcon] = {
    ADD: _Theme.ListAdd,
    BACK: _Theme.GoPrevious,
    BROWSE: _Theme.FolderOpen,
    CANCEL: _Theme.ProcessStop,
    CLEAR: _Theme.EditClear,
    CLOSE: _Theme.WindowClose,  # where the font below is missing
    CONNECT: _Theme.NetworkWired,
    COPY: _Theme.EditCopy,
    DELETE: _Theme.EditDelete,
    DISCARD: _Theme.DocumentRevert,
    DISCONNECT: _Theme.NetworkOffline,
    DOWN: _Theme.GoDown,
    EXIT: _Theme.ApplicationExit,  # where the font below is missing
    EXPORT: _Theme.DocumentSave,  # it is saving; DocumentSend is drawn as Send's paper plane
    FOLDER: _Theme.FolderOpen,
    HOME: _Theme.GoHome,
    NEW: _Theme.DocumentNew,
    OPEN: _Theme.DocumentOpen,
    PAUSE: _Theme.MediaPlaybackPause,
    RECORD: _Theme.MediaRecord,
    RECOVER: _Theme.SystemReboot,
    RELOAD: _Theme.ViewRefresh,
    REMOVE: _Theme.ListRemove,
    SAVE: _Theme.DocumentSave,
    SAVE_AS: _Theme.DocumentSaveAs,
    SELECT_ALL: _Theme.EditSelectAll,
    SEND: _Theme.MailSend,
    START: _Theme.MediaPlaybackStart,
    STOP: _Theme.MediaPlaybackStop,  # where the font below is missing
    UNDO: _Theme.EditUndo,
    UP: _Theme.GoUp,
}

#: The ones Qt has no name for, as characters of Windows' icon font. The same
#: code in Windows 11's Segoe Fluent Icons and Windows 10's Segoe MDL2 Assets.
GLYPH: dict[str, int] = {
    ATTACH: 0xE944,  # back into the window
    BOOKMARK: 0xE734,  # a star, outlined: showing everything
    BOOKMARKED: 0xE735,  # a star, filled in: showing only the bookmarked
    BUG: 0xEBE8,  # a beetle
    CHECK: 0xE73E,  # a tick: the magnifying glass is kept for search
    # The theme's own X is drawn bigger and bolder than the icons beside it.
    CLOSE: 0xE711,
    DETACH: 0xE8A7,  # open in a new window
    DIAGNOSTICS: 0xE9D9,  # a pulse on a screen, as Windows has it
    DOWNLOAD: 0xE896,  # an arrow down into a tray
    EDIT: 0xE70F,  # a pencil, as Write
    # The theme's own is an X, the same as Remove DBC's beside it.
    EXIT: 0xE89F,  # an arrow into a box: Windows 10 has no door
    FILTER: 0xE71C,  # a funnel
    HELP: 0xE897,  # a question mark
    INFO: 0xE946,  # an i in a circle
    KNOWN_IDS: 0xE8EC,  # a tag: the name each id goes by
    LICENCE: 0xEB95,  # a certificate
    LIST: 0xE8FD,  # a bulleted list
    LOGIN: 0xE8D7,  # a key
    MANAGE: 0xE90F,  # a spanner: the gear is Settings
    PIN: 0xE718,  # a pin, outlined: not pinned yet
    PINNED: 0xE842,  # a pin, filled in: pinned, and pressing it unpins
    PLOT: 0xE9D2,  # a small chart
    READ: 0xE736,  # an open book
    RESET: 0xE777,  # an arrow back round: the other way from Reload
    RESTART: 0xE7E8,  # a power button: a device starting again
    SEARCH: 0xE721,  # a magnifying glass
    SETTINGS: 0xE713,  # a gear
    # Solid: the theme's own stop is a hollow square, which beside the
    # Cyclic tick boxes read as one more of them.
    STOP: 0xE73B,
    SWITCH: 0xE8AB,  # two arrows, one each way
    UNLOCK: 0xE785,  # an open padlock
    UPDATES: 0xE895,  # two arrows round, as Windows Update has them
    UPLOAD: 0xE898,  # an arrow up out of a tray
    VIEW: 0xE890,  # an eye
    WRITE: 0xE70F,  # a pencil
}

#: Better ones that only Windows 11's font has.
FLUENT_ONLY: dict[str, int] = {EXIT: 0xF3B1}  # a door, with the arrow going out

#: How much of the icon's room a glyph takes, where not all of it: the solid
#: square fills its whole cell and looked twice the weight of those beside it.
GLYPH_SCALE: dict[str, float] = {STOP: 0.7}

#: Windows 11's icon font first, then Windows 10's.
GLYPH_FONTS = ("Segoe Fluent Icons", "Segoe MDL2 Assets")


#: Where *Tools > Settings > Show icons* is kept: with the computer,
#: like the theme, since it is about who sits at it rather than the work.
SETTING = "icons/shown"
TITLE = "Show icons"
TIP = (
    "A standard icon beside the word on common buttons and menu items:\n"
    "Clear, Save, Stop and the like. The words stay either way. Kept for\n"
    "you on this computer."
)

#: Which icon a button or menu item was given, kept on it so that turning
#: icons off and on again can find it and put it back.
PROPERTY = "pycangui_icon"

#: The setting, read once: asked on every button made, it would be read
#: from the disk hundreds of times at startup.
_shown: bool | None = None


def shown() -> bool:
    """Whether icons go beside the words. On unless somebody turned them off."""
    global _shown
    if _shown is None:
        _shown = str(QSettings().value(SETTING, "true")).lower() != "false"
    return _shown


def set_shown(on: bool) -> None:
    """Turn icons on or off, at once, everywhere they were asked for."""
    global _shown
    QSettings().setValue(SETTING, on)
    _shown = on
    for widget in QApplication.allWidgets():
        for thing in (widget, *widget.actions()):
            name = thing.property(PROPERTY)
            if name and on:
                _draw(thing, name)
            elif name:
                thing.setIcon(QIcon())


def beside[Thing](thing: Thing, name: str) -> Thing:
    """Give a button or menu item the system's icon for ``name``, if it has one.

    Returns ``thing``, so that it can wrap the line that makes it.
    """
    thing.setProperty(PROPERTY, name)
    if shown():
        _draw(thing, name)
    return thing


def _draw(thing, name: str) -> None:
    code = GLYPH.get(name)
    if name in FLUENT_ONLY and glyph_font() == GLYPH_FONTS[0]:
        code = FLUENT_ONLY[name]
    icon = _glyph(code, GLYPH_SCALE.get(name, 1.0)) if code else None
    if icon is None and name in THEME:
        icon = QIcon.fromTheme(THEME[name])
    if icon is not None and not icon.isNull():
        thing.setIcon(icon)


_Box = QDialogButtonBox.StandardButton

#: The buttons Qt names itself, in every dialog and message box. A message
#: box's buttons are the same ones under another enum.
STANDARD: dict[QDialogButtonBox.StandardButton, str] = {
    _Box.Ok: CHECK,
    _Box.Yes: CHECK,
    _Box.YesToAll: CHECK,
    _Box.Apply: CHECK,
    _Box.Cancel: CANCEL,
    _Box.No: CANCEL,
    _Box.NoToAll: CANCEL,
    _Box.Abort: CANCEL,
    _Box.Close: CLOSE,
    _Box.Save: SAVE,
    _Box.SaveAll: SAVE,
    _Box.Open: OPEN,
    _Box.Discard: DISCARD,
    _Box.Help: HELP,
    _Box.Reset: RESET,
    _Box.RestoreDefaults: RESET,
    _Box.Retry: RELOAD,
}


def on_buttons[Box: (QDialogButtonBox, QMessageBox)](box: Box) -> Box:
    """Icons beside a dialog's OK, Cancel, Close and the rest.

    Only on those still without one: a button given its own word, and an
    icon for it, keeps that. Returns ``box``, to wrap the line that makes it.
    """
    for button in box.buttons():
        if button.property(PROPERTY) or not button.icon().isNull():
            continue
        name = STANDARD.get(_Box(box.standardButton(button).value))
        if name is not None:
            beside(button, name)
    return box


# --- drawn from the icon font --------------------------------------------------------
def glyph_font() -> str:
    """The icon font this machine has, or "" where it has none."""
    have = set(QFontDatabase.families())
    return next((family for family in GLYPH_FONTS if family in have), "")


#: Engines handed to Qt, kept here so that Python does not collect one that
#: an icon is still drawing with.
_ENGINES: list[QIconEngine] = []


class _GlyphEngine(QIconEngine):
    """One character of the icon font, in the palette's text colour when drawn."""

    def __init__(self, family: str, code: int, scale: float = 1.0) -> None:
        super().__init__()
        self.family = family
        self.code = code
        self.scale = scale

    def paint(self, painter, rect: QRect, mode, state) -> None:
        palette = QGuiApplication.palette()
        group = QPalette.Disabled if mode == QIcon.Disabled else QPalette.Active
        font = QFont(self.family)
        font.setPixelSize(max(1, round(min(rect.width(), rect.height()) * self.scale)))
        painter.save()
        painter.setFont(font)
        painter.setPen(palette.color(group, _ink(state)))
        painter.drawText(rect, Qt.AlignCenter, chr(self.code))
        painter.restore()

    def pixmap(self, size: QSize, mode, state) -> QPixmap:
        out = QPixmap(size)
        out.fill(Qt.transparent)
        painter = QPainter(out)
        self.paint(painter, QRect(0, 0, size.width(), size.height()), mode, state)
        painter.end()
        return out

    def clone(self) -> QIconEngine:
        return _kept(_GlyphEngine(self.family, self.code, self.scale))


def _ink(state) -> QPalette.ColorRole:
    """The colour the word beside it is drawn in.

    Windows 11 fills a pressed toggle with the accent colour and draws its
    word in the window colour to stand out on it; elsewhere a pressed button
    keeps the ordinary button text.
    """
    from pycangui.ui import theme  # not at the top: theme is the heavier of the two

    pressed_on_accent = state == QIcon.On and theme.style_name() == theme.STYLE_NAMES["windows11"]
    return QPalette.Window if pressed_on_accent else QPalette.ButtonText


def _kept(engine: QIconEngine) -> QIconEngine:
    _ENGINES.append(engine)
    return engine


def _glyph(code: int, scale: float = 1.0) -> QIcon | None:
    family = glyph_font()
    return QIcon(_kept(_GlyphEngine(family, code, scale))) if family else None
