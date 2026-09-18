# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""A queue-fed worker thread for blocking protocol calls (SDO, UDS requests).

Each job is a plain function; its result or exception is delivered back on the
GUI thread through ``done``, so callers never block the window. One worker
per protocol keeps requests for that protocol sequential, which is what the
protocols want anyway.
"""

from __future__ import annotations

import queue
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QThread, Signal, Slot

Callback = Callable[[Any, str | None], None]


class Worker(QThread):
    done = Signal(object)  # (callback, result, error)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._jobs: queue.Queue = queue.Queue()
        self.done.connect(self._deliver)

    def submit(self, fn: Callable[[], Any], callback: Callback) -> None:
        # Started on first use: a QThread that is running when Qt destroys it
        # aborts the process, so an owner that is created and dropped without
        # ever asking for anything must not leave a thread behind.
        if not self.isRunning():
            self.start()
        self._jobs.put((fn, callback))

    def run(self) -> None:
        while (job := self._jobs.get()) is not None:
            fn, callback = job
            try:
                self.done.emit((callback, fn(), None))
            except Exception as exc:  # report, never die
                self.done.emit((callback, None, f"{type(exc).__name__}: {exc}"))

    @Slot(object)
    def _deliver(self, packet: tuple) -> None:
        callback, result, error = packet
        callback(result, error)

    def stop(self) -> None:
        """Finish the queue and stop delivering results.

        The ``done`` connection is dropped first: a queued result arriving
        after the owner has been torn down would be delivered to a dead
        object, which Qt punishes with an access violation rather than an
        exception.
        """
        try:
            self.done.disconnect(self._deliver)
        except (RuntimeError, TypeError):  # already disconnected
            pass
        if not self.isRunning():
            return
        self._jobs.put(None)
        self.wait(2000)
