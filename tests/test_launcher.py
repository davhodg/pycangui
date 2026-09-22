# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Starting up: noticing a dependency that was added since .venv was built.

The launchers used to set up only when .venv was missing, so adding a library
broke every existing checkout. Under pythonw there is no console, so what the
user saw was a window that never opened and no message anywhere.
"""

import importlib
import sys
from pathlib import Path

import pytest
from PySide6.QtWidgets import QMessageBox

from pycangui.ui import messages

# build/ is not a package -- it holds the scripts the launchers and the
# packaging step call -- so it is put on the path rather than imported from.
PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT / "build"))
check_deps = importlib.import_module("check_deps")


# --- reading pyproject ----------------------------------------------------------------
def test_the_distribution_name_comes_out_of_the_requirement():
    assert check_deps.name_of("python-can[serial]>=4.4") == "python-can"
    assert check_deps.name_of("PySide6-Essentials>=6.7") == "PySide6-Essentials"
    assert check_deps.name_of("bincopy>=20.0") == "bincopy"
    assert check_deps.name_of("pywin32>=306; sys_platform == 'win32'") == "pywin32"


def test_a_windows_only_requirement_is_not_wanted_on_linux():
    """Otherwise every start on Linux would offer to install pywin32."""
    requirement = "pywin32>=306; sys_platform == 'win32'"
    assert check_deps.wanted(requirement, platform="win32")
    assert not check_deps.wanted(requirement, platform="linux")
    assert check_deps.wanted("cantools>=39", platform="linux"), "no marker means everywhere"


def test_a_marker_that_is_not_understood_is_left_alone():
    """Guessing that it applies would mean reinstalling on every single start."""
    assert not check_deps.wanted("something>=1; python_version < '3.13'")


def test_this_environment_has_what_the_project_asks_for():
    assert check_deps.missing() == []


def test_a_dependency_that_is_not_installed_is_named(tmp_path):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\ndependencies = ["cantools>=39", "nosuchpackage-xyz>=1"]\n', encoding="utf-8"
    )
    assert check_deps.missing(pyproject) == ["nosuchpackage-xyz"]


# --- the entry point ------------------------------------------------------------------
def test_a_missing_library_is_a_dialog_rather_than_a_silent_death(app, monkeypatch):
    """pythonw has nowhere to print, so the last chance to say anything is a window."""
    import pycangui.__main__ as entry

    shown = []
    monkeypatch.setattr(messages, "critical", lambda *a, **k: shown.append(a[2]))
    monkeypatch.setattr(QMessageBox, "exec", lambda _box: QMessageBox.Ok)  # the notice
    # None in sys.modules is what Python turns into an ImportError on import.
    monkeypatch.setitem(sys.modules, "pycangui.ui.main_window", None)
    monkeypatch.setattr(sys, "argv", ["pycangui"])

    assert entry.main() == 1
    assert shown
    assert "pycangui.cmd" in shown[0], "and say what to do about it"


def test_the_notice_is_shown_before_anything_is_loaded(app, monkeypatch):
    """It is what the loading hides behind, so the order is the feature: a
    notice shown after the libraries had loaded would be the same total time
    and none of the benefit."""
    import pycangui.__main__ as entry

    order = []
    monkeypatch.setattr(
        QMessageBox, "exec", lambda _box: order.append("notice answered") or QMessageBox.Cancel
    )
    monkeypatch.setattr(sys, "argv", ["pycangui"])
    monkeypatch.setitem(sys.modules, "pycangui.ui.main_window", None)
    monkeypatch.setattr(messages, "critical", lambda *a, **k: order.append("gave up"))

    assert entry.main() == 0, "quitting at the notice starts nothing"
    assert order == ["notice answered"], "and nothing was built to be given up on"


def test_quitting_at_the_notice_opens_no_window(app, monkeypatch):
    import pycangui.__main__ as entry

    monkeypatch.setattr(QMessageBox, "exec", lambda _box: QMessageBox.Cancel)
    monkeypatch.setattr(sys, "argv", ["pycangui"])
    opened = []
    monkeypatch.setattr("pycangui.ui.session.Session.open", lambda self: opened.append(1))
    assert entry.main() == 0
    assert not opened


LEAN_CAN = """
import sys
from pycangui.__main__ import import_can_without_mf4
import_can_without_mf4()
import can
bus = can.Bus(interface="virtual", channel="lean")
bus.shutdown()
print("pandas" in sys.modules, "asammdf" in sys.modules)
try:
    import asammdf
    print("asammdf importable")
except ImportError:
    print("asammdf not installed")
