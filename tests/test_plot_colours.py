# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Plot colours -- the theme's, always light or always dark -- and the View button."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette

from pycangui.canopen.display import Display
from pycangui.core.signals import SignalHub
from pycangui.custom_panes.model import Field
from pycangui.ui import field_widgets, plot_colours
from pycangui.ui.plot_colours import DARK, DARK_SCHEME, LIGHT, LIGHT_SCHEME, THEME, contrast
from pycangui.ui.plot_view import PlotView


@pytest.fixture(autouse=True)
def back_to_the_theme():
    yield
    plot_colours.choose(THEME)


def background(plot_widget) -> str:
    return plot_widget.backgroundBrush().color().name()


def pen_of(curve) -> str:
    return curve.opts["pen"].color().name()


@pytest.mark.parametrize("scheme", [LIGHT_SCHEME, DARK_SCHEME], ids=["light", "dark"])
def test_every_colour_can_be_made_out_on_its_background(scheme):
    for colour in scheme.curves:
        assert contrast(colour, scheme.background) >= 3, colour
    assert contrast(scheme.cursor, scheme.background) >= 3
    assert contrast(scheme.foreground, scheme.background) >= 4.5, "the axes' numbers are text"
    assert len(set(scheme.curves)) == len(scheme.curves)


def test_contrast_is_measured_the_usual_way():
    assert contrast("#000000", "#ffffff") == pytest.approx(21)
    assert contrast("#777777", "#777777") == pytest.approx(1)


def plotted(*keys):
    hub = SignalHub()
    for key in keys:
        hub.push("g", key, 0.0, 1)
    plot = PlotView(hub, now=lambda: 1.0)
    for key in keys:
        plot.set_plotted(f"g/{key}", True)
    return plot


def test_always_dark_and_always_light_redraw_a_plot_already_open(app):
    plot = plotted("a", "b")
    plot.set_cursor_positions(0.2, 0.8)
    plot_colours.choose(DARK)
    assert background(plot.plot) == DARK_SCHEME.background
    assert pen_of(plot._curves["g/b"]) == DARK_SCHEME.curves[1], "keeps its place in the order"
    assert plot._lines[0].pen.color().name() == DARK_SCHEME.cursor
    assert plot.plot.getAxis("left").textPen().color().name() == DARK_SCHEME.foreground

    plot_colours.choose(LIGHT)
    assert background(plot.plot) == LIGHT_SCHEME.background
    assert pen_of(plot._curves["g/b"]) == LIGHT_SCHEME.curves[1]
    assert plot_colours.chosen() == LIGHT, "remembered"


def test_matching_the_theme_follows_it_when_it_changes(app):
    plot = plotted("a")
    plot_colours.choose(THEME)
    dark = QPalette(plot.palette())
    dark.setColor(QPalette.Base, QColor("#202020"))
    plot.setPalette(dark)
    assert background(plot.plot) == DARK_SCHEME.background
    light = QPalette(plot.palette())
    light.setColor(QPalette.Base, QColor("#ffffff"))
    plot.setPalette(light)
    assert background(plot.plot) == LIGHT_SCHEME.background

    plot_colours.choose(LIGHT)
    plot.setPalette(dark)
    assert background(plot.plot) == LIGHT_SCHEME.background, "not when it is always light"


def test_a_curve_plotted_later_takes_the_colours_in_use(app):
    plot = plotted("a")
    plot_colours.choose(DARK)
    plot.hub.push("g", "late", 0.0, 1)
    plot.set_plotted("g/late", True)
    assert pen_of(plot._curves["g/late"]) == DARK_SCHEME.curves[1]


def test_a_map_in_a_custom_pane_follows_the_same_choice(app):
    widget = field_widgets.build(Field(index=0x2100, kind="map"), Display())
    plot_colours.choose(DARK)
    assert background(widget.plot) == DARK_SCHEME.background
    assert pen_of(widget.curve) == DARK_SCHEME.curves[0]


def test_the_view_button_places_the_list_and_chooses_the_colours(app, tmp_path, monkeypatch):
    from pycangui.core.context import Context
    from pycangui.ui.scope_view import ScopeView

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    pane = ScopeView(SignalHub(), lambda: 0.0, Context(log=print), key="scope")
    assert pane.plot.view_button.menu() is pane.plot.view_menu

    below = next(a for a in pane.place_actions.actions() if a.data() is True)
    below.trigger()
    assert pane.list_below and pane.splitter.orientation() == Qt.Vertical
    pane.plot.view_menu.aboutToShow.emit()
    assert below.isChecked(), "the menu shows where it is"
    beside = next(a for a in pane.place_actions.actions() if a.data() is False)
    beside.trigger()
    assert not pane.list_below

    pane.set_list_below(True)  # from the list's right-click
    pane.plot.view_menu.aboutToShow.emit()
    assert below.isChecked() and not beside.isChecked()

    dark = next(a for a in pane.plot.colour_actions.actions() if a.data() == DARK)
    dark.trigger()
    assert plot_colours.chosen() == DARK
    assert background(pane.plot.plot) == DARK_SCHEME.background
