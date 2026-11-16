# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Notice CAN databases changed on disk, and say together the ones that change together.

One ``FileWatch`` a database, so each is looked after the way the A2L is: the
folder watched too, a quiet second before anything is said, and only different
contents counted. What this adds is the gathering. A pull or a new release
often brings several databases at once, and one question about all of them is
better than a question a file.

A database copied into the workspace is watched through the file it was copied
from, since the copy only changes when it is copied again. And what pycangui
writes itself -- the database editor's Save as -- is not a change to ask about.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from pycangui.ui.file_watch import FileWatch

#: How long after one database has changed others are waited for, to be asked about with it.
GATHER_MS = 500


def _same(a: str | Path, b: str | Path) -> bool:
    try:
        return Path(a).resolve() == Path(b).resolve()
    except OSError:
        return str(a) == str(b)


class DbcWatch(QObject):
    """Watches the databases in use. ``changed`` with those whose files have new contents."""

    #: The databases in use -- as they are loaded, by path -- whose files have changed.
    changed = Signal(list)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        #: A database in use, and the watch on its file (or the file it came from).
        self._watches: dict[str, FileWatch] = {}
        self._pending: list[str] = []
        self._gather = QTimer(self, singleShot=True, interval=GATHER_MS)
        self._gather.timeout.connect(self._say)

    def watch(self, files: dict[str, Path]) -> None:
        """Watch these: each database in use, and the file to watch for it.

        One already watched through the same file is left as it is, so that a
        change seen but not yet said is not forgotten by watching again.
        """
        for used in list(self._watches):
            if used not in files:
                self._watches.pop(used).stop()
        for used, watched in files.items():
            watch = self._watches.get(used)
            if watch is None:
                watch = FileWatch(self)
                watch.changed.connect(lambda _path, u=used: self._noticed(u))
                self._watches[used] = watch
            if watch.path is None or not _same(watch.path, watched):
                watch.watch(watched)
        self._pending = [used for used in self._pending if used in self._watches]

    def watched(self) -> dict[str, Path | None]:
        """Each database in use, and the file watched for it."""
        return {used: watch.path for used, watch in self._watches.items()}

    def stop(self) -> None:
        self.watch({})

    def written(self, path: str) -> None:
        """pycangui has just written ``path``: what it holds now is what is known."""
        for watch in self._watches.values():
            if watch.path is not None and _same(watch.path, path):
                watch.watch(watch.path)

    def _noticed(self, used: str) -> None:
        if used not in self._pending:
            self._pending.append(used)
        self._gather.start()

    def _say(self) -> None:
        pending, self._pending = self._pending, []
        if pending:
            self.changed.emit(pending)
