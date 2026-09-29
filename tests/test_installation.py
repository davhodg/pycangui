# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Where pycangui is running from, as About reports it.

A machine with several Pythons can start pycangui from any of them, and a
Python started inside a source folder imports the folder whether or not its
environment has pycangui installed. The report has to tell those apart.
"""

import json
import sys

import pytest

from pycangui.core import installation
from pycangui.ui.help_menu import environment_report


@pytest.fixture
def site_packages(tmp_path, monkeypatch):
    """An environment's package folder, empty until a test installs into it."""
    folder = tmp_path / "site-packages"
    folder.mkdir()
    monkeypatch.setattr(installation, "site_folders", lambda: [str(folder)])
    monkeypatch.delattr(sys, "frozen", raising=False)
    return folder


def install(folder, editable_from=None):
    """What pip leaves behind: a dist-info, and the package unless editable."""
    info = folder / "pycangui-0.1.1.dist-info"
    info.mkdir()
    (info / "METADATA").write_text("Metadata-Version: 2.1\nName: pycangui\nVersion: 0.1.1\n")
    if editable_from is None:
        (folder / "pycangui").mkdir()
    else:
        url = editable_from.as_uri()
        (info / "direct_url.json").write_text(
            json.dumps({"dir_info": {"editable": True}, "url": url})
        )


def test_a_pip_install_running_its_own_copy(site_packages):
    install(site_packages)
    assert installation.how_installed(site_packages / "pycangui") == "pip"


def test_nothing_installed_is_said(site_packages, tmp_path):
    """The case reported: a test environment without pycangui, started from
    a source folder, ran the folder's copy and nothing said so."""
    said = installation.how_installed(tmp_path / "clone" / "pycangui")
    assert "not installed" in said


def test_a_source_folder_beside_an_installed_copy_is_said(site_packages, tmp_path):
    install(site_packages)
    said = installation.how_installed(tmp_path / "clone" / "pycangui")
    assert said != "pip" and "not the pip install" in said


def test_an_editable_install_names_its_source(site_packages, tmp_path):
    clone = tmp_path / "clone"
    (clone / "pycangui").mkdir(parents=True)
    install(site_packages, editable_from=clone)
    said = installation.how_installed(clone / "pycangui")
    assert said.startswith("pip, editable") and str(clone) in said


def test_an_editable_install_of_another_folder_is_said(site_packages, tmp_path):
    install(site_packages, editable_from=tmp_path / "one")
    said = installation.how_installed(tmp_path / "other" / "pycangui")
    assert "not the editable install" in said


def test_a_build_left_in_the_source_folder_is_not_an_install(site_packages, tmp_path):
    """setuptools leaves pycangui.egg-info in a source folder, which Python
    finds on sys.path when started there. It installed nothing."""
    clone = tmp_path / "clone"
    (clone / "pycangui.egg-info").mkdir(parents=True)
    (clone / "pycangui.egg-info" / "PKG-INFO").write_text("Name: pycangui\nVersion: 0.1.1\n")
    sys.path.insert(0, str(clone))
    try:
        assert installation.installed() is None
    finally:
        sys.path.remove(str(clone))


def test_about_says_which_python_and_which_copy():
    report = environment_report()
    assert sys.executable in report
    assert str(installation.PACKAGE) in report
