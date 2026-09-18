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
opened, filters being applied), so Tools > Verbose CAN logging turns it on
rather than making it the default and burying the log in noise.
"""

from __future__ import annotations

import logging
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


class _Bridge(logging.Handler):
    def __init__(self, sink: Callable[[str, str], None]) -> None:
        super().__init__(level=QUIET_LEVEL)
        self._sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        try:
            text = record.getMessage()
        except Exception:  # a broken format string is not worth taking down
            text = record.msg
        # The logger name says which backend spoke, which is the useful part
        # when two adapters are connected at once.
        try:
            self._sink(
                f"{record.levelname.title()} [{record.name}]: {text}",
                LEVEL_NAMES.get(record.levelno, INFORMATION),
            )
        except RuntimeError:
            # The Event Log's C++ side has gone: the window is being torn down
            # and a library is still talking. python-can's Bus.__del__ says
            # "was not properly shut down" from the garbage collector, which
            # runs at moments nobody chose, this one included. There is
            # nowhere left to put the message, and a logging handler that
            # raises turns a tidy shutdown into a traceback.
            pass


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
