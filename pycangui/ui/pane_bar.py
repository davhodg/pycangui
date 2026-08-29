"""A strip of buttons along the top of a pane that is out of the window.

The options only apply to a pane that is out, so they only appear then: while
a pane is docked it keeps Qt's own title bar, and with it the drag that
undocks it.  Replacing that permanently would mean re-implementing dragging,
which is not a trade worth making for two buttons.

Once the pane is floating it has a frame of its own -- the window manager's --
so this strip does not have to be a drag handle.  It sits under the frame with
the buttons at the right, where a window's buttons are.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QToolButton, QWidget

BAR_HEIGHT = 22


def _button(text: str, tip: str, checkable: bool = False) -> QToolButton:
    button = QToolButton()
    button.setText(text)
    button.setToolTip(tip)
    button.setCheckable(checkable)
    button.setAutoRaise(True)  # flat until the mouse is over it
    button.setFocusPolicy(Qt.NoFocus)
    return button


class PaneBar(QWidget):
    """Pin, detach and put-back, for a pane that is not in the main window."""

    pinned = Signal(bool)
    detach_requested = Signal()
    dock_requested = Signal()

    def __init__(self, detachable: bool = True, on_top: bool = False) -> None:
        super().__init__()
        self.setFixedHeight(BAR_HEIGHT)

        self.pin = _button("Pin", "Keep this pane above other windows", checkable=True)
        self.pin.setChecked(on_top)
        self.pin.toggled.connect(self.pinned)

        # Words rather than a glyph: there is no icon anybody recognises for
        # either of these, and a mystery button is worse than a small label.
        self.detach = _button(
            "Detach",
            "Give this pane a window of its own, with a taskbar entry, that "
            "nothing will try to dock",
        )
        self.detach.clicked.connect(self.detach_requested)
        self.detach.setVisible(detachable)

        self.dock = _button("Dock", "Put this pane back in the main window")
        self.dock.clicked.connect(self.dock_requested)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(2)
        layout.addStretch()
        for button in (self.pin, self.detach, self.dock):
            layout.addWidget(button)

    def set_pinned(self, on: bool) -> None:
        self.pin.setChecked(on)
