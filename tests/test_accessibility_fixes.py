# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""What the accessibility review of 10 October found to fix: buttons that are
a sign and not a word, buttons the keyboard could not reach, and plot text
that did not grow with the rest."""

import re

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QAbstractButton

from pycangui.core.signals import SignalHub
from pycangui.ui import fonts
from pycangui.ui.pane_bar import PaneBar
from pycangui.ui.plot_view import PlotView


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    from pycangui.ui.main_window import MainWindow

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    made = MainWindow()
    yield made
    made.close()


def test_a_button_that_shows_a_sign_has_a_name_in_words(window):
    """A screen reader says a button's text when it has no name of its own,
    and "plus" says nothing about what a button does."""
    signs = [
        b
        for b in window.findChildren(QAbstractButton)
        if b.text().strip() and not re.search(r"[A-Za-z0-9]", b.text())
    ]
    assert signs, "the channel's + and - buttons are among them"
    for button in signs:
        assert re.search(r"[A-Za-z]", button.accessibleName()), button.text()


def test_pin_and_detach_are_reached_with_the_keyboard(app):
    bar = PaneBar()
    for button in (bar.pin, bar.move_button):
        assert button.focusPolicy() & Qt.TabFocus, button.text()


def test_the_plot_legend_is_in_the_text_size_of_the_rest(app):
    view = PlotView(SignalHub(), lambda: 0.0)
    size = view.legend.opts["labelTextSize"]
    assert float(size.removesuffix("pt")) == pytest.approx(view.font().pointSizeF())


def test_the_time_window_box_is_wide_enough_for_its_longest_value(app):
    view = PlotView(SignalHub(), lambda: 0.0)
    box = view.window_s
    assert box.minimumWidth() >= fonts.width_for(box, "3600.00 s")
