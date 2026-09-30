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


def kind(package):
    return installation.origin(package)[0]


def test_a_pip_install_running_its_own_copy(site_packages):
    install(site_packages)
    assert kind(site_packages / "pycangui") == installation.PIP


def test_nothing_installed_is_told_apart(site_packages, tmp_path):
    """The case reported: a test environment without pycangui, started from
    a source folder, ran the folder's copy and nothing said so."""
    assert kind(tmp_path / "clone" / "pycangui") == installation.NOT_INSTALLED


def test_a_source_folder_beside_an_installed_copy_is_told_apart(site_packages, tmp_path):
    install(site_packages)
    assert kind(tmp_path / "clone" / "pycangui") == installation.OTHER_PIP


def test_an_editable_install_knows_its_source(site_packages, tmp_path):
    clone = tmp_path / "clone"
    (clone / "pycangui").mkdir(parents=True)
    install(site_packages, editable_from=clone)
    found, root, _version = installation.origin(clone / "pycangui")
    assert found == installation.EDITABLE and root == clone


def test_an_editable_install_of_another_folder_is_told_apart(site_packages, tmp_path):
    install(site_packages, editable_from=tmp_path / "one")
    assert kind(tmp_path / "other" / "pycangui") == installation.OTHER_EDITABLE


def test_every_kind_can_be_described(site_packages, tmp_path, monkeypatch):
    """A kind origin() can answer that how_installed() had no words for would
    throw from About."""
    for each in (
        installation.FROZEN,
        installation.PIP,
        installation.EDITABLE,
        installation.NOT_INSTALLED,
        installation.OTHER_PIP,
        installation.OTHER_EDITABLE,
    ):
        monkeypatch.setattr(installation, "origin", lambda _p, k=each: (k, tmp_path, "1.0"))
        assert installation.how_installed()


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
