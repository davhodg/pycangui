# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Files given to a second pycangui, handed to the one already open.

Double-clicking a DCF starts pycangui with the file on its command line. With
one already open, a second copy would be a second notice, a second window on
the same workspace, and a second attempt at the adapters the first one holds.
So the new copy asks the open one to take the file, and goes.

A local socket (a named pipe on Windows), named for the user folder: two
people on one machine, or a test with a folder of its own, do not reach each
other's. Only the files cross it, and only to be opened.
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from pycangui.core import paths

#: How long to look for an open pycangui. It is on this machine, so an answer
#: is quick; one that does not come means there is nobody there.
CONNECT_MS = 500
#: How long the open one has to say it took the files. Longer, because it may
#: be busy on its GUI thread -- a blocking SDO read in the console, say.
ANSWER_MS = 5000
TOOK_THEM = b"ok\n"


def server_name() -> str:
    where = str(paths.user_dir())
    if sys.platform == "win32":
        where = where.lower()
    return "pycangui-" + hashlib.sha256(where.encode("utf-8")).hexdigest()[:16]


def hand_over(files: list[Path], name: str | None = None) -> bool:
    """Give the files to the pycangui already open. False if there is none,
    or it did not say it took them: then this one opens them itself."""
    socket = QLocalSocket()
    socket.connectToServer(name or server_name())
    if not socket.waitForConnected(CONNECT_MS):
        return False
    _let_it_come_forward()
    socket.write(json.dumps([str(f) for f in files]).encode("utf-8") + b"\n")
    socket.waitForBytesWritten(CONNECT_MS)
    answer = b""
    while not answer.endswith(b"\n") and socket.waitForReadyRead(ANSWER_MS):
        answer += socket.readAll().data()
    socket.disconnectFromServer()
    return answer == TOOK_THEM


def _let_it_come_forward() -> None:
    """Windows lets a program bring its window to the front only when it was
    the last one used, which the open pycangui is not: this one, just started
    by the double-click, is. Lend it that."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
    except (AttributeError, OSError):
        pass


class Listener(QObject):
    """Takes files from a pycangui started after this one.

    Started before the notice, so a file double-clicked while it is being
    read still comes here; files that arrive before there is a window wait
    for ``deliver``.
    """

    def __init__(self, name: str | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._name = name or server_name()
        self._server = QLocalServer(self)
        self._server.setSocketOptions(QLocalServer.UserAccessOption)
        self._server.newConnection.connect(self._accept)
        self._open: Callable[[list[Path]], None] | None = None
        self.waiting: list[Path] = []
        self.listening = self._listen()

    def _listen(self) -> bool:
        # Another pycangui answering keeps the name: this one is a second copy
        # started on purpose, and taking the name would leave the first deaf.
        probe = QLocalSocket()
        probe.connectToServer(self._name)
        if probe.waitForConnected(CONNECT_MS):
            probe.disconnectFromServer()
            return False
        if self._server.listen(self._name):
            return True
        # Nobody answered, so what holds the name is left over from a copy
        # that did not close cleanly (a socket file, off Windows).
        QLocalServer.removeServer(self._name)
        return self._server.listen(self._name)

    def deliver(self, open_files: Callable[[list[Path]], None]) -> None:
        """Where files go from now on, starting with any that have waited."""
        self._open = open_files
        waiting, self.waiting = self.waiting, []
        if waiting:
            open_files(waiting)

    def close(self) -> None:
        self._server.close()

    def _accept(self) -> None:
        # Each socket is the server's child and goes with it: not deleteLater
        # on disconnecting, which on Windows aborts the process when the
        # listener has gone first. A few bytes a double-click.
        while (socket := self._server.nextPendingConnection()) is not None:
            socket.readyRead.connect(lambda s=socket: self._read(s))
            # What came before readyRead was connected will not announce itself.
            self._read(socket)

    def _read(self, socket: QLocalSocket) -> None:
        if not socket.canReadLine():
            return
        try:
            names = json.loads(socket.readLine().data().decode("utf-8"))
        except ValueError:
            names = None
        if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
            socket.disconnectFromServer()
            return
        socket.write(TOOK_THEM)
        socket.flush()
        files = [Path(n) for n in names]
        if self._open is None:
            self.waiting += files
        else:
            self._open(files)
