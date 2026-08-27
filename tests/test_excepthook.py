"""Failures reach the Event Log rather than a console that does not exist."""

import threading
import time

import pytest
from PySide6.QtCore import QTimer

from pycangui.core.excepthook import ExceptionLogger


@pytest.fixture
def logger(app):
    seen = []
    hook = ExceptionLogger(seen.append)
    hook.install()
    yield hook, seen
    hook.remove()


def drain(app, pred, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not pred() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)


def test_an_exception_in_a_slot_is_logged(app, logger):
    """The failure mode this exists for: pythonw has no stderr to print to.

    A stale widget call stopped the demo device dead and the only symptom was
    a bus with no traffic; nothing was written anywhere a user could see.
    """
    _hook, seen = logger

    def boom():
        raise AttributeError("'ChannelBox' object has no attribute 'text'")

    QTimer.singleShot(0, boom)
    drain(app, lambda: seen)
    assert seen, "an exception in a Qt slot must reach the log"
    assert "ChannelBox" in seen[0] and "AttributeError" in seen[0]
    assert "bug in pycangui" in seen[0], "and say what it means for the user"


def test_the_same_fault_is_not_repeated(app, logger):
    """A fault inside a 20 ms timer would otherwise fill the log with itself."""
    _hook, seen = logger

    def boom():
        raise ValueError("the same thing over and over")

    for _ in range(5):
        QTimer.singleShot(0, boom)
    drain(app, lambda: len(seen) >= 1)
    for _ in range(20):
        app.processEvents()
    assert len(seen) == 1, f"reported once, not five times: {seen}"


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_a_failure_on_a_worker_thread_is_logged(app, logger):
    """Protocol work runs on threads; a request that dies must not die quietly."""
    _hook, seen = logger

    def boom():
        raise RuntimeError("SDO worker fell over")

    thread = threading.Thread(target=boom, name="worker")
    thread.start()
    thread.join()
    drain(app, lambda: seen)
    assert seen and "SDO worker fell over" in seen[0]
    assert "worker" in seen[0], "name the thread it happened on"


def test_removing_the_hook_restores_what_was_there(app):
    import sys

    before, before_thread = sys.excepthook, threading.excepthook
    hook = ExceptionLogger(lambda _t: None)
    hook.install()
    assert sys.excepthook is not before
    hook.remove()
    assert sys.excepthook is before
    assert threading.excepthook is before_thread
