# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
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
    # free.  Walking those a thousand times over cost more than the whole of
    # the rest of the fixture, and grew as the heap did: a full collect here
    # is 78s against 48s for the suite.
    #
    # What this gives up, and it is worth being honest about it: an object
    # that survived two collections mid-test is in generation 2, so it is
    # freed by Python's own automatic sweep at some arbitrary later moment
    # rather than here, where the queue has just been drained.  That is a
    # slightly worse moment, not a new hazard -- the automatic sweep runs
    # during tests whatever this line does.  A worker did die once under
    # parallel load (test_replay_action, unreproduced in a dozen runs since);
    # if that comes back, putting the full collect here is the first thing
    # to try.
    gc.collect(1)
    if instance is not None:
        instance.processEvents()


# --- the demo device, as the tests want it ---------------------------------------
class _NodeContext:
    """Enough Context for the node manager, pointed at a temporary folder.

    Not the real Context: most of these tests never set PYCANGUI_HOME, and
    one that quietly wrote example files into somebody's own workspace would
    be a test with a side effect nobody asked for.
    """

    def __init__(self, folder):
        self.nodes_dir = folder
        self.eds_dir = folder

    def log(self, message, level=None):
        pass

    warn = error = log


class _OneChannel:
    """A Channels stand-in wrapping the single bus a test already has.

    A node stands on a pycangui channel, and these tests have a BusManager
    rather than a Channels.  This is the adapter between the two, and it
    refuses to invent a second one: a test asking for a channel it did not
    set up has a bug in it.
    """

    def __init__(self, name, bus):
        self._name = name
        self._bus = bus

    def get(self, name):
        return self._bus if name == self._name else None

    def add(self, name):
        raise AssertionError(f"the test has no channel called {name!r}")


class _Demo:
    """The running demo, reachable by protocol.

    ``demo["canopen_device"].state.device`` is the CANopen server, and the
    others keep their own state the same way.  A test that wants to reach
    inside the device -- to check a value landed in the object dictionary,
    say -- goes through the node that owns it, which is also how a node file
    would.
    """

    def __init__(self, manager, nodes):
        self.manager = manager
        self.nodes = nodes

    def __getitem__(self, kind):
        return self.nodes[kind]

    def stop_all(self):
        self.manager.stop_all()


@pytest.fixture
def demo_device(tmp_path_factory):
    """Start the demo device on a bus: the shipped example nodes, as the
    application starts them when the demo channel is connected.

    Returns a function so a test can pick which protocols it needs -- there
    is no sense standing up a J1939 engine for a test about SDO -- and every
    node started is stopped afterwards.
    """
    from pycangui.core.simnodes import SimulatedNodes
    from pycangui.nodes import DEMO

    started = []

    def start(bus, kinds=DEMO, channel="CAN"):
        nodes = SimulatedNodes(
            _NodeContext(tmp_path_factory.mktemp("nodes")),
            channels=_OneChannel(channel, bus),
        )
        started.append(nodes)
        return _Demo(nodes, {kind: nodes.start(kind, channel) for kind in kinds})

    yield start
    for nodes in started:
        nodes.stop_all()
