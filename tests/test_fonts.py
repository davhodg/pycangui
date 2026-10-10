# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Lists, boxes and title buttons follow the size of the application's text."""

import pytest
from PySide6.QtWidgets import QAbstractButton, QDockWidget, QLineEdit

from pycangui.ui import fonts
from pycangui.ui.panes import enlarge_title_buttons


@pytest.fixture
def larger_text(app):
    """The application's text half as big again, and put back afterwards."""
    before = app.font()
    bigger = app.font()
    bigger.setPointSizeF(before.pointSizeF() * 1.5)
    app.setFont(bigger)
    yield before.pointSizeF()
    app.setFont(before)


def test_the_fixed_pitch_font_is_the_size_of_the_text_around_it(app):
    assert fonts.mono().pointSizeF() == pytest.approx(app.font().pointSizeF())
    assert fonts.mono().fixedPitch()


def test_it_grows_when_the_text_does(app, larger_text):
    assert fonts.mono().pointSizeF() == pytest.approx(larger_text * 1.5)


def test_its_size_can_be_set_apart_from_the_text(app, monkeypatch):
    monkeypatch.setattr(fonts, "MONO_SIZE", 2.0)
    assert fonts.mono().pointSizeF() == pytest.approx(app.font().pointSizeF() * 2)


def test_a_box_is_as_wide_as_its_text_needs(app):
    box = QLineEdit()
    box.setFont(fonts.mono())
    eight = fonts.width_for(box, "1FFFFFFF")
    assert eight > fonts.width_for(box, "FF")

    bigger = fonts.mono()
    bigger.setPointSizeF(bigger.pointSizeF() * 2)
    box.setFont(bigger)
    assert fonts.width_for(box, "1FFFFFFF") > eight


def test_no_list_names_a_font_of_its_own():
    """Asked of the source, since it is the source that went wrong: a font and
    a size written out in a pane is one that does not follow the text."""
    from pathlib import Path

    import pycangui

    ui = Path(pycangui.__file__).parent / "ui"
    named = [
        path.name
        for path in ui.glob("*.py")
        if path.name != "fonts.py" and 'QFont("' in path.read_text(encoding="utf-8")
    ]
    assert named == []


def test_a_panes_title_buttons_are_as_tall_as_its_title(app):
    plain, enlarged = QDockWidget("As Qt makes it"), QDockWidget("Enlarged")
    enlarge_title_buttons(enlarged)

    def sizes(dock):
        return [
            button.style().pixelMetric(button.style().PixelMetric.PM_SmallIconSize, None, button)
            for button in dock.findChildren(QAbstractButton)
            if button.objectName().startswith("qt_dockwidget_")
        ]

    assert sizes(enlarged) and all(
        big > small for big, small in zip(sizes(enlarged), sizes(plain), strict=True)
    )
