# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""One window at a time, replaced when the workspace changes.

Switching workspace is a full reload. The settings, the hooks, the EDS
bindings and the dock layout all come from the workspace and are read once,
while the window is being built, so the honest way to open another one is to
build another window rather than to talk the running one out of everything it
already read.

A window cannot do that to itself -- it would be closing the object running
the code -- so something outside it holds the pair: close the old one, move
the pointer, open the next. The order matters. Closing first means the
window writes its layout and its settings into the workspace it was actually
in, rather than into the one being opened.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QMainWindow

from pycangui.core import workspaces


class Session(QObject):
    """Whatever window is currently open, and how to replace it."""

    def __init__(self, build: Callable[[], QMainWindow]) -> None:
        super().__init__()
        self._build = build
        self.window: QMainWindow | None = None

    def open(self) -> QMainWindow:
        window = self._build()
        window.reopen_requested.connect(self.reopen)
        window.show()
        self.window = window
        return window

    def reopen(self, name: str) -> QMainWindow:
        """Close what is open, move to that workspace, and open it again."""
        previous, self.window = self.window, None
        if previous is not None and not previous.close():
            # Somebody chose to stay -- edits on a custom pane they have not
            # saved -- so the window they are looking at is still the one.
            self.window = previous
            return previous
        if name and workspaces.exists(name):
            workspaces.set_active(name)
        return self.open()
