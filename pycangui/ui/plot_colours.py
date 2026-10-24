# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""What colours a plot is drawn in: the theme's, or always light, or always dark.

A plot is a picture as much as a widget. Somebody working in the dark theme
wants it dark with the rest of the window, and somebody pasting it into a
report wants it white whatever the window is; so there are three choices,
kept on this computer like the theme itself, and every plot follows a change
at once.

Each scheme has its own curve colours, because a colour is only as readable
as what is behind it: an orange that stands out on near-black is all but
lost on white, and a brown that reads on white is lost on near-black. Every
curve colour has at least 3:1 contrast with its background, which is what a
line needs to be made out (a test holds them to it), and the hues come in
the same order in both, so the first signal plotted is blue either way.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QEvent, QObject, QSettings, Signal
from PySide6.QtWidgets import QWidget

from pycangui.ui.event_colours import dark_behind

SETTING = "appearance/plot_colours"
THEME, LIGHT, DARK = "theme", "light", "dark"
#: In the order the menu offers them.
CHOICES = ((THEME, "Match the theme"), (LIGHT, "Always light"), (DARK, "Always dark"))
TIP = "The colours of every plot. Kept for you on this computer."


@dataclass(frozen=True)
class Scheme:
    background: str
    #: Axes, their numbers and labels, the legend's words, and the grid,
    #: which is this drawn faint.
    foreground: str
    curves: tuple[str, ...]
    cursor: str
    cursor_hover: str
    cursor_label: str
    cursor_label_fill: str

    def curve(self, index: int) -> str:
        return self.curves[index % len(self.curves)]


LIGHT_SCHEME = Scheme(
    background="#ffffff",
    foreground="#000000",
    curves=(
        "#1f77b4",
        "#c45a00",
        "#2a8a2a",
        "#d62728",
        "#8a5cc0",
        "#8c564b",
        "#c03a9a",
        "#0e7f8c",
    ),
    cursor="#404040",
    cursor_hover="#000000",
    cursor_label="#202020",
    cursor_label_fill="#ffffffc0",
)
DARK_SCHEME = Scheme(
    background="#1e1e1e",
    foreground="#d8d8d8",
    curves=(
        "#5aa9e6",
        "#ff9e4a",
        "#5cc85c",
        "#ff6b6b",
        "#b99aea",
        "#d2a679",
        "#f08bd0",
        "#3dd6e0",
    ),
    cursor="#c8c8c8",
    cursor_hover="#ffffff",
    cursor_label="#e8e8e8",
    cursor_label_fill="#1e1e1ec0",
)


class _Changes(QObject):
    changed = Signal()


_changes: _Changes | None = None


def changes() -> _Changes:
    """What says the choice has changed, for every plot to follow."""
    global _changes
    if _changes is None:
        _changes = _Changes()
    return _changes


def chosen(settings: QSettings | None = None) -> str:
    """The choice remembered, or the theme's when there is none or it is not one we know."""
    value = str((settings or QSettings()).value(SETTING, THEME) or THEME)
    return value if value in dict(CHOICES) else THEME


def choose(choice: str, settings: QSettings | None = None) -> None:
    """Remember a choice, and have every plot follow it."""
    (settings or QSettings()).setValue(SETTING, choice if choice in dict(CHOICES) else THEME)
    changes().changed.emit()


def scheme_for(widget: QWidget, choice: str | None = None) -> Scheme:
    """The scheme for a plot in this widget: the theme's is the widget's own background's."""
    choice = chosen() if choice is None else choice
    if choice == THEME:
        return DARK_SCHEME if dark_behind(widget.palette()) else LIGHT_SCHEME
    return DARK_SCHEME if choice == DARK else LIGHT_SCHEME


def contrast(a: str, b: str) -> float:
    """How far apart two colours are to the eye, from 1 to 21, as accessibility
    guidance measures it."""

    def lightness(colour: str) -> float:
        channels = [int(colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]
        r, g, b = (c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    high, low = sorted((lightness(a), lightness(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def paint(plot, scheme: Scheme) -> None:
    """Background, axes and legend of a pyqtgraph PlotWidget; its curves are the caller's."""
    plot.setBackground(scheme.background)
    item = plot.getPlotItem()
    for side in ("left", "bottom", "right", "top"):
        axis = item.getAxis(side)
        axis.setPen(scheme.foreground)
        axis.setTextPen(scheme.foreground)
    legend = item.legend
    if legend is not None:
        legend.setLabelTextColor(scheme.foreground)
        # Words already in the legend keep their colour until written again.
        for _sample, label in legend.items:
            label.setText(label.text)


class Follower(QObject):
    """Calls ``repaint`` when the choice changes, and when the theme does
    while the choice is to match it."""

    def __init__(self, widget: QWidget, repaint) -> None:
        super().__init__(widget)
        self._repaint = repaint
        widget.installEventFilter(self)
        # Through a slot of this object's, so the connection goes with it.
        changes().changed.connect(self._changed)

    def _changed(self) -> None:
        self._repaint()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.PaletteChange and chosen() == THEME:
            self._repaint()
        return False
