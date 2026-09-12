# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Render the application icon from its SVG sources.

    python build/icon.py

The SVGs in pycangui/resources are the source; pycangui.ico and pycangui.png
next to them are generated from them and committed, so neither a build nor a
running pycangui needs to rasterise anything.  Run this after editing either
SVG.

Two drawings rather than one scaled: a zigzag resistor stops being a zigzag
somewhere below 32 px and turns into a blur, so the small sizes are drawn with
the bar that blur was trying to be.  Each size is rendered straight from the
vector at that size -- not shrunk from a large bitmap -- so edges land on
whole pixels where the geometry allows.

The .ico holds PNG-compressed images, which Windows has read since Vista, so
it is written directly here rather than pulling in an imaging library for it.
"""

from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

RESOURCES = Path(__file__).resolve().parent.parent / "pycangui" / "resources"
LARGE = RESOURCES / "pycangui.svg"
SMALL = RESOURCES / "pycangui_small.svg"

#: Below this the small drawing is used.
SMALL_BELOW = 32

#: The sizes Windows asks an .ico for: 16 title bar and Explorer details, 20
#: and 24 the taskbar at 125% and 100%, 32 Alt+Tab, 40 and 48 the desktop,
#: 64 and 128 large views and high DPI, 256 extra large.
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)

#: The PNG Qt uses for the window icon everywhere an .ico is not the answer.
PNG_SIZE = 256


def render(svg: Path, size: int) -> bytes:
    """*svg* rasterised at *size* x *size*, as PNG bytes."""
    renderer = QSvgRenderer(str(svg))
    if not renderer.isValid():
        raise SystemExit(f"{svg} is not a valid SVG")
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    buffer.close()
    return bytes(data)


def source_for(size: int) -> Path:
    return SMALL if size < SMALL_BELOW else LARGE


def ico(images: dict[int, bytes]) -> bytes:
    """An .ico file holding one PNG per size.

    ICONDIR, then one 16-byte ICONDIRENTRY per image, then the images.  A
    width or height of 0 in an entry means 256.
    """
    header = struct.pack("<HHH", 0, 1, len(images))
    entries = b""
    body = b""
    offset = len(header) + 16 * len(images)
    for size, png in sorted(images.items()):
        side = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", side, side, 0, 0, 1, 32, len(png), offset + len(body))
        body += png
    return header + entries + body


def main() -> int:
    _app = QGuiApplication(sys.argv)
    images = {size: render(source_for(size), size) for size in ICO_SIZES}
    (RESOURCES / "pycangui.ico").write_bytes(ico(images))
    (RESOURCES / "pycangui.png").write_bytes(render(source_for(PNG_SIZE), PNG_SIZE))
    print(f"wrote pycangui.ico ({', '.join(map(str, ICO_SIZES))}) and pycangui.png ({PNG_SIZE})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
