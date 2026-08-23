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
from PySide6.QtWidgets import QApplication


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
def _drain_qt_events():
    yield
    # Fixtures tear down after this point, so deliver what is already queued
    # first, then let go of anything unreferenced before the next test.
    instance = QCoreApplication.instance()
    if instance is not None:
        for _ in range(3):
            instance.processEvents()
    gc.collect()
    if instance is not None:
        instance.processEvents()
