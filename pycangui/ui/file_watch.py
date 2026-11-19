# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Notice when a file on disk has been changed, and say so once per change.

The operating system is asked to report changes (``QFileSystemWatcher``), so
nothing is spent while nothing happens. Three things make that less simple
than it sounds, and are handled here:

- A build tool or an editor often saves by writing a new file and renaming it
  over the old one, after which the watcher has lost the file. The folder is
  watched as well, and the file is watched again after every change.
- A file is written in several bursts. Nothing is said until it has been
  quiet for ``SETTLE_MS``, so the question does not come half way through.
- Network drives and some synced folders report nothing. Every ``POLL_MS`` the
  file's size and time are looked at too: one call to the file system.

A change is only a change if the contents are different: a tool that writes
the same bytes again is not worth a question. The contents are hashed when the
change has settled, which for a large A2L is some tens of milliseconds.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer, Signal

#: How long a file has to be left alone before it is taken to be written.
SETTLE_MS = 1000
#: How often the file is looked at, for a drive that reports no changes.
POLL_MS = 5000


def digest(path: Path) -> str | None:
    """The file's contents, hashed; None for a file that cannot be read now."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _stamp(path: Path) -> tuple[int, int, int] | None:
    """The file's size, time and identity.

    The identity (the inode, the file ID on Windows) because a file renamed
    over the old one can have the same size and, on Windows, whose clock for
    file times moves only every few milliseconds, the same time as well: it
    is still another file. A drive that has no identities gives 0, and size
    and time are all there is to go on.
    """
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_size, stat.st_mtime_ns, stat.st_ino


class FileWatch(QObject):
    """Watches one file. ``changed`` once for each new version of its contents."""

    #: The file now holds something other than it did: its path.
    changed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(lambda _p: self._settle.start())
        self._watcher.directoryChanged.connect(lambda _p: self._look())
        self._settle = QTimer(self, singleShot=True, interval=SETTLE_MS)
        self._settle.timeout.connect(self._settled)
        self._poll = QTimer(self, interval=POLL_MS)
        self._poll.timeout.connect(self._look)
        self.path: Path | None = None
        #: The contents last seen, or last said to have changed.
        self._known: str | None = None
        self._stamp: tuple[int, int] | None = None

    def watch(self, path: str | Path | None) -> None:
        """Watch ``path`` from what it holds now; None to watch nothing."""
        self.stop()
        if path is None:
            return
        self.path = Path(path)
        self._known = digest(self.path)
        self._stamp = _stamp(self.path)
        self._rewatch()
        self._poll.start()

    def stop(self) -> None:
        self._settle.stop()
        self._poll.stop()
        watched = self._watcher.files() + self._watcher.directories()
        if watched:
            self._watcher.removePaths(watched)
        self.path = None

    def _rewatch(self) -> None:
        """Watch the file again -- it may be a new file by the same name -- and its folder."""
        if self.path is None:
            return
        for path in (self.path, self.path.parent):
            if path.exists() and str(path) not in (
                self._watcher.files() + self._watcher.directories()
            ):
                self._watcher.addPath(str(path))

    def _look(self) -> None:
        """Its size or time is different: wait for it to settle."""
        if self.path is not None and _stamp(self.path) != self._stamp:
            self._settle.start()

    def _settled(self) -> None:
        if self.path is None:
            return
        self._rewatch()
        stamp = _stamp(self.path)
        if stamp is None:
            return  # gone for now: being replaced, or moved away
        if stamp == self._stamp:
            return
        self._stamp = stamp
        now = digest(self.path)
        if now is None or now == self._known:
            return  # the same bytes written again
        self._known = now
        self.changed.emit(str(self.path))
