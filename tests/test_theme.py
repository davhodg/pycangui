# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Tools > Theme: light, dark, or whatever the system is set to."""

import pytest
from PySide6.QtCore import QSettings, Qt

from pycangui.ui import theme


@pytest.fixture
def settings(tmp_path):
    """Settings of their own, so no test touches the real user's."""
    return QSettings(str(tmp_path / "theme.ini"), QSettings.IniFormat)


class Hints:
    """Stands in for QStyleHints, recording what it was asked for. The
    offscreen platform the tests run on has no colour scheme to change."""

    def __init__(self):
        self.asked = []

    def setColorScheme(self, scheme):
        self.asked.append(scheme)

    def unsetColorScheme(self):
        self.asked.append("system")


class App:
    def __init__(self):
        self.hints = Hints()

    def styleHints(self):
        return self.hints


@pytest.mark.parametrize(
    ("choice", "asked"),
    [
        (theme.LIGHT, Qt.ColorScheme.Light),
        (theme.DARK, Qt.ColorScheme.Dark),
        (theme.SYSTEM, "system"),
    ],
)
def test_each_choice_asks_qt_for_its_scheme(choice, asked):
    app = App()
    theme.apply(choice, app)
    assert app.hints.asked == [asked]


def test_nothing_chosen_is_the_system(settings):
    assert theme.chosen(settings) == theme.SYSTEM


def test_a_choice_is_remembered(settings):
    theme.choose(theme.DARK, settings, App())
    assert theme.chosen(settings) == theme.DARK


def test_a_value_it_does_not_know_is_the_system(settings):
    settings.setValue(theme.SETTING, "sepia")
    assert theme.chosen(settings) == theme.SYSTEM


def test_the_menu_offers_three_with_the_current_one_ticked(app, settings):
    settings.setValue(theme.SETTING, theme.LIGHT)
    menu = theme.menu(None, settings)
    actions = menu.actions()
    assert [a.data() for a in actions] == [theme.SYSTEM, theme.LIGHT, theme.DARK]
    assert all(a.isCheckable() for a in actions)
    assert [a.isChecked() for a in actions] == [False, True, False]


def test_picking_one_applies_and_remembers_it(app, settings, monkeypatch):
    applied = []
    monkeypatch.setattr(theme, "apply", lambda choice, app=None: applied.append(choice))
    menu = theme.menu(None, settings)
    dark = next(a for a in menu.actions() if a.data() == theme.DARK)
    dark.trigger()
    assert applied == [theme.DARK]
    assert theme.chosen(settings) == theme.DARK
    assert dark.isChecked() and sum(a.isChecked() for a in menu.actions()) == 1


def test_it_is_under_tools(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    from pycangui.ui.main_window import MainWindow

    window = MainWindow()
    tools = next(a.menu() for a in window.menuBar().actions() if a.text() == "&Tools")
    assert window.theme_menu.menuAction() in tools.actions()
    window.close()
