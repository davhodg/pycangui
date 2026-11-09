# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Icons beside the words: shipped with pycangui, and drawn in the text colour."""

from __future__ import annotations

import pytest
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPalette
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QPushButton

from pycangui import resources
from pycangui.ui import icons

SIZE = 20


def drawn_colours(icon: QIcon, mode=QIcon.Normal) -> set[str]:
    """The colours of the pixels the icon covers fully."""
    image = icon.pixmap(SIZE, SIZE, mode).toImage()
    return {
        image.pixelColor(x, y).name()
        for x in range(image.width())
        for y in range(image.height())
        if image.pixelColor(x, y).alpha() == 255
    }


def test_every_name_has_a_picture_that_loads():
    for name in icons.FILE:
        assert QSvgRenderer(str(icons.path(name))).isValid(), name


def test_every_picture_shipped_is_used():
    shipped = {file.stem for file in resources.path("icons").glob("*.svg")}
    assert shipped == set(icons.FILE.values())


def test_a_button_given_one_has_it(app, monkeypatch):
    monkeypatch.setattr(icons, "_shown", True)
    for name in icons.FILE:
        assert not icons.beside(QPushButton(), name).icon().isNull(), name


def test_turned_off_a_button_keeps_the_word_alone(app, monkeypatch):
    monkeypatch.setattr(icons, "_shown", False)
    button = icons.beside(QPushButton("Clear"), icons.CLEAR)
    assert button.icon().isNull()
    assert button.property(icons.PROPERTY) == icons.CLEAR


def test_it_is_drawn_in_the_text_colour_not_the_files(app):
    text = QGuiApplication.palette().color(QPalette.Active, QPalette.ButtonText).name()
    assert drawn_colours(icons.icon(icons.STOP)) == {text}


def test_disabled_it_is_drawn_in_the_disabled_text_colour(app, monkeypatch):
    palette = QPalette(QGuiApplication.palette())
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor("#ff0000"))
    monkeypatch.setattr(QGuiApplication, "palette", staticmethod(lambda: palette))
    assert drawn_colours(icons.icon(icons.STOP), QIcon.Disabled) == {"#ff0000"}


def test_a_new_palette_recolours_an_icon_already_made(app, monkeypatch):
    made = icons.icon(icons.STOP)
    palette = QPalette(QGuiApplication.palette())
    palette.setColor(QPalette.Active, QPalette.ButtonText, QColor("#00ff00"))
    monkeypatch.setattr(QGuiApplication, "palette", staticmethod(lambda: palette))
    assert drawn_colours(made) == {"#00ff00"}


def test_no_menu_has_both_ticks_and_icons(app, tmp_path, monkeypatch):
    """Qt gives a menu with tick items a column for the ticks, and one with
    icons a column for those: a menu with both has two, side by side, with
    most rows empty in one of them. A menu of ticks gets no icons."""
    from PySide6.QtWidgets import QMenu

    from pycangui.ui.main_window import MainWindow

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    monkeypatch.setattr(icons, "_shown", True)
    window = MainWindow()
    try:
        for _ in range(2):  # a menu filled as it opens can add submenus
            for menu in window.findChildren(QMenu):
                menu.aboutToShow.emit()
        mixed = []
        for menu in window.findChildren(QMenu):
            shown = [a for a in menu.actions() if not a.isSeparator() and a.isVisible()]
            if any(a.isCheckable() for a in shown) and any(not a.icon().isNull() for a in shown):
                mixed.append(menu.title() or type(menu.parent()).__name__)
        assert mixed == []
    finally:
        window.close()


@pytest.mark.parametrize("on", [True, False])
def test_qts_own_text_menu_has_these_icons_or_none(app, monkeypatch, on):
    """Qt draws Undo, Cut, Copy, Paste, Delete and Select All from the
    system's icons, which matched nothing else here."""
    from PySide6.QtWidgets import QLineEdit

    monkeypatch.setattr(icons, "_shown", on)
    icons.on_standard_menus(app)
    box = QLineEdit("text")
    menu = box.createStandardContextMenu()
    menu.show()
    standard = [a for a in menu.actions() if a.objectName() in icons.STANDARD_MENU]
    assert len(standard) == len(icons.STANDARD_MENU)
    for action in standard:
        assert action.property(icons.PROPERTY) == icons.STANDARD_MENU[action.objectName()]
        assert action.icon().isNull() is not on
    menu.close()
