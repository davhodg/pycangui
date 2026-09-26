# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""The packages About and the diagnostics report list: all of them, read not written down."""

import tomllib
from pathlib import Path

from pycangui.core import packages

PROJECT = Path(__file__).resolve().parent.parent

PYPROJECT = """
[project]
dependencies = ["PySide6-Essentials>=6.8", "python-can[serial, pywin32]>=4.4"]
[project.optional-dependencies]
all = ["asammdf>=8"]
dev = ["pytest"]
"""


def names(requirements):
    return [r.name for r in requirements]


def test_the_list_is_the_dependencies_and_the_all_extra_but_not_the_dev_tools(tmp_path):
    (tmp_path / "pyproject.toml").write_text(PYPROJECT)
    found = packages.declared(tmp_path)
    assert names(found) == ["PySide6-Essentials", "python-can", "asammdf"]
    assert found[1].extras == {"serial", "pywin32"}
    assert [r.extra for r in found] == [None, None, "all"]


def test_every_dependency_pycangui_declares_is_listed():
    project = tomllib.loads((PROJECT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    wanted = project["dependencies"] + project["optional-dependencies"]["all"]
    listed = {r.key for r in packages.declared()}
    assert {packages.Requirement(line).key for line in wanted} == listed


def test_without_a_pyproject_the_installed_record_is_used(tmp_path, monkeypatch):
    monkeypatch.setattr(
        packages.metadata,
        "requires",
        lambda name: ["numpy>=1.26", 'asammdf>=8; extra == "all"', 'ruff; extra == "dev"'],
    )
    assert names(packages.declared(tmp_path)) == ["numpy", "asammdf"]


def test_what_they_brought_follows_the_extras_asked_for_and_only_what_is_here():
    requires = {
        "python-can": [
            "wrapt",
            'pyserial; extra == "serial"',
            'asammdf; extra == "mf4"',
            'pywin32; sys_platform == "win32"',
        ],
        "wrapt": ["python-can"],  # a loop must not go round for ever
        "pyserial": [],
    }
    here = {"python-can", "wrapt", "pyserial", "asammdf"}
    direct = [packages.Requirement("python-can[serial]>=4.4")]
    found = packages.pulled_in(direct, requires.get, lambda n: "1.0" if n in here else None)
    # asammdf is installed, but python-can was not asked for mf4; pywin32 is
    # asked for only on Windows, and is not here.
    assert found == ["pyserial", "wrapt"]


def test_about_names_every_package_with_its_version_or_why_not(monkeypatch):
    from pycangui.ui.help_menu import environment_report

    monkeypatch.setattr(packages, "version", lambda n: None if n == "asammdf" else "9.9")
    report = environment_report()
    for requirement in packages.declared():
        assert requirement.name in report
    assert "asammdf" in report and "not installed (optional)" in report
