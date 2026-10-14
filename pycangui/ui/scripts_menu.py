# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""File > Run script... and File > Recent scripts.

A script was run from a button in the Python Console, which is a pane that
has to be opened before the button can be found, and it was forgotten as
soon as it had run. Running the same test script again is the usual case,
so the last few are kept, for each workspace, a click away.

The running is the console's: a script sees the names the console has, and
what it prints and any traceback is written there, which is why the console
is shown first. A script whose output went nowhere would look like a menu
entry that does nothing.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QMenu, QWidget

from pycangui.core import workspace_files
from pycangui.ui import folders, messages

if TYPE_CHECKING:
    from pycangui.core.context import Context

#: In the workspace's settings: the scripts run, most recent first.
RECENT_KEY = "scripts.recent"
#: How many are kept.
MOST_RECENT = 5
RUN_TIP = (
    "Run a Python file in the Python Console, which is shown so that what it\n"
    "prints can be read: it sees the names the console has -- ctx, bus,\n"
    "channels, canopen, uds, j1939, xcp, window, send and wait."
)
RECENT_TIP = "The last scripts run in this workspace, newest first."
GONE = "{name} is not there any more, so it has been taken off the list:\n\n{path}"


class ScriptsMenu(QObject):
    """The two entries, and the list behind the second."""

    #: Run this file: the window shows the console and runs it there.
    run_wanted = Signal(str)

    def __init__(self, window: QWidget, ctx: Context) -> None:
        super().__init__(window)
        self.window = window
        self.ctx = ctx
        self.recent_menu = QMenu("Recent scripts")
        self.recent_menu.setToolTipsVisible(True)
        self.recent_menu.aboutToShow.connect(self._build_recent)

    def install(self, file_menu: QMenu) -> None:
        run = file_menu.addAction("Run script...", self.run_dialog)
        run.setToolTip(RUN_TIP)
        recent = file_menu.addMenu(self.recent_menu)
        recent.setToolTip(RECENT_TIP)

    # --- the list -------------------------------------------------------------------
    def recent(self) -> list[Path]:
        """The scripts run in this workspace, newest first."""
        workspace = self.ctx.workspace_dir
        values = self.ctx.settings.get(RECENT_KEY, [])
        if not isinstance(values, list):
            return []
        return [workspace_files.resolve(str(value), workspace) for value in values]

    def remember(self, path: str | Path) -> None:
        """Put a script at the top of the list: run again, it moves up rather
        than being listed twice. Kept relative to the workspace where it is
        in it, so a workspace taken elsewhere still finds its own."""
        workspace = self.ctx.workspace_dir
        mine = Path(path).resolve()
        kept = [p for p in self.recent() if p.resolve() != mine]
        values = [workspace_files.stored(p, workspace) for p in [mine, *kept][:MOST_RECENT]]
        self.ctx.settings.set(RECENT_KEY, values)

    def forget(self, path: Path) -> None:
        mine = path.resolve()
        workspace = self.ctx.workspace_dir
        values = [
            workspace_files.stored(p, workspace) for p in self.recent() if p.resolve() != mine
        ]
        self.ctx.settings.set(RECENT_KEY, values)

    def _build_recent(self) -> None:
        self.recent_menu.clear()
        scripts = self.recent()
        for path in scripts:
            action = self.recent_menu.addAction(path.name, lambda p=path: self.run_recent(p))
            action.setToolTip(str(path))
        if not scripts:
            nothing = self.recent_menu.addAction("No scripts run yet")
            nothing.setEnabled(False)

    # --- running --------------------------------------------------------------------
    def run_dialog(self) -> None:
        path = folders.open_file(
            self.window,
            self.ctx,
            folders.SCRIPT,
            "Run Python script",
            "Python (*.py)",
            self.ctx.user_dir,
        )
        if path:
            self.run_wanted.emit(path)

    def run_recent(self, path: Path) -> None:
        if not path.is_file():
            # Said in a box: chosen from a menu, an answer in the Event Log
            # is one in a pane that may not be open.
            self.forget(path)
            messages.warning(
                self.window, "Script not found", GONE.format(name=path.name, path=path)
            )
            return
        self.run_wanted.emit(str(path))
