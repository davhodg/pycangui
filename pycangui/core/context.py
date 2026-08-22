"""The one object handed to user code (hooks and scripts).

Keep this small and stable: everything a user hook needs should be reachable
from ``ctx`` without importing Qt or python-can internals.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from pycangui.core import paths
from pycangui.core.settings import Settings


class Context:
    def __init__(self, log: Callable[[str], None] | None = None) -> None:
        self.user_dir: Path = paths.user_dir()
        self.hooks_dir: Path = paths.hooks_dir()
        self.eds_dir: Path = paths.eds_dir()
        self.settings = Settings(self.user_dir / "settings.json")
        self._log = log or print

    def log(self, message: str) -> None:
        """Write a line to the Log pane."""
        self._log(message)
