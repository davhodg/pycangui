"""One offscreen QApplication for the whole test session, and a drain between
tests.

The drain matters more than it looks.  Managers, buses and demo devices are
created per test and talk to each other through Qt signals, some of them
emitted from worker threads and so *queued*.  If a queued signal is still in
the event queue when the test ends and its target is garbage collected, Qt
delivers it to a dead C++ object and the interpreter dies with a segmentation
fault (an access violation on Windows) somewhere in a later test -- which is
exactly what CI hit in test_emcy.

Draining the queue while everything is still alive, then collecting garbage
before the next test starts, keeps that from happening.
"""

import gc
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QSettings
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

#: Taken before anything patches it, so a test that wants a real dialog can
#: have one back.
_REAL_EXEC = {QDialog: QDialog.exec, QMessageBox: QMessageBox.exec}


@pytest.fixture(scope="session", autouse=True)
def _isolate_settings(tmp_path_factory):
    """Keep QSettings in a temporary file rather than the registry.

    MainWindow saves its geometry and dock layout in closeEvent, so without
    this a test run leaves settings behind, and the next run restores a layout
    from the last one -- which is exactly what a layout test must not see.
    """
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(
        QSettings.IniFormat, QSettings.UserScope, str(tmp_path_factory.mktemp("settings"))
    )


@pytest.fixture(scope="session")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _no_unanswered_dialogs(monkeypatch):
    """A modal dialog nobody answered blocks forever.

    Offscreen or not, ``exec`` runs its own event loop and waits, so a test
    that reaches an unexpected dialog does not fail -- it hangs, which is the
    worst thing in a suite to work out.  This turns that into an ordinary
    failure naming the dialog.  A test that means to answer one patches
    ``exec`` itself, and its patch replaces this one.
    """

    def refuse(self, *_args, **_kwargs):
        raise AssertionError(
            f"a modal {type(self).__name__} opened that no test answered: {self.windowTitle()!r}"
        )

    for widget in (QDialog, QMessageBox):
        monkeypatch.setattr(widget, "exec", refuse)


@pytest.fixture
def real_dialogs(monkeypatch):
    """Put ``exec`` back, for the few tests that mean to open a real dialog.

    A test checking what happens *while* one is up has to have a real one, and
    arranges to close it itself.
    """
    for widget in (QDialog, QMessageBox):
        monkeypatch.setattr(widget, "exec", _REAL_EXEC[widget])


@pytest.fixture(autouse=True)
def _drain_qt_events():
    yield
    # Fixtures tear down after this point, so deliver what is already queued
    # first, then let go of anything unreferenced before the next test.
    instance = QCoreApplication.instance()
    if instance is not None:
        for _ in range(3):
            instance.processEvents()
    # The younger generations, not everything.  What has to go is the test's
    # own wreckage, which by definition has survived at most one collection
    # and so is still in generation 0 or 1; generation 2 holds the session's
    # QApplication and every imported module, which this was never trying to
    # free.  Walking those a thousand times over was a quarter of the run,
    # and grew as the heap did.  It is the safer direction too: the crash
    # this guards against came from collecting too eagerly against a queue
    # that had not drained, so leaving something alive a moment longer
    # cannot bring it back.
    gc.collect(1)
    if instance is not None:
        instance.processEvents()
