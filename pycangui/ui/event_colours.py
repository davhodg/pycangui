# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""What a line in the Event Log looks like, by how much it matters.

A log where every line looks the same is a log nobody reads: connecting a
channel, a plugin loading and an encoder fault all arrive in the same grey,
and the one that matters is found by reading all of them. Colour is what
makes a page of text skimmable, and there are only ever four things to say
-- this went wrong, this did not go as asked, this came back, and this is
just news.

Two sets of colours because a colour is only as readable as what is behind
it. A red dark enough to read on white disappears into a dark theme, and one
bright enough for a dark theme is glaring on white. Which set is in use is
decided from the widget's own background rather than from a setting, so a
theme changed underneath is followed without being told.
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette

from pycangui.core.events import ERROR, GOOD, WARNING

#: On a pale background: dark enough to read against white.
LIGHT = {
    ERROR: "#b3261e",
    WARNING: "#a05000",
    GOOD: "#1b6b2f",
}

#: On a dark one: light enough to read against near-black, and none of them
#: at full saturation, which reads as a screen fault rather than as text.
DARK = {
    ERROR: "#ff8a80",
    WARNING: "#e8b04b",
    GOOD: "#81c995",
}

#: An error is the one worth finding while scrolling past, so it is the only
#: one that is also heavier than the text around it. Bolding the warnings
#: too would leave the errors no way to stand out from them.
BOLD = (ERROR,)

#: Below this, a background counts as dark. Qt's own lightness, 0 to 255.
DARK_BELOW = 128


def dark_behind(palette: QPalette) -> bool:
    """Whether text on this palette is being read against a dark background."""
    return palette.base().color().lightness() < DARK_BELOW


def colour_for(level: str, dark: bool) -> QColor | None:
    """The colour for a level, or None to leave the text as the theme has it.

    Information is deliberately not coloured. A colour means something on a
    line that has something to say, and colouring the ordinary lines too
    would leave nothing for the others to stand out from.
    """
    name = (DARK if dark else LIGHT).get(level)
    return QColor(name) if name is not None else None


def bold_for(level: str) -> bool:
    return level in BOLD
