# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""A program a plugin runs beside pycangui: ``app.helper`` (plugin API 3).

Some work does not belong in pycangui's process -- a fieldbus master in Rust
or C, a server for another tool, anything that needs its own timing or its
own crash. A plugin ships the program in its folder and pycangui runs it:

* started from the plugin's folder, which is its working directory;
* if it serves something, the first line it prints is the port it listens on,
  handed to the plugin, so nothing has to agree a fixed port in advance;
* and the first line it reads on stdin is a token, new each time it starts,
  which the plugin has too (``token``): a port on 127.0.0.1 can be reached by
  any program on the computer, so a helper takes only a connection that says
  the token -- the pipe to its stdin is the one place nothing else can read;
* every later line it prints goes to the Event Log, stderr as warnings;
* stopped when the plugin is unloaded or reloaded and when pycangui closes:
  its stdin is closed first, the polite way to ask, and if it has not gone a
  second later it is killed;
* if it stops on its own, the Event Log says so and the user is offered a
  restart, unless the plugin said it would deal with that itself.

Qt's QProcess does the work, so its output arrives on the GUI thread with
nothing to poll and nothing for the plugin to lock.
"""

from __future__ import annotations

import secrets
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from PySide6.QtCore import QProcess, QTimer
from PySide6.QtWidgets import QMessageBox

from pycangui.core import installation
from pycangui.ui import messages

#: How long a helper has to print its port before it is taken as broken.
PORT_TIMEOUT_S = 10.0
#: How long a helper has to go once its stdin is closed, before it is killed.
STOP_GRACE_MS = 1000
#: Random bytes in a helper's token, written to its stdin as twice as many hex digits.
TOKEN_BYTES = 16

RESTART = "{plugin}'s helper {program} stopped (exit code {code}).\n\nStart it again?"


def command(folder: Path, program: str | Path, args: Sequence[str] = ()) -> list[str]:
    """What to run for ``program``, relative to the plugin's ``folder``.

    A ``.py`` file runs with this Python; the Windows build has none to offer,
    so there it is refused. On Windows ``.exe`` may be left off the name, and
    is preferred when it is there: a plugin made for every system carries the
    Linux program beside it, under the bare name, which Windows cannot run.
    """
    path = Path(program)
    if not path.is_absolute():
        path = folder / path
    if sys.platform == "win32" and not path.suffix and path.with_suffix(".exe").is_file():
        path = path.with_suffix(".exe")
    if not path.is_file():
        raise FileNotFoundError(f"no helper program {path}")
    if path.suffix == ".py":
        if installation.how_installed() == installation.FROZEN:
            raise ValueError(
                f"{path.name} is a Python script, and this build of pycangui has no "
                "Python to run it with: ship the helper as a program"
            )
        return [sys.executable, str(path), *map(str, args)]
    return [str(path), *map(str, args)]


class Helper:
    """One running helper program. Made by ``app.helper``, not directly."""

    def __init__(
        self,
        app,
        argv: list[str],
        folder: Path,
        *,
        port: bool = True,
        on_ready: Callable[[int | None], None] | None = None,
        on_exit: Callable[[int], None] | None = None,
        timeout: float = PORT_TIMEOUT_S,
    ) -> None:
        self._app = app
        self._argv = argv
        self._folder = folder
        self._wants_port = port
        self._on_ready = on_ready
        self._on_exit = on_exit
        self._timeout_ms = int(timeout * 1000)
        self.name = Path(argv[1] if Path(argv[0]) == Path(sys.executable) else argv[0]).name
        #: The port it printed, once it has; None before, and when it serves none.
        self.port: int | None = None
        #: What a connection to it must say, new each time it starts; "" when
        #: it serves none, as it is then given none.
        self.token = ""
        self._process: QProcess | None = None
        self._stopping = False
        self._partial = {"out": b"", "err": b""}
        self._timer = QTimer()
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._no_port)

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.state() != QProcess.NotRunning

    def start(self) -> None:
        """Start it (again). Called by ``app.helper``; a plugin may call it after a stop."""
        if self.running:
            return
        self.port = None
        self._stopping = False
        self._partial = {"out": b"", "err": b""}
        process = QProcess(self._app.window)  # gone with the window, whatever else happens
        process.setWorkingDirectory(str(self._folder))
        process.readyReadStandardOutput.connect(lambda: self._read(process, "out"))
        process.readyReadStandardError.connect(lambda: self._read(process, "err"))
        process.finished.connect(lambda code, status: self._finished(process, code, status))
        process.errorOccurred.connect(lambda error: self._error(process, error))
        if self._wants_port:
            self._timer.start(self._timeout_ms)
        else:
            process.started.connect(lambda: self._ready(None))
        self._process = process
        self.token = secrets.token_hex(TOKEN_BYTES) if self._wants_port else ""
        process.start(self._argv[0], self._argv[1:])
        if self.token:
            process.write(f"{self.token}\n".encode("ascii"))  # waits in the pipe until it reads

    def stop(self) -> None:
        """Ask it to stop by closing its stdin; kill it if it has not a moment later."""
        self._timer.stop()
        process, self._process = self._process, None
        if process is None:
            return
        self._stopping = True
        if process.state() != QProcess.NotRunning:
            process.closeWriteChannel()
            if not process.waitForFinished(STOP_GRACE_MS):
                process.kill()
                process.waitForFinished(STOP_GRACE_MS)
        process.deleteLater()

    # --- what it says ----------------------------------------------------------------------
    def _read(self, process: QProcess, which: str) -> None:
        raw = bytes(
            process.readAllStandardOutput() if which == "out" else process.readAllStandardError()
        )
        *lines, self._partial[which] = (self._partial[which] + raw).split(b"\n")
        for line in lines:
            self._line(which, line.decode("utf-8", "replace").rstrip("\r"))

    def _line(self, which: str, text: str) -> None:
        if which == "out" and self._wants_port and self.port is None and self._timer.isActive():
            self._timer.stop()
            try:
                port = int(text.strip())
            except ValueError:
                said = f"helper {self.name} printed {text!r} where its port should be; stopped"
                self._app.error(said)
                self.stop()
                return
            self.port = port
            self._ready(port)
            return
        if not text.strip():
            return
        (self._app.warn if which == "err" else self._app.log)(f"helper {self.name}: {text}")

    def _ready(self, port: int | None) -> None:
        self._app.log(f"helper {self.name} started" + (f", on port {port}" if port else ""))
        if self._on_ready is not None:
            self._on_ready(port)

    def _no_port(self) -> None:
        within = self._timeout_ms / 1000
        self._app.error(f"helper {self.name} printed no port within {within:g} s; stopped")
        self.stop()

    # --- and how it ends -------------------------------------------------------------------
    def _error(self, process: QProcess, error) -> None:
        if error == QProcess.FailedToStart and process is self._process:
            self._timer.stop()
            self._process = None
            self._app.error(f"helper {self.name} could not be started: {process.errorString()}")

    def _finished(self, process: QProcess, code: int, _status) -> None:
        if process is not self._process or self._stopping:
            return  # asked to stop, or an earlier run's
        self._timer.stop()
        for which in ("out", "err"):  # a last line with no newline after it
            if self._partial[which]:
                self._line(which, self._partial[which].decode("utf-8", "replace"))
                self._partial[which] = b""
        self._process = None
        process.deleteLater()
        self._app.error(f"helper {self.name} stopped on its own (exit code {code})")
        if self._on_exit is not None:
            self._on_exit(code)
            return
        answer = messages.question(
            self._app.window,
            "A plugin's helper stopped",
            RESTART.format(plugin=self._app.plugin, program=self.name, code=code),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if answer == QMessageBox.Yes:
            self.start()
