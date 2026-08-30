"""Put unhandled exceptions in the Event Log instead of nowhere.

pycangui is started with ``pythonw.exe``, which has no console.  An exception
raised inside a Qt slot does not propagate anywhere useful: PySide prints the
traceback to a standard error stream that does not exist, the slot simply
does not finish, and the window carries on looking perfectly healthy.

That is not hypothetical.  A stale call to a widget method -- ``.text()`` on
what had become a combo box -- stopped the demo device dead, and the only
symptom was a bus with no traffic on it.  Nothing was logged, no dialog
appeared, and the menu item stayed ticked.

So the hook routes tracebacks to the Event Log, where somebody can see them.
Delivery goes through a signal because an exception can be raised on any
thread and Qt widgets may only be touched from the GUI one.
"""

from __future__ import annotations

import sys
import threading
import traceback
from collections.abc import Callable

from PySide6.QtCore import QObject, Qt, QtMsgType, Signal, Slot, qInstallMessageHandler

from pycangui.core.events import ERROR, INFORMATION

#: Said of a Python traceback and of nothing else.  Qt complains through the
#: same handler about things that are often not pycangui's doing at all -- a
#: platform plugin, a window manager -- and telling somebody those are bugs in
#: this application is both wrong and unhelpful.
BUG = "Unhandled error (this is a bug in pycangui; the action it interrupted did not finish):"


class ExceptionLogger(QObject):
    """Sends unhandled exceptions to a sink on the GUI thread."""

    caught = Signal(str, str)  # message, level

    def __init__(self, sink: Callable[[str, str], None]) -> None:
        super().__init__()
        self._sink = sink
        self._previous = None
        self._previous_thread = None
        self._previous_qt = None
        self._seen: set[str] = set()
        # Queued: emitted from a worker thread, delivered on the GUI thread.
        self.caught.connect(self._deliver, Qt.QueuedConnection)

    def install(self) -> None:
        self._previous = sys.excepthook
        sys.excepthook = self._hook
        # Worker threads have their own hook, and protocol work happens on
        # them: an SDO read that dies must not die quietly either.
        self._previous_thread = threading.excepthook
        threading.excepthook = self._thread_hook
        # Qt complains through its own channel, and those complaints are
        # often the explanation -- a signal connected to nothing, a widget
        # touched off the GUI thread.
        self._previous_qt = qInstallMessageHandler(self._qt_hook)

    def remove(self) -> None:
        if self._previous is not None:
            sys.excepthook = self._previous
            self._previous = None
        if self._previous_thread is not None:
            threading.excepthook = self._previous_thread
            self._previous_thread = None
        qInstallMessageHandler(self._previous_qt)
        self._previous_qt = None

    def _thread_hook(self, args) -> None:
        text = "".join(
            traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)
        ).rstrip()
        name = getattr(args.thread, "name", "?")
        self.caught.emit(f"{BUG}\non thread {name}:\n{text}", ERROR)
        if self._previous_thread is not None:
            self._previous_thread(args)

    def _qt_hook(self, mode, context, message) -> None:
        # Critical and fatal are worth interrupting for.  A Qt warning is
        # usually the explanation of something else rather than the something
        # else -- "unable to set geometry", "this plugin does not support
        # propagateSizeHints" -- so it is recorded and left there, or the
        # Event Log would spring open at every start on some machines.
        if mode in (QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg):
            self.caught.emit(f"Qt: {message}", ERROR)
        elif mode == QtMsgType.QtWarningMsg:
            self.caught.emit(f"Qt: {message}", INFORMATION)
        if self._previous_qt is not None:
            self._previous_qt(mode, context, message)

    def _hook(self, exc_type, exc, tb) -> None:
        text = "".join(traceback.format_exception(exc_type, exc, tb)).rstrip()
        self.caught.emit(f"{BUG}\n{text}", ERROR)
        if self._previous is not None:
            self._previous(exc_type, exc, tb)  # keep stderr working when there is one

    @Slot(str, str)
    def _deliver(self, text: str, level: str) -> None:
        # A fault inside a repeating timer would otherwise fill the log with
        # the same traceback fifty times a second.
        key = text.rsplit("\n", 1)[-1]
        if key in self._seen:
            return
        self._seen.add(key)
        self._sink(text, level)
