# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""A button in two parts: what is done every time, and what is chosen once.

Pressing it does the thing. The small arrow beside it opens a menu of what
goes with the thing and is rarely wanted -- the file a key comes from, beside
the button that unlocks. Two buttons for that pair spent a row's width on the
one nobody presses twice; one menu for both cost a click on the one pressed
all the time.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QPushButton, QToolButton


class SplitButton(QToolButton):
    """Pressed, it calls ``pressed``; its arrow opens the menu ``add`` fills."""

    def __init__(self, text: str, tip: str, pressed: Callable[[], None]) -> None:
        super().__init__()
        self.setText(text)
        self.setToolTip(tip)
        self.setPopupMode(QToolButton.MenuButtonPopup)
        self.clicked.connect(lambda _checked=False: pressed())
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        self.setMenu(menu)
        # As tall as an ordinary button, which it sits in a row with: a tool
        # button is drawn shorter.
        self.setMinimumHeight(QPushButton(text).sizeHint().height())

    def add(self, text: str, tip: str, chosen: Callable[[], None]) -> QAction:
        action = self.menu().addAction(text)
        action.setToolTip(tip)
        action.triggered.connect(lambda _checked=False: chosen())
        return action
