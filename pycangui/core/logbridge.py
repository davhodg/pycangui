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
#: How many different lines are remembered as recent. A backend can say two
#: or three things about each frame -- the IXXAT one says "CAN acknowledgment
#: error" and then the frame's flags -- so a line is a repeat if it was said
#: lately, not only if it was the last thing said.
RECENT_LINES = 16


class Repeats:
    """Which lines to say, out of a stream where the same few come again and again.

    A line not said lately is said. One said lately is counted, and the count
    is said with the line now and then (``REPEATS_SAID_EVERY_S``), and once
    more when it stops: when it has not come for longer than that, or when a line
    that is not one of the recent ones arrives. Lines are whatever the caller
    compares by -- (text, level) for the Event Log. Not thread safe on its
    own; the caller holds a lock where lines arrive on several threads.
    """

    def __init__(self) -> None:
        #: line -> [repeats not yet said, when it was last said, when last seen]
        self._recent: dict[tuple[str, str], list[float]] = {}

    def say(self, line: tuple[str, str], now: float) -> list[tuple[str, str]]:
        out = self._gone_quiet(now)
        seen = self._recent.get(line)
        if seen is not None:
            seen[0] += 1
            seen[2] = now
            if now - seen[1] >= REPEATS_SAID_EVERY_S:
                out.append(self._count(line, seen, now))
            return out
        # Something new: whatever is still being counted is said first, so the
        # counts come before the line that ended them.
        for was, pending in self._recent.items():
            if pending[0]:
                out.append(self._count(was, pending, now))
        self._recent[line] = [0, now, now]
        if len(self._recent) > RECENT_LINES:
            del self._recent[min(self._recent, key=lambda k: self._recent[k][2])]
        out.append(line)
        return out

    def _gone_quiet(self, now: float) -> list[tuple[str, str]]:
        """The counts of lines that have stopped coming, and those lines forgotten."""
        out = []
        for line, seen in list(self._recent.items()):
            if now - seen[2] > REPEATS_SAID_EVERY_S:
                if seen[0]:
                    out.append(self._count(line, seen, now))
                del self._recent[line]
        return out

    @staticmethod
    def _count(line: tuple[str, str], seen: list[float], now: float) -> tuple[str, str]:
        text, level = line
        said = (repeated(text, int(seen[0]), now - seen[1]), level)
        seen[0], seen[1] = 0, now
        return said


class _Bridge(logging.Handler):
    def __init__(self, sink: Callable[[str, str], None]) -> None:
        super().__init__(level=QUIET_LEVEL)
        self._sink = sink
        #: Records arrive on any thread.
        self._lock = threading.Lock()
        self._repeats = Repeats()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            text = record.getMessage()
        except Exception:  # a broken format string is not worth taking down
            text = record.msg
        # The canopen package logs an abort as a bare number -- "Transfer
        # aborted by client with code 0x05040000" -- and stops there, which
        # is a code and a shrug. It has the table, and so do we, so the
        # meaning goes in beside it.
        level = LEVEL_NAMES.get(record.levelno, INFORMATION)
        said_as = record.levelname.title()
        if record.name.startswith("canopen"):
            from pycangui.canopen import explain_aborts, mapped_not_found, sdo_log

            text = explain_aborts(text)
            # Nor does it say whose: this thread's transfer, where there is one,
            # which also keeps two nodes' failures from reading as one repeated.
            if record.name.startswith("canopen.sdo") and (doing := sdo_log.under_way()):
                text = f"{sdo_log.where(doing)}: {text}"
            # A mapping entry whose object the file lacks is said with the PDO
            # that maps it, and where the mapping came from. In a disabled PDO
            # it is only a note: nothing is decoded or sent by it.
            if record.name.startswith("canopen.pdo") and (mapped := mapped_not_found(text)):
                text, enabled = mapped
                if not enabled:
                    level, said_as = INFORMATION, "Info"
        # The logger name says which backend spoke, which is the useful part
        # when two adapters are connected at once.
        line = f"{said_as} [{record.name}]: {text}"
        try:
            for said in self._what_to_say(line, level):
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
        """This line, or nothing while it repeats -- with how many times, now and then.

        The count is said with the line itself, not as "the line above": by the
        time it is said the line may have scrolled out of sight, or out of the
        diagnostics report's last few problems.
        """
        with self._lock:
            return self._repeats.say((line, level), time.monotonic())


def repeated(line: str, count: int, seconds: float) -> str:
    """The line again, with how many more times it came and over how long."""
    times = f"{count} more time{'s' if count != 1 else ''}"
    return f"{line} ({times} in {max(seconds, 1):.0f} s)"


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
