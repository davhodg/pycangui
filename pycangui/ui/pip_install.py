# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Installing a package with pip, from inside pycangui, without freezing it.

For a library a pip installation leaves out until something needs it -- the
MDF reader first. pip runs as a process of its own, in this interpreter, so
the package lands where pycangui will import it from; the window stays usable
while it does, says what pip is doing, and can stop it.

That last part is the reason this exists. pip was run on the window's own
thread, so for the whole download -- minutes on a slow connection, since a
package brings others with it -- the window could not repaint, and the
progress box it had put up looked like a crash.
"""

from __future__ import annotations

import sys

from PySide6.QtCore import QEventLoop, QProcess, Qt, QTimer
from PySide6.QtWidgets import QProgressDialog, QWidget

#: Long enough for a slow connection; pip is stopped if it takes longer.
TIMEOUT_S = 900
#: The widest a line of pip's output is shown in the progress box.
SHOWN = 90


def pip_command(*packages: str) -> list[str]:
    """pip, in this interpreter, saying a line at a time what it is doing.

    ``--progress-bar off`` leaves one line per step -- collecting, downloading
    and its size, installing -- rather than a bar redrawn in place, which is
    what can be shown in a dialog.
    """
    return [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--progress-bar",
        "off",
        "--disable-pip-version-check",
        *packages,
    ]


def install(
    parent: QWidget | None, ctx, packages: list[str], command: list[str] | None = None
) -> bool:
    """Install ``packages`` with pip, showing what it is doing. True if pip succeeded.

    Waits for pip as a dialog would, with the window still turning. A cancel,
    a failure and a timeout are said in the Event Log -- a failure with pip's
    own last lines, which are what says why. ``command`` replaces pip itself,
    for tests.
    """
    names = ", ".join(packages)
    program, *arguments = command or pip_command(*packages)
    process = QProcess(parent)
    process.setProgram(program)
    process.setArguments(arguments)
    process.setProcessChannelMode(QProcess.MergedChannels)

    waiting = QProgressDialog(f"Installing {names}: starting pip...", "Cancel", 0, 0, parent)
    waiting.setWindowTitle(f"Installing {names}")
    waiting.setWindowModality(Qt.ApplicationModal)
    waiting.setMinimumDuration(0)
    waiting.setMinimumWidth(460)
    said: list[str] = []

    def read() -> None:
        text = bytes(process.readAll()).decode(errors="replace")
        for line in (part.strip() for part in text.splitlines()):
            if line:
                said.append(line)
                shown = line if len(line) <= SHOWN else line[: SHOWN - 3] + "..."
                waiting.setLabelText(f"Installing {names}, which can take a while:\n{shown}")

    loop = QEventLoop()
    process.readyRead.connect(read)
    process.finished.connect(loop.quit)
    process.errorOccurred.connect(lambda _error: loop.quit())
    waiting.canceled.connect(process.kill)
    timer = QTimer(parent, singleShot=True, interval=TIMEOUT_S * 1000)
    timer.timeout.connect(process.kill)
    waiting.show()
    process.start()
    timer.start()
    loop.exec()
    timed_out = not timer.isActive() and process.exitStatus() != QProcess.NormalExit
    timer.stop()
    read()
    cancelled = waiting.wasCanceled()
    waiting.close()

    if cancelled:
        ctx.warn(f"Installing {names} was cancelled")
        return False
    if process.error() == QProcess.FailedToStart:
        ctx.error(f"Installing {names} failed: pip could not be started")
        return False
    if timed_out:
        ctx.error(f"Installing {names} took over {TIMEOUT_S // 60} minutes and was stopped")
        return False
    if process.exitStatus() != QProcess.NormalExit or process.exitCode() != 0:
        tail = said[-3:] or ["pip stopped without saying why"]
        ctx.error(f"Installing {names} failed:\n" + "\n".join(tail))
        return False
    return True
