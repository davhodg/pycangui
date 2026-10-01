# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""The command line: --workspace, --run with its exit code, --skip-start-warning,
and a DCF or EDS handed to the pycangui already open."""

import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings

from pycangui import __main__ as entry
from pycangui.core import cli, script_run, workspaces
from pycangui.ui import handoff
from pycangui.ui.canopen_view import ROLE_FILE
from pycangui.ui.main_window import MainWindow
from pycangui.ui.session import Session

EDS = """[FileInfo]
FileName=drive.eds
[DeviceInfo]
VendorNumber=0x42
[MandatoryObjects]
SupportedObjects=1
1=0x1000
[1000]
ParameterName=Device type
ObjectType=0x7
DataType=0x0007
AccessType=ro
DefaultValue=0x00020192
"""


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(workspaces, "_this_run", "")
    return tmp_path


def script(tmp_path, text, name="job.py"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


# --- reading it -----------------------------------------------------------------------------
def test_files_and_options_are_told_apart(home):
    workspaces.create("rig")
    job = script(home, "pass")
    options = cli.parse(["pycangui", "--workspace", "rig", f"--run={job}", "a.dcf", "b.eds"])
    assert not options.problem
    assert options.workspace == "rig" and options.run == job.resolve()
    assert [f.name for f in options.files] == ["a.dcf", "b.eds"]
    assert not options.hand_over, "a workspace or a script means a pycangui of its own"


def test_qt_options_and_their_values_are_left_for_qt(home):
    options = cli.parse(["pycangui", "-platform", "offscreen", "-reverse", "--timing", "x.dcf"])
    assert not options.problem and [f.name for f in options.files] == ["x.dcf"]
    assert options.hand_over, "files alone go to the pycangui already open"


@pytest.mark.parametrize(
    "argv",
    [
        ["--wrokspace", "rig"],
        ["--workspace", "nowhere"],
        ["--run", "missing.py"],
        ["--workspace"],
        ["--skip-start-warning"],
        ["--skip_start_warning", "x.dcf"],
    ],
)
def test_a_command_line_that_cannot_be_followed_is_refused(home, argv):
    assert cli.parse(["pycangui", *argv]).problem


@pytest.mark.parametrize("flag", ["--skip-start-warning", "--skip_start_warning"])
def test_the_notice_can_be_skipped_only_for_a_run(home, flag):
    options = cli.parse(["pycangui", "--run", str(script(home, "pass")), flag])
    assert options.skip_notice and not options.problem


def test_a_workspace_named_on_the_command_line_is_for_this_run_only(home):
    workspaces.create("rig")
    workspaces.use("rig")
    assert workspaces.active() == "rig"
    workspaces.use("")
    assert workspaces.active() == workspaces.DEFAULT, "next time opens what it did before"
    workspaces.use("rig")
    workspaces.set_active(workspaces.DEFAULT)
    assert workspaces.active() == workspaces.DEFAULT, "a switch in the window is for good"


# --- running a script -----------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("x = 1", 0),
        ("import sys; sys.exit()", 0),
        ("import sys; sys.exit(3)", 3),
        ("import sys; sys.exit('no answer from node 5')", 1),
        ("raise RuntimeError('broken')", 1),
        ("assert value == 7", 0),
        ("assert value == 8", 1),
    ],
)
def test_the_exit_code_is_the_scripts(tmp_path, text, code):
    assert script_run.run(script(tmp_path, text), {"value": 7}, print, print) == code


def test_what_a_script_prints_is_kept_line_by_line(tmp_path):
    said = []
    script_run.run(script(tmp_path, "print('one'); print('two', end='')"), {}, said.append, print)
    assert said == ["one", "two"]


def test_a_script_that_is_not_there_fails(tmp_path):
    complained = []
    assert script_run.run(tmp_path / "gone.py", {}, print, complained.append) == 1
    assert complained


# --- in the window --------------------------------------------------------------------------
@pytest.fixture
def window(app, home):
    QSettings().clear()
    win = MainWindow()
    win.show()
    app.processEvents()
    yield win
    win.close()


def file_rows(window):
    rows = window.canopen_view.nodes
    return [rows.topLevelItem(i) for i in range(rows.topLevelItemCount())]


def test_a_dcf_on_the_command_line_opens_in_the_canopen_pane(window, home):
    eds = home / "drive.eds"
    eds.write_text(EDS, encoding="utf-8")
    window.open_files([eds, home / "notes.txt"])
    assert [Path(r.data(0, ROLE_FILE)).name for r in file_rows(window)] == ["drive.eds"]
    assert window.panes.on_screen(window._canopen_pane)


def test_run_waits_for_the_startup_hook_then_closes_with_the_code(app, window, home):
    job = script(home, "import sys; sys.exit(0 if window is not None and canopen else 5)")
    loaded = {}
    entry.run_when_started(window, job, loaded)
    window.started.emit()
    app.processEvents()
    assert loaded["exit code"] == 0 and not window.isVisible()
    window.started.emit()  # a second start, from a workspace switch, runs nothing
    assert loaded["exit code"] == 0


def test_wait_lets_the_window_run(app, window):
    ticks = []
    from PySide6.QtCore import QTimer

    QTimer.singleShot(10, lambda: ticks.append(1))
    window._console_namespace()["wait"](0.1)
    assert ticks == [1], "time.sleep would have held the timer back"


def test_files_between_windows_wait_for_the_next(app, home):
    opened = []

    class Window:
        def open_files(self, files):
            opened.extend(files)

    session = Session(Window)
    session.open_files([Path("a.dcf")])
    assert opened == []
    session.window = Window()
    session.open_files([Path("b.dcf")])
    assert [f.name for f in opened] == ["a.dcf", "b.dcf"]


# --- handing over ---------------------------------------------------------------------------
@pytest.fixture
def name():
    return f"pycangui-test-{uuid.uuid4().hex[:12]}"


SECOND = """
import sys
from pathlib import Path
from PySide6.QtCore import QCoreApplication
app = QCoreApplication([])
from pycangui.ui import handoff
sys.exit(0 if handoff.hand_over([Path(f) for f in sys.argv[2:]], sys.argv[1]) else 1)
"""


def hand_over_from_a_second_pycangui(app, files, name):
    """A second pycangui, as a process of its own -- a local socket is between
    processes -- while this one's event loop answers."""
    second = subprocess.Popen(
        [sys.executable, "-c", SECOND, name, *map(str, files)],
        cwd=Path(__file__).parent.parent,
    )
    while second.poll() is None:
        app.processEvents()
        time.sleep(0.01)
    return second.returncode == 0


def test_files_go_to_the_pycangui_already_open(app, name):
    listener = handoff.Listener(name)
    got = []
    listener.deliver(got.extend)
    try:
        assert listener.listening
        assert hand_over_from_a_second_pycangui(app, [Path("C:/x/drive.dcf")], name)
        app.processEvents()
        assert [f.name for f in got] == ["drive.dcf"]
    finally:
        listener.close()


def test_files_that_come_before_the_window_wait_for_it(app, name):
    listener = handoff.Listener(name)
    try:
        assert hand_over_from_a_second_pycangui(app, [Path("early.eds")], name)
        app.processEvents()
        got = []
        listener.deliver(got.extend)
        assert [f.name for f in got] == ["early.eds"]
    finally:
        listener.close()


def test_with_nobody_open_this_one_opens_them(name):
    assert not handoff.hand_over([Path("a.dcf")], name)


def test_a_second_copy_leaves_the_first_listening(app, name):
    first = handoff.Listener(name)
    second = handoff.Listener(name)
    try:
        assert first.listening and not second.listening
    finally:
        second.close()
        first.close()


def test_the_name_belongs_to_the_user_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path / "one"))
    one = handoff.server_name()
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path / "two"))
    assert handoff.server_name() != one
