"""Starting up: noticing a dependency that was added since .venv was built.

The launchers used to set up only when .venv was missing, so adding a library
broke every existing checkout.  Under pythonw there is no console, so what the
user saw was a window that never opened and no message anywhere.
"""

import importlib
import sys
from pathlib import Path

import pytest
from PySide6.QtWidgets import QMessageBox

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
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: shown.append(a[2]))
    # None in sys.modules is what Python turns into an ImportError on import.
    monkeypatch.setitem(sys.modules, "pycangui.ui.main_window", None)
    monkeypatch.setattr(sys, "argv", ["pycangui"])

    assert entry.main() == 1
    assert shown and "missing" in shown[0]
    assert "pycangui.cmd" in shown[0], "and say what to do about it"


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
    assert 'install -e ".[dev]"' in text
