# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""The one hook that answers no question: the window is up.

Every other hook is asked something -- which EDS, what this fault code means --
and returns an answer.  This one is told, and does whatever the workspace's own
setup needs doing: connect the channels this product lives on, load its
database, open the panes the job wants.

Two properties matter more than the feature.  Nothing it does can stop pycangui
starting, because the tool needed to fix a broken startup hook is the one that
would not have started.  And connecting through it asks the same question the
Connect button asks, because a workspace is a folder that gets handed to
colleagues.
"""

import pytest
from PySide6.QtCore import QSettings

from pycangui.core import workspaces
from pycangui.core.hooks import registry
from pycangui.ui.main_window import MainWindow


def settle(app, times=5):
    for _ in range(times):
        app.processEvents()


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    return tmp_path


def write_startup(source: str) -> None:
    """A workspace's own startup hook, as somebody would write one."""
    folder = workspaces.hooks_dir()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "startup.py").write_text(source, encoding="utf-8")


def opened(app, home) -> MainWindow:
    window = MainWindow()
    window.show()
    settle(app)
    return window


# --- that it is a hook at all -----------------------------------------------------------
def test_it_is_in_the_registry_like_any_other():
    assert "on_startup" in registry()["startup"]


def test_the_file_is_written_out_on_a_first_run(app, home):
    """Working, commented code to start from, like every other hook file."""
    window = opened(app, home)
    text = (workspaces.hooks_dir() / "startup.py").read_text(encoding="utf-8")
    assert "def on_startup(" in text
    assert "window.connect_channel" in text, "and it says how to connect"
    window.close()


# --- that it runs ------------------------------------------------------------------------
def test_it_runs_once_the_window_is_up(app, home):
    write_startup("def on_startup(window, *, ctx):\n    ctx.log('the startup hook ran')\n")
    window = opened(app, home)
    assert "the startup hook ran" in window.log.toPlainText()
    window.close()


def test_it_is_handed_the_window_and_everything_in_it(app, home):
    """The same objects the Python Console has, under the same names."""
    write_startup(
        "def on_startup(window, *, ctx):\n"
        "    panes = len(window.panes.names())\n"
        "    ctx.log('panes=%d canopen=%s' % (panes, window.canopen is not None))\n"
    )
    window = opened(app, home)
    assert "canopen=True" in window.log.toPlainText()
    assert "panes=0" not in window.log.toPlainText()
    window.close()


def test_it_runs_after_the_layout_is_restored(app, home):
    """A pane opened by the hook must not be put away again by the saved layout
    arriving over the top of it."""
    write_startup("def on_startup(window, *, ctx):\n    window.panes.show('canopen')\n")
    window = opened(app, home)
    assert window.panes.on_screen("canopen")
    window.close()


def test_it_runs_once_and_not_on_every_event(app, home):
    write_startup(
        "COUNT = []\n"
        "def on_startup(window, *, ctx):\n"
        "    COUNT.append(1)\n"
        "    ctx.log('ran %d' % len(COUNT))\n"
    )
    window = opened(app, home)
    settle(app, 20)
    assert "ran 1" in window.log.toPlainText()
    assert "ran 2" not in window.log.toPlainText()
    window.close()


# --- that it cannot stop the window opening -------------------------------------------------
def test_a_hook_that_raises_does_not_stop_pycangui_starting(app, home):
    """The one failure that would matter: the tool needed to fix a broken
    startup hook is the one that would not start."""
    write_startup("def on_startup(window, *, ctx):\n    raise RuntimeError('deliberate')\n")
    window = opened(app, home)
    assert window.isVisible()
    assert "deliberate" in window.log.toPlainText()
    window.close()


def test_a_hook_file_that_will_not_import_is_reported_and_skipped(app, home):
    write_startup("this is not python\n")
    window = opened(app, home)
    assert window.isVisible()
    assert "startup" in window.log.toPlainText().lower()
    window.close()


# --- connecting through it ---------------------------------------------------------------------
def test_a_virtual_channel_connects_without_asking(app, home, monkeypatch):
    """Nothing leaves pycangui, so there is nothing to ask about."""
    from pycangui.ui import confirm

    monkeypatch.setattr(
        confirm.QMessageBox,
        "exec",
        lambda _box: pytest.fail("it asked about a virtual bus"),
    )
    write_startup(
        "def on_startup(window, *, ctx):\n"
        "    window.connect_channel('CAN 1', 'virtual', 'vcan0', 500000)\n"
    )
    window = opened(app, home)
    assert window.channels.get("CAN 1").is_connected
    window.close()


def test_a_real_bus_still_raises_the_question(app, home, monkeypatch):
    """A workspace is a folder that gets copied and handed to a colleague.  One
    that silently joined a live bus when they opened it would be a bad thing to
    have built."""
    from pycangui.ui import confirm

    asked = []
    monkeypatch.setattr(
        confirm.QMessageBox,
        "exec",
        lambda box: (asked.append(box.text()), confirm.QMessageBox.Cancel)[1],
    )
    write_startup(
        "def on_startup(window, *, ctx):\n"
        "    window.connect_channel('CAN 1', 'pcan', 'PCAN_USBBUS1', 500000)\n"
    )
    window = opened(app, home)
    assert asked, "no question was asked before joining a real bus"
    assert "500" in asked[0], "and it names the bitrate, which is what it is for"
    assert not window.channels.get("CAN 1").is_connected, "cancelled means not connected"
    window.close()


def test_connecting_a_channel_that_is_not_there_says_so(app, home):
    window = opened(app, home)
    assert not window.connect_channel("Nonexistent", "virtual", "vcan0", 500000)
    assert "Nonexistent" in window.log.toPlainText()
    window.close()
