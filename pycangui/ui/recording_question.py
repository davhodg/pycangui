# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Disconnecting the last channel while recording: ask first.

Disconnect sits beside Record, and pressing one for the other is easy. On its
own a disconnect leaves the recording running with nothing to record, which
is right when the channel is about to be connected again -- at a new bitrate,
say -- and wrong when it was meant to end the capture. Only the person
pressing it knows which, so it is asked, with Cancel the default: a slip of
the mouse followed by Enter changes nothing.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QMessageBox, QWidget

from pycangui.ui import icons

STOP = "stop"
KEEP = "keep"
CANCEL = "cancel"

TITLE = "Disconnect while recording?"
TEXT = (
    "Recording to {name}. With no channel connected, nothing more is recorded "
    "until one is connected again, and then the recording goes on in the same file."
)


def ask(parent: QWidget | None, path: Path | str | None) -> str:
    """STOP, KEEP or CANCEL."""
    box = QMessageBox(QMessageBox.Question, TITLE, TITLE, QMessageBox.NoButton, parent)
    box.setInformativeText(TEXT.format(name=Path(path).name if path else "a file"))
    stop = icons.beside(
        box.addButton("Disconnect and stop recording", QMessageBox.AcceptRole), icons.STOP
    )
    keep = icons.beside(
        box.addButton("Disconnect, keep recording", QMessageBox.AcceptRole), icons.DISCONNECT
    )
    cancel = box.addButton(QMessageBox.Cancel)
    box.setDefaultButton(cancel)
    box.setEscapeButton(cancel)
    icons.on_buttons(box).exec()
    pressed = box.clickedButton()
    if pressed is stop:
        return STOP
    if pressed is keep:
        return KEEP
    return CANCEL
