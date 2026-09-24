# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""The version an installer is built as: a tag's, or a development build's."""

import importlib
import sys
import tomllib
from pathlib import Path

import pytest

import pycangui

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT / "build"))
version = importlib.import_module("version")


def test_a_tag_is_built_as_the_tag():
    assert version.installer_version("0.2.0", "v0.2.0", "497694c", False) == "0.2.0"


def test_a_tag_that_disagrees_with_the_package_is_refused():
    """An installer called 0.2.0 whose Help > About says 0.1.0 is worse than
    no release: it is caught here, where it costs a bump and a retag."""
    with pytest.raises(version.VersionError, match="__version__"):
        version.installer_version("0.1.0", "v0.2.0", "497694c", False)


def test_any_other_commit_is_a_development_build_named_after_it():
    built = version.installer_version("0.2.0", None, "497694c", False)
    assert built == "0.2.0-dev-497694c"


def test_uncommitted_changes_are_said():
    assert version.installer_version("0.2.0", None, "497694c", True).endswith("-dirty")


def test_a_tag_with_changes_on_top_is_not_that_release():
    built = version.installer_version("0.2.0", "v0.2.0", "497694c", True)
    assert built != "0.2.0"
    assert "497694c" in built


def test_without_git_it_is_still_marked_as_development():
    assert version.installer_version("0.2.0", None, None, False) == "0.2.0-dev"


@pytest.mark.parametrize(
    ("package", "numbers"),
    [("0.2.0", "0.2.0.0"), ("1.2", "1.2.0.0"), ("1.2.3.4.5", "1.2.3.4")],
)
def test_the_windows_version_resource_gets_four_numbers(package, numbers):
    assert version.file_version(package) == numbers


def test_it_reads_the_same_version_the_package_reports():
    assert version.package_version() == pycangui.__version__


def test_the_version_is_written_down_once():
    """pyproject takes it from __init__.py rather than holding a copy of its
    own that a release could forget to bump."""
    project = tomllib.loads((PROJECT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "version" not in project["project"]
    assert "version" in project["project"]["dynamic"]
    attr = project["tool"]["setuptools"]["dynamic"]["version"]["attr"]
    assert attr == "pycangui.__version__"
