# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""The one object handed to user code (hooks and scripts).

Keep this small and stable: everything a user hook needs should be reachable
from ``ctx`` without importing Qt or python-can internals.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from pycangui.core import paths, timing, workspaces
from pycangui.core.events import ERROR, GOOD, INFORMATION, WARNING, EventLog
from pycangui.core.layout import Layout
from pycangui.core.settings import Settings


class Context:
    def __init__(
        self,
        log: Callable[[str], None] | None = None,
        events: EventLog | None = None,
    ) -> None:
        # Marked in three because the whole of it is folders being made and
        # two files being read, which costs nothing on a local disc and a
        # surprising amount on a profile that is redirected to a network
        # share or watched by a scanner.
        self.user_dir: Path = paths.user_dir()
        #: Your own components are about this machine's ability to talk to a
        #: bus at all, so every workspace shares them rather than owning one.
        self.components_dir: Path = paths.components_dir()
        timing.mark("user folder")
        #: Which product is being worked on. Everything below belongs to it.
        self.workspace: str = workspaces.active()
        self.workspace_dir: Path = workspaces.active_dir()
        self.hooks_dir: Path = workspaces.hooks_dir()
        self.nodes_dir: Path = workspaces.nodes_dir()
        self.eds_dir: Path = workspaces.eds_dir()
        timing.mark("workspace folders")
        self.settings = Settings(workspaces.settings_path())
        #: The dock arrangement, in the workspace folder rather than in
        #: QSettings, so that the workspace is one thing that can be copied.
        self.layout = Layout(workspaces.layout_path())
        timing.mark("settings and layout files")
        #: The protocol managers and the channels, attached by the main
        #: window once they exist -- the same objects the Python Console
        #: has under the same names, and the ones the shipped hook files
        #: use in their examples. None until then, and in a script or a
        #: test with no window.
        self.bus = None
        self.channels = None
        self.canopen = None
        self.uds = None
        self.j1939 = None
        self.xcp = None
        #: Everything said to the user goes through here, with a level.
        self.events = events if events is not None else EventLog()
        if events is None:
            # A plain one-argument sink, which is what a script or a test
            # wants: the level is the window's business, not theirs.
            sink = log or print
            self.events.posted.connect(lambda message, _level: sink(message))

    def log(self, message: str, level: str = INFORMATION) -> None:
        """Write a line to the Event Log pane."""
        self.events.post(message, level)

    def warn(self, message: str) -> None:
        """Say that what was asked for did not happen.

        A warning opens the Event Log if it has been closed, so use it for
        something the user is waiting on an answer to and would otherwise be
        left wondering about.
        """
        self.events.post(message, WARNING)

    def error(self, message: str) -> None:
        """Say that something went wrong that nobody asked for."""
        self.events.post(message, ERROR)

    def good(self, message: str) -> None:
        """Say that something that was wrong is right again."""
        self.events.post(message, GOOD)
