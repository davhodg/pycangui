# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""One offscreen QApplication for the whole test session, and a drain between
tests.

The drain matters more than it looks. Managers, buses and demo devices are
created per test and talk to each other through Qt signals, some of them
emitted from worker threads and so *queued*. If a queued signal is still in
the event queue when the test ends and its target is garbage collected, Qt
delivers it to a dead C++ object and the interpreter dies with a segmentation
fault (an access violation on Windows) somewhere in a later test -- which is
exactly what CI hit in test_emcy.

Draining the queue while everything is still alive, then collecting garbage
before the next test starts, keeps that from happening.
"""

import gc
import os
import weakref

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# python-can is loaded the way the application loads it, without the MF4
# support it would otherwise pull in. Otherwise a release of asammdf, or of
# something asammdf uses, that will not import takes ``import can`` with it
# and every test in the suite fails to be collected -- which is what a bad
# canmatrix release did to CI -- where only the tests that read MDF should.
from pycangui.__main__ import import_can_without_mf4

import_can_without_mf4()

from PySide6.QtCore import QCoreApplication, QEvent, QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

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


# --- a window a test made goes when the test does ------------------------------
#: The windows there were before the test now running began.
_WINDOWS_BEFORE: set = set()


def pytest_runtest_setup(item):
    instance = QApplication.instance()
    _WINDOWS_BEFORE.clear()
    if instance is not None:
        _WINDOWS_BEFORE.update(instance.topLevelWidgets())


def _ours_to_destroy(widget) -> bool:
    """A window of pycangui's own, with nothing above it that will take it away.

    Not one that has a parent: a drop-down's list and a button's menu are
    windows as far as Qt is concerned, and belong to the widget that made
    them, which deletes them itself -- destroyed here first, that is the
    same object deleted twice. And not one of Qt's own classes with no
    parent: those found here are pyqtgraph's, a plot's control panel and
    its menus, which the plot's Python side owns, and destroying them under
    it is a fault when the process exits. What is left is what a test
    makes and means: a main window, a detached pane, a dialog.
    """
    return widget.parent() is None and not type(widget).__module__.startswith("PySide6")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_teardown(item):
    """Destroy the windows the test made, once its fixtures have finished with them.

    Closing a window hides it and no more. Letting go of it does not free it
    either: its signals are connected to functions that hold the window, Qt
    holds the connections, and that is a ring the garbage collector cannot
    see round. So every window every test made stayed for the rest of the
    run -- 1,200 widgets each, 200,000 in a process by the end -- and each
    collection, and anything said to every widget, got slower as they piled
    up. Destroying the window takes its connections with it, and the rest
    then goes the ordinary way.

    After the fixtures and not in one: a fixture that closes its window on
    the way out needs the window to be there. A fixture wider than one test
    must not make a window, since the first test to use it would take it away.
    """
    yield
    instance = QApplication.instance()
    if instance is None:
        return
    for window in instance.topLevelWidgets():
        if window in _WINDOWS_BEFORE or not _ours_to_destroy(window):
            continue
        if getattr(window, "_closing", None) is False:
            # A main window its test never closed. Closing is what stops its
            # threads, and Qt ends the process over a thread destroyed while
            # it runs. Said to be closing already, so that it does not stop
            # to ask about anything unsaved: nobody is there to answer.
            window._closing = True
            window.close()
        window.deleteLater()
    _WINDOWS_BEFORE.clear()
    # Asked for by name: with no event loop running, nothing else delivers them.
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


@pytest.fixture(autouse=True)
def _no_unanswered_dialogs(monkeypatch):
    """A modal dialog nobody answered blocks forever.

    Offscreen or not, ``exec`` runs its own event loop and waits, so a test
    that reaches an unexpected dialog does not fail -- it hangs, which is the
    worst thing in a suite to work out. This turns that into an ordinary
    failure naming the dialog. A test that means to answer one patches
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


#: Background threads a test started, kept weakly so registering one here
#: never keeps it alive. See ``_drain_qt_events`` for what is done with them.
_THREADS: weakref.WeakSet = weakref.WeakSet()


def _still_going(thread) -> bool:
    """Whether this thread is running, asked in whichever way it answers.

    A ``QThread`` has ``isRunning``; a python-can ``Notifier`` has ``stopped``.
    Stopping one that has already stopped is not harmless: ``Worker.stop``
    disconnects a signal, and PySide warns loudly when there is nothing left
    to disconnect, which would fill the run with warnings about tests that
    tidied up properly.
    """
    try:
        if (running := getattr(thread, "isRunning", None)) is not None:
            return bool(running())
        return not getattr(thread, "stopped", False)
    except RuntimeError:  # the C++ side has gone, so nothing is running
        return False


def _watch(cls, monkeypatch):
    """Note every instance of ``cls`` a test makes, for stopping afterwards."""
    original = cls.__init__

    def made(self, *args, **kwargs):
        original(self, *args, **kwargs)
        _THREADS.add(self)

    monkeypatch.setattr(cls, "__init__", made)


@pytest.fixture(autouse=True)
def _drain_qt_events(monkeypatch):
    import can

    from pycangui.core.logging import Player
    from pycangui.core.worker import Worker

    for cls in (Player, Worker, can.Notifier):
        _watch(cls, monkeypatch)
    yield
    # Nothing a test started may outlive it. A QThread still running when Qt
    # destroys its C++ side aborts the process -- Worker.submit says the same
    # thing from the other end -- and a replay player or a python-can Notifier
    # left running sends frames into the next test's bus. Stopping them here
    # rather than leaving it to each test means a test that forgets is slow,
    # not a crash in whatever ran afterwards; that is how a replay player
    # outliving its test killed a CI worker in test_export and test_right_axis.
    for thread in list(_THREADS):
        if not _still_going(thread):
            continue  # its owner stopped it, which is the normal case
        try:
            thread.stop()
        except Exception:  # half-built, or stopping twice
            pass
        if (wait := getattr(thread, "wait", None)) is not None:
            wait(2000)
    _THREADS.clear()
    # Fixtures tear down after this point, so deliver what is already queued
    # first, then let go of anything unreferenced before the next test.
    instance = QCoreApplication.instance()
    if instance is not None:
        for _ in range(3):
            instance.processEvents()
    # Everything, not only the young generations. Collecting generations 0
    # and 1 is much cheaper -- a full collect walks the session's QApplication
    # and every imported module, and cost 78s against 48s for the suite -- but
    # it leaves anything that survived two collections mid-test to Python's
    # own sweep, at an arbitrary later moment rather than here, where the
    # queue has just been drained and no background thread is running. Qt
    # objects freed at that arbitrary moment are what CI kept dying on.
    gc.collect()
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
        from pycangui.core.settings import Settings

        self.nodes_dir = folder
        self.eds_dir = folder
        # Beside the folder rather than in it, like a workspace's own.
        self.settings = Settings(folder.parent / f"{folder.name}-settings.json")

    def log(self, message, level=None):
        pass

    warn = error = log


class _OneChannel:
    """A Channels stand-in wrapping the single bus a test already has.

    A node stands on a pycangui channel, and these tests have a BusManager
    rather than a Channels. This is the adapter between the two, and it
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
    others keep their own state the same way. A test that wants to reach
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
