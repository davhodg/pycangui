# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Whether pycangui's own traffic is paused, on the status bar, and the two
commands that go with it: pause it by hand, and stop everything that repeats.

On the status bar because it is about the whole tool, not one pane, and a
pause nobody can see is a pause somebody forgets and then wonders why the
polling stopped. The commands are on its right-click and in the Tools menu,
which is where the keyboard reaches them. See core/pause.py for what each
holds back or stops.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QFont
from PySide6.QtWidgets import QLabel, QMenu, QWidget

from pycangui.core.pause import BY_HAND, TrafficPause, described

PAUSE_TEXT = "Pause automatic traffic"
STOP_TEXT = "Stop all repeating traffic"
PAUSE_TIP = (
    "Hold back what pycangui sends by itself: polling in custom panes, plugins\n"
    "and XCP or CCP, CANopen SYNC and TIME, and CANopen's unasked reads of a\n"
    "node. All of it goes on again when the pause is lifted. What you do by\n"
    "hand still goes out, and so do UDS Tester Present and the J1939 DM13 hold.\n"
    "\n"
    "A flash pauses it by itself while it runs; this is for any other time."
)
HELD_BY_WORK_TIP = "Held by {who} until it has finished."
STOP_TIP = (
    "Stop everything that repeats, for good: cyclic messages, polling, CANopen\n"
    "SYNC and TIME, and what a plugin keeps doing. Nothing starts again until\n"
    "it is asked for. UDS Tester Present and the J1939 DM13 hold go on: they\n"
    "keep a session open and a network quiet, and are stopped where they are."
)
LABEL_TIP = "Right-click to pause pycangui's own traffic, or to stop all of it."


class PauseStatus(QLabel):
    """The status bar's word on it, and the actions the menus share."""

    def __init__(self, pause: TrafficPause, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pause = pause
        self.setAccessibleName("Automatic traffic")
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self.pause_action = QAction(PAUSE_TEXT, self)
        self.pause_action.setCheckable(True)
        self.pause_action.toggled.connect(pause.set_by_hand)
        self.stop_action = QAction(STOP_TEXT, self)
        self.stop_action.setToolTip(STOP_TIP)
        self.stop_action.triggered.connect(pause.stop_all)
        pause.holders_changed.connect(self._show)
        self._show()

    def _show(self) -> None:
        holders = self._pause.holders
        bold = QFont(self.font())
        bold.setBold(bool(holders))
        self.setFont(bold)
        # Said in words as well as in bold: the word is what reaches somebody
        # who cannot see the weight, or has the status bar read aloud.
        self.setText(
            f"Automatic traffic paused: {described(holders)}"
            if holders
            else "Automatic traffic running"
        )
        self.setToolTip(LABEL_TIP)
        self.pause_action.blockSignals(True)
        self.pause_action.setChecked(self._pause.by_hand)
        self.pause_action.blockSignals(False)
        # Not somebody's to lift while a flash holds it: the flash gives it
        # back when it is done, and lifting it half way through is what the
        # pause is there to prevent.
        work = [who for who in holders if who != BY_HAND]
        self.pause_action.setEnabled(not work)
        self.pause_action.setToolTip(
            HELD_BY_WORK_TIP.format(who=described(work)) if work else PAUSE_TIP
        )

    def menu(self) -> QMenu:
        """The right-click, built apart from being shown so it can be looked at."""
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        menu.addAction(self.pause_action)
        menu.addAction(self.stop_action)
        return menu

    def _menu(self, at) -> None:
        menu = self.menu()
        menu.exec(self.mapToGlobal(at))
        menu.deleteLater()
