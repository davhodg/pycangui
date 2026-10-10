# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""The fixed-pitch font, and how wide a box for so many characters is.

Identifiers, data bytes and addresses are read in columns, so the lists and
the boxes they are typed into use a fixed-pitch font. It was written out as
Consolas at 9 points wherever one was wanted, and that is two mistakes: 9 is
the size of the text around it only until somebody asks their system for
larger text, at which the labels grow and the data does not; and Consolas is
a Windows font, so everywhere else Qt picked whatever it liked.

A box for an identifier was likewise so many pixels wide, which is eight
characters at one size of text and five at another. Both are asked for here,
in terms of the text there is.
"""

from __future__ import annotations

from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import QApplication, QWidget

#: Tried in order. The first is what it has always been on Windows; the hint
#: finds a fixed-pitch font wherever none of them is installed.
FAMILIES = ["Consolas", "Menlo", "DejaVu Sans Mono", "Liberation Mono"]

#: The fixed-pitch font's size, as a share of the application's own text.
#: The one place to make the lists and boxes larger or smaller than the
#: words around them.
MONO_SIZE = 1.0

#: Either side of the text in a box: the frame, and room for the cursor.
BOX_PADDING = 16

#: How tall the float and close marks in a pane's title bar are, as a share
#: of the height of the title beside them.
TITLE_BUTTON_SIZE = 1.0


def mono() -> QFont:
    """The fixed-pitch font, at the size of the application's own text."""
    font = QFont()
    font.setFamilies(FAMILIES)
    font.setStyleHint(QFont.Monospace)
    font.setFixedPitch(True)
    around = QApplication.font()
    if around.pointSizeF() > 0:
        font.setPointSizeF(around.pointSizeF() * MONO_SIZE)
    elif around.pixelSize() > 0:
        font.setPixelSize(round(around.pixelSize() * MONO_SIZE))
    return font


def text_height() -> int:
    """How tall a line of the application's own text is, in pixels."""
    return QFontMetrics(QApplication.font()).height()


def width_with_arrows(widget: QWidget, text: str) -> int:
    """How wide a spin box or a drop-down has to be to show this much.

    The text, and then the arrows beside it, which are about as wide as
    the box is tall -- twice that, to be sure of a spin box's pair. What
    such a box says it needs is less than this in some styles once the
    text is large, and a row then gives it less than its own contents.
    """
    return width_for(widget, text) + 2 * widget.sizeHint().height()


def width_for(widget: QWidget, text: str) -> int:
    """How wide a box has to be to show this much, in the widget's own font.

    ``text`` is the longest thing it is meant to hold, as an example: eight
    digits for an identifier, two for an address.
    """
    return QFontMetrics(widget.font()).horizontalAdvance(text) + BOX_PADDING
