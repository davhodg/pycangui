# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""A strip of buttons along the top of a pane that is out of the window.

Two buttons, each saying what pressing it will do rather than what is
currently true: Pin becomes Unpin, Detach becomes Attach.  A button that
names its own effect needs no reading twice.

They live at the top of the pane's own content rather than in a title bar.
Giving a dock a custom title bar makes Qt float it frameless, and that costs
it the native frame along with the move, resize and close that come with it.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QToolButton, QWidget

BAR_HEIGHT = 22

PIN_TIP = (
    "Keep this pane above every other window, pycangui's and everyone else's,\n"
    "so it stays readable while you work in something in front of it."
)
DETACH_TIP = (
    "Give this pane a window of its own, with an entry in the taskbar.\n"
    "Nothing will try to dock it, so it can be dropped on another display\n"
    "and left there."
)
ATTACH_TIP = "Put this pane back where it came from, in the main window."


def _button(text: str, tip: str, checkable: bool = False) -> QToolButton:
    button = QToolButton()
    button.setText(text)
    button.setToolTip(tip)
    button.setCheckable(checkable)
    button.setAutoRaise(True)  # flat until the mouse is over it
    button.setFocusPolicy(Qt.NoFocus)
    return button


class PaneBar(QWidget):
    """Pin and detach, for a pane that is not in the main window."""

    pinned = Signal(bool)
    detach_requested = Signal()
    attach_requested = Signal()

    def __init__(self, on_top: bool = False) -> None:
        super().__init__()
        self.setFixedHeight(BAR_HEIGHT)

        # Words rather than a glyph: there is no icon anybody recognises for
        # either of these, and a mystery button is worse than a small label.
        self.pin = _button("Pin", PIN_TIP, checkable=True)
        self.pin.toggled.connect(self._on_pinned)

        self.move_button = _button("Detach", DETACH_TIP)
        self.move_button.clicked.connect(self._on_move)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(2)
        layout.addStretch()
        layout.addWidget(self.pin)
        layout.addWidget(self.move_button)

        self._detached = False
        self.set_pinned(on_top)

    # --- state ------------------------------------------------------------------------
    def _on_pinned(self, on: bool) -> None:
        self.pin.setText("Unpin" if on else "Pin")
        self.pinned.emit(on)

    def _on_move(self) -> None:
        (self.attach_requested if self._detached else self.detach_requested).emit()

    def set_pinned(self, on: bool) -> None:
        self.pin.setChecked(on)
        self.pin.setText("Unpin" if on else "Pin")

    def set_detached(self, detached: bool) -> None:
        """Detach when it is not, Attach when it is: one button, one meaning."""
        self._detached = detached
        self.move_button.setText("Attach" if detached else "Detach")
        self.move_button.setToolTip(ATTACH_TIP if detached else DETACH_TIP)