"""


def test_python_can_is_imported_without_pandas():
    """python-can's MF4 support drags in asammdf and pandas at import, some
    500 modules that pycangui never uses from there -- the bulk of a start
    reported at 29 s. A fresh interpreter, because this one has them already."""
    import importlib.util
    import subprocess

    out = subprocess.run(
        [sys.executable, "-c", LEAN_CAN], capture_output=True, text=True, check=True, timeout=120
    ).stdout.splitlines()
    assert out[0] == "False False", "pandas or asammdf was loaded with python-can"
    importable = importlib.util.find_spec("asammdf") is not None
    assert (out[1] == "asammdf importable") == importable, "MDF import must still work"


def test_the_selftest_covers_the_libraries_that_move_firmware():
    import pycangui.__main__ as entry

    assert "bincopy" in entry.SELFTEST_MODULES
    assert entry.selftest() == 0


# --- the launchers themselves ---------------------------------------------------------
@pytest.mark.parametrize("name", ("pycangui.cmd", "pycangui.sh"))
def test_the_launcher_checks_before_every_start(name):
    """Not only when .venv is absent -- that was the whole bug."""
    text = (PROJECT / name).read_text(encoding="utf-8")
    assert "check_deps.py" in text, f"{name} never notices a new dependency"
    assert 'install -e "."' in text
    assert ".[dev]" not in text, "a user's launcher has no business installing the test tools"


# --- the check that runs before every start ------------------------------------------------
@pytest.mark.parametrize("name", ("pycangui.cmd", "pycangui.sh"))
def test_the_dependency_check_is_not_repeated_for_an_unchanged_pyproject(name):
    """It costs a whole Python start -- a quarter of a second on every launch --
    to answer a question whose answer only changes when pyproject.toml does.

    So the answer is kept as a copy of the file it was the answer to: identical
    means asked and answered, and any edit at all asks again.
    """
    text = (PROJECT / name).read_text(encoding="utf-8")
    assert ".deps-ok" in text, f"{name} runs the check on every start"
    assert "check_deps.py" in text, "and still runs it when the answer could have changed"


@pytest.mark.parametrize("name", ("pycangui.cmd", "pycangui.sh"))
def test_the_stamp_is_only_written_after_a_good_answer(name):
    """A failed check remembered as an answer would be a .venv short of a
    library that nothing ever looks at again."""
    text = (PROJECT / name).read_text(encoding="utf-8")
    stamp = text.index(".deps-ok", text.index("check_deps.py"))
    installed = text.index('install -e "."')
    assert stamp > installed, f"{name} stamps before it has installed anything"


# --- startup timing ----------------------------------------------------------------
@pytest.fixture(autouse=True)
def _timing_left_as_found():
    """Reloading a module sticks, so put it back for whatever runs next."""
    yield
    import importlib

    from pycangui.core import timing

    importlib.reload(timing)


def test_the_report_is_off_unless_it_is_asked_for(monkeypatch):
    """The marks are always taken -- a start that turns out to have been slow
    cannot be measured afterwards -- but nothing is reported unasked."""
    import importlib

    from pycangui.core import timing

    monkeypatch.delenv(timing.ENV, raising=False)
    monkeypatch.setattr("sys.argv", ["pycangui"])
    fresh = importlib.reload(timing)
    assert not fresh.enabled(), "so __main__ writes no report"
    fresh.mark("something")
    assert fresh.total_work() > 0, "and the start is still measured"


def test_the_flag_turns_it_on_and_it_reports_each_step(monkeypatch, tmp_path):
    import importlib

    from pycangui.core import timing

    monkeypatch.setattr("sys.argv", ["pycangui", timing.FLAG])
    fresh = importlib.reload(timing)
    assert fresh.enabled()
    fresh.mark("first step")
    fresh.mark("second step")

    lines = fresh.report_lines()
    assert any("first step" in line for line in lines)
    assert any("second step" in line for line in lines)
    assert any("total" in line for line in lines)
    assert any("longest step" in line for line in lines), "it says which one to look at"

    written = fresh.write_report(tmp_path)
    assert written is not None and written.read_text(encoding="utf-8").count("step") >= 2


def test_the_environment_variable_turns_it_on_too(monkeypatch):
    import importlib

    from pycangui.core import timing

    monkeypatch.setattr("sys.argv", ["pycangui"])
    monkeypatch.setenv(timing.ENV, "1")
    assert importlib.reload(timing).enabled()
    monkeypatch.setenv(timing.ENV, "0")
    assert not importlib.reload(timing).enabled(), "0 means off, not 'set'"


def test_imports_are_attributed_to_the_package_they_are_in(monkeypatch):
    """The step that costs the most is usually 'libraries imported', which is
    no use on its own: the question is always which library."""
    import importlib
    import sys

    from pycangui.core import timing

    monkeypatch.setattr("sys.argv", ["pycangui", timing.FLAG])
    fresh = importlib.reload(timing)
    fresh.watch_imports()
    try:
        for name in ("wave", "colorsys", "difflib"):  # cheap, and rarely already in
            sys.modules.pop(name, None)
            importlib.import_module(name)
        lines = fresh.import_lines()
    finally:
        sys.meta_path[:] = [f for f in sys.meta_path if type(f).__name__ != "_TimedImports"]

    assert lines and "longest to import" in lines[0]
    assert any("difflib" in line or "wave" in line or "colorsys" in line for line in lines)


def test_the_report_says_the_notice_line_is_a_person_waiting(monkeypatch):
    """The libraries load behind the notice and the window is built after it,
    so the wait lands between two marks and looks like work."""
    import importlib

    from pycangui.core import timing

    monkeypatch.setattr("sys.argv", ["pycangui", timing.FLAG])
    fresh = importlib.reload(timing)
    fresh.mark("libraries imported")
    fresh.mark(fresh.NOTICE_STEP)
    fresh.mark("panes built")

    lines = fresh.report_lines()
    assert any(fresh.NOTICE_STEP in line for line in lines), "the wait is shown"
    assert any("not counting the wait" in line for line in lines), "and kept out of the total"


def test_the_launcher_stamp_is_read_in_both_shapes(monkeypatch):
    """A shell stamps epoch seconds, cmd stamps %TIME%, which is a clock
    reading; the report wants seconds either way."""
    import importlib
    import time as clock

    from pycangui.core import timing

    monkeypatch.delenv(timing.PYTHON_ENV, raising=False)
    monkeypatch.setenv(timing.LAUNCH_ENV, f"{clock.time() - 2.0:.3f}")
    timing = importlib.reload(timing)  # its clock is taken as it is imported
    took, _starting = timing.launcher_seconds()
    assert took is not None and 1.5 <= took <= 4.0

    now = clock.localtime()
    a_moment_ago = now.tm_hour * 3600 + now.tm_min * 60 + now.tm_sec - 1
    hours, rest = divmod(max(a_moment_ago, 0), 3600)
    minutes, seconds = divmod(rest, 60)
    monkeypatch.setenv(timing.LAUNCH_ENV, f"{hours:02d}:{minutes:02d}:{seconds:02d}.00")
    took, _starting = timing.launcher_seconds()
    assert took is not None and 0 <= took <= 5


def test_a_stamp_from_an_older_run_is_ignored(monkeypatch):
    import importlib
    import time as clock

    from pycangui.core import timing

    timing = importlib.reload(timing)
    monkeypatch.delenv(timing.PYTHON_ENV, raising=False)
    monkeypatch.setenv(timing.LAUNCH_ENV, f"{clock.time() - 3600:.3f}")
    assert timing.launcher_seconds() == (None, None), "an hour is somebody else's launch"
    monkeypatch.setenv(timing.LAUNCH_ENV, "not a time")
    assert timing.launcher_seconds() == (None, None)
    monkeypatch.delenv(timing.LAUNCH_ENV)
    assert timing.launcher_seconds() == (None, None), "nothing stamped it, nothing to say"


def test_the_script_and_the_interpreter_are_counted_apart(monkeypatch):
    """Doing less in the script and starting Python faster are different
    problems, so one number for both would not say which to work on."""
    import importlib
    import time as clock

    from pycangui.core import timing

    now = clock.time()
    monkeypatch.setenv(timing.LAUNCH_ENV, f"{now - 3.0:.3f}")  # script began
    monkeypatch.setenv(timing.PYTHON_ENV, f"{now - 1.0:.3f}")  # python started
    script, interpreter = importlib.reload(timing).launcher_seconds()
    assert script is not None and 1.5 <= script <= 2.5, "the script's own two seconds"
    assert interpreter is not None and 0.5 <= interpreter <= 1.5


def test_the_launcher_is_not_charged_with_what_came_after_it(monkeypatch):
    """It measures to the moment timing was imported, not to the moment the
    report is written: the report comes after Qt, the libraries and the
    window, and charging those to the launcher made a fast start look slow."""
    import importlib
    import time as clock

    from pycangui.core import timing

    monkeypatch.setenv(timing.LAUNCH_ENV, f"{clock.time() - 0.5:.3f}")
    monkeypatch.delenv(timing.PYTHON_ENV, raising=False)
    fresh = importlib.reload(timing)
    first, _ = fresh.launcher_seconds()
    clock.sleep(0.3)  # as if the window were being built
    second, _ = fresh.launcher_seconds()

    assert first is not None and second is not None
    assert abs(second - first) < 0.05, "asking later must not make the answer bigger"


def test_the_total_leaves_out_the_time_spent_waiting_for_a_person(monkeypatch):
    """A total that grows because somebody read the notice slowly is a total
    nobody can compare against the last run."""
    import importlib
    import time as clock

    from pycangui.core import timing

    monkeypatch.setattr("sys.argv", ["pycangui", timing.FLAG])
    fresh = importlib.reload(timing)
    fresh.mark("libraries imported")
    clock.sleep(0.2)  # as if somebody were reading
    fresh.mark(fresh.NOTICE_STEP)
    fresh.mark("window on screen")

    total = next(line for line in fresh.report_lines() if "not counting the wait" in line)
    assert float(total.split()[0]) < 0.15, "the reading time is not part of it"


def test_the_diagnostics_report_says_how_long_starting_took(app, tmp_path, monkeypatch):
    """Always, without the flag: nobody thinks to ask for timing before the
    start they wanted to measure, and a report from a slow machine is where
    the question actually arrives."""
    from PySide6.QtCore import QSettings

    from pycangui.core import timing
    from pycangui.ui.help_menu import diagnostics
    from pycangui.ui.main_window import MainWindow

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    timing.mark("a step of some kind")
    window = MainWindow()

    report = diagnostics(window)

    assert "startup timing" in report
    assert "total, not counting the wait" in report
    window.close()
