# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Tools > Settings > Theme: light, dark, or whatever the system is set to."""

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

    def __init__(self, system):
        self.asked = []
        self.system = system
        self.scheme = system
        self.colorSchemeChanged = self  # connected to, and never emitted here

    def connect(self, _slot):
        pass

    def setColorScheme(self, scheme):
        self.asked.append(scheme)
        self.scheme = scheme

    def unsetColorScheme(self):
        self.asked.append("system")
        self.scheme = self.system

    def colorScheme(self):
        return self.scheme


class Style:
    def __init__(self, name):
        self._name = name

    def name(self):
        return self._name


class App:
    """A system set to light or dark, with the native style Qt chose for it."""

    def __init__(self, style="windows11", system=Qt.ColorScheme.Light):
        self.hints = Hints(system)
        self._style = Style(style)
        self._sheet = ""

    def styleSheet(self):
        return self._sheet

    def setStyleSheet(self, sheet):
        self._sheet = sheet

    def styleHints(self):
        return self.hints

    def style(self):
        return self._style

    def setStyle(self, name):
        self._style = Style(name.lower())


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


# --- a native style that cannot draw dark --------------------------------------------------
@pytest.mark.parametrize("choice", [theme.SYSTEM, theme.LIGHT, theme.DARK])
def test_windows_10_uses_fusion_whatever_is_chosen(choice):
    """Reported: on Windows 10 Qt's native style stays light whatever is asked
    for, so choosing Dark did nothing. Fusion for light as well as dark, so that
    switching changes colours and never the controls' shapes."""
    app = App(style="windowsvista")
    theme.apply(choice, app)
    assert app.style().name() == "fusion"


def test_switching_on_windows_10_changes_colours_not_the_style():
    app = App(style="windowsvista")
    theme.apply(theme.DARK, app)
    theme.apply(theme.LIGHT, app)
    assert app.style().name() == "fusion"
    assert app.hints.asked[-1] == Qt.ColorScheme.Light


def test_unticked_boxes_are_outlined_where_the_dark_style_hides_them():
    """Reported on Windows 10 in the dark: a tick box in a list could not be
    seen until it was ticked. Fusion draws it a shade darker than what is
    behind it."""
    app = App(style="windowsvista")
    theme.apply(theme.DARK, app)
    assert app.styleSheet() == theme.TICK_BOX_OUTLINE

    theme.apply(theme.LIGHT, app)
    assert app.styleSheet() == "", "in the light it is drawn well enough"
    assert app.style().name() == "fusion", "and the style is still the one it was"

    theme.apply(theme.DARK, app)
    assert app.styleSheet() == theme.TICK_BOX_OUTLINE


def test_a_style_that_draws_them_itself_is_left_alone():
    app = App(style="windows11", system=Qt.ColorScheme.Dark)
    theme.apply(theme.DARK, app)
    assert app.styleSheet() == ""


@pytest.mark.parametrize("choice", [theme.SYSTEM, theme.LIGHT, theme.DARK])
def test_a_native_style_that_can_be_dark_is_kept(choice):
    app = App(style="windows11")
    theme.apply(choice, app)
    assert app.style().name() == "windows11"


def test_a_value_it_does_not_know_is_the_system(settings):
    settings.setValue(theme.SETTING, "sepia")
    assert theme.chosen(settings) == theme.SYSTEM


def test_the_menu_offers_three_with_the_current_one_ticked(app, settings):
    settings.setValue(theme.SETTING, theme.LIGHT)
    menu = theme.menu(None, settings)
    actions = [a for a in menu.actions() if a.isCheckable()]
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


# --- the style in use ---------------------------------------------------------------------
@pytest.mark.parametrize(
    ("native", "shown"),
    [("windows11", "Windows 11"), ("windowsvista", "Fusion"), ("macos", "macOS")],
)
def test_the_style_in_use_is_named(native, shown):
    """Windows 10's native style is replaced, so it is Fusion that is named."""
    app = App(style=native)
    theme.apply(theme.SYSTEM, app)
    assert theme.style_name(app) == shown


def test_a_style_it_does_not_know_is_named_as_qt_names_it():
    app = App(style="breeze")
    theme.apply(theme.SYSTEM, app)
    assert theme.style_name(app) == "breeze"


def test_the_style_is_the_one_noted_once_a_style_sheet_is_set():
    """With a style sheet set the application's style is no longer the one to ask."""
    app = App(style="windowsvista", system=Qt.ColorScheme.Dark)
    theme.apply(theme.DARK, app)
    assert app.styleSheet()
    app._style = Style("stylesheetstyle")
    assert theme.style_name(app) == "Fusion"


def test_matching_the_system_says_what_the_system_gave(settings):
    dark, light = App(system=Qt.ColorScheme.Dark), App(system=Qt.ColorScheme.Light)
    assert theme.describe(settings, dark) != theme.describe(settings, light)
    settings.setValue(theme.SETTING, theme.DARK)
    assert theme.describe(settings, dark) == theme.describe(settings, light)


def test_the_menu_shows_the_style_greyed_out_below_the_choices(app, settings):
    menu = theme.menu(None, settings)
    style = menu.style_action
    assert menu.actions()[-1] is style
    assert not style.isEnabled() and not style.isCheckable()
    assert theme.style_name() in style.text()
    assert theme.style_name() in menu.menuAction().toolTip(), "reachable without a mouse"


def test_it_is_under_tools_settings(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    from pycangui.ui.main_window import MainWindow

    window = MainWindow()
    tools = next(a.menu() for a in window.menuBar().actions() if a.text() == "&Tools")
    assert window.settings_menu.menuAction() in tools.actions()
    assert window.theme_menu.menuAction() in window.settings_menu.actions()
    window.close()


# --- cells typed into ---------------------------------------------------------------------
@pytest.mark.parametrize("view", ["tree", "table"])
def test_a_cell_typed_into_hides_the_value_it_is_over(app, view):
    """Reported: in the Windows 11 style the old value showed through under
    the new one, since the box typed into painted no background of its own."""
    from PySide6.QtWidgets import (
        QLineEdit,
        QTableWidget,
        QTableWidgetItem,
        QTreeWidget,
        QTreeWidgetItem,
    )

    theme.apply(theme.SYSTEM, App())
    if view == "tree":
        widget = QTreeWidget()
        item = QTreeWidgetItem(["old value"])
        item.setFlags(item.flags() | Qt.ItemIsEditable)
        widget.addTopLevelItem(item)
        widget.show()
        widget.editItem(item, 0)
    else:
        widget = QTableWidget(1, 1)
        item = QTableWidgetItem("old value")
        widget.setItem(0, 0, item)
        widget.show()
        widget.editItem(item)
    editor = widget.findChild(QLineEdit)
    assert editor is not None and editor.autoFillBackground()
    widget.close()
