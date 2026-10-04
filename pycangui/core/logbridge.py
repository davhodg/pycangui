# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Send python-can's own log messages to the Event Log.

The backends say a great deal through the standard :mod:`logging` module and
nothing through their return values. The IXXAT backend, for one, reports every
bus error that way -- ``log.warning("CAN error: ...")`` -- so a wrong bitrate,
which produces a steady stream of error frames and no traffic at all, looked
from inside pycangui exactly like a bus with nothing on it.

Warnings and errors go to the Event Log by default. Info is where the useful
detail lives when something is actually wrong (which channels a backend
opened, filters being applied), so Tools > Settings > Verbose CAN logging turns it on
rather than making it the default and burying the log in noise.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from pycangui.core.events import ERROR, INFORMATION, WARNING

#: The loggers worth relaying. python-can names its loggers "can.<backend>",
#: so this covers every backend including ones installed later.
LOGGERS = ("can", "canopen", "j1939", "udsoncan", "isotp")

QUIET_LEVEL = logging.WARNING
VERBOSE_LEVEL = logging.INFO

#: A library that says "error" means it, and the Event Log should open for it.
#: This is the whole reason the bridge exists: the IXXAT backend reports a
#: wrong bitrate as a stream of log warnings and in no other way.
LEVEL_NAMES = {
    logging.CRITICAL: ERROR,
    logging.ERROR: ERROR,
    logging.WARNING: WARNING,
}

#: A line said again and again -- a library failing on every frame of
#: something a device keeps sending -- is said once, and then how many more
#: times at most this often, rather than a line a frame.
REPEATS_SAID_EVERY_S = 10.0


class _Bridge(logging.Handler):
    def __init__(self, sink: Callable[[str, str], None]) -> None:
        super().__init__(level=QUIET_LEVEL)
        self._sink = sink
        #: The last line said, its level, how many times it has come again
        #: since, and when that was last said. Records arrive on any thread.
        self._lock = threading.Lock()
        self._last: tuple[str, str] | None = None
        self._repeats = 0
        self._repeats_said = 0.0

    def emit(self, record: logging.LogRecord) -> None:
        try:
            text = record.getMessage()
        except Exception:  # a broken format string is not worth taking down
            text = record.msg
        # The canopen package logs an abort as a bare number -- "Transfer
        # aborted by client with code 0x05040000" -- and stops there, which
        # is a code and a shrug. It has the table, and so do we, so the
        # meaning goes in beside it.
        if record.name.startswith("canopen"):
            from pycangui.canopen import explain_aborts

            text = explain_aborts(text)
        # The logger name says which backend spoke, which is the useful part
        # when two adapters are connected at once.
        line = f"{record.levelname.title()} [{record.name}]: {text}"
        try:
            for said in self._what_to_say(line, LEVEL_NAMES.get(record.levelno, INFORMATION)):
                self._sink(*said)
        except RuntimeError:
            # The Event Log's C++ side has gone: the window is being torn down
            # and a library is still talking. python-can's Bus.__del__ says
            # "was not properly shut down" from the garbage collector, which
            # runs at moments nobody chose, this one included. There is
            # nowhere left to put the message, and a logging handler that
            # raises turns a tidy shutdown into a traceback.
            pass

    def _what_to_say(self, line: str, level: str) -> list[tuple[str, str]]:
        """This line, or nothing while it repeats -- with how many times, now and then."""
        now = time.monotonic()
        with self._lock:
            if (line, level) == self._last:
                self._repeats += 1
                if now - self._repeats_said < REPEATS_SAID_EVERY_S:
                    return []
                count, self._repeats, self._repeats_said = self._repeats, 0, now
                return [(repeated(count), level)]
            out = [(repeated(self._repeats), self._last[1])] if self._repeats else []
            self._last, self._repeats, self._repeats_said = (line, level), 0, now
            return [*out, (line, level)]


def repeated(count: int) -> str:
    return f"    (the line above, {count} more time{'s' if count != 1 else ''})"


class LogBridge:
    """Attaches to the library loggers for as long as it is wanted."""

    def __init__(self, sink: Callable[[str, str], None]) -> None:
        self._handler = _Bridge(sink)
        self._loggers = [logging.getLogger(name) for name in LOGGERS]
        for logger in self._loggers:
            logger.addHandler(self._handler)
            # Without this a library logger left at WARNING would never pass an
            # info record to the handler, however low the handler's level is.
            logger.setLevel(min(logger.level or logging.WARNING, QUIET_LEVEL))

    @property
    def verbose(self) -> bool:
        return self._handler.level <= VERBOSE_LEVEL

    def set_verbose(self, on: bool) -> None:
        level = VERBOSE_LEVEL if on else QUIET_LEVEL
        self._handler.setLevel(level)
        for logger in self._loggers:
            logger.setLevel(level)

    def detach(self) -> None:
        for logger in self._loggers:
            logger.removeHandler(self._handler)
