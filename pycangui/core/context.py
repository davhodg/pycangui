"""The one object handed to user code (hooks and scripts).

Keep this small and stable: everything a user hook needs should be reachable
from ``ctx`` without importing Qt or python-can internals.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from pycangui.core import paths
from pycangui.core.events import ERROR, INFORMATION, WARNING, EventLog
from pycangui.core.settings import Settings


class Context:
    def __init__(
        self,
        log: Callable[[str], None] | None = None,
        events: EventLog | None = None,
    ) -> None:
        self.user_dir: Path = paths.user_dir()
        self.hooks_dir: Path = paths.hooks_dir()
        self.eds_dir: Path = paths.eds_dir()
        self.backends_dir: Path = paths.backends_dir()
        self.settings = Settings(self.user_dir / "settings.json")
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
