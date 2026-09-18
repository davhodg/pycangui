# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""A plugin as one file: what goes in, what comes out, and what is refused.

Installed, a plugin is a folder. Distributed, a folder is useless -- it
cannot be attached to an email and "unzip this into the right place" is an
instruction people get wrong -- so a package is a zip, and installing is what
turns one back into a folder.

Most of this file is about the refusals, because this is the one place in
pycangui that takes a file from a stranger and turns it into code that runs.
"""

import zipfile

import pytest

from pycangui.core import plugin_package
from pycangui.core.plugin_package import PackageError
from pycangui.core.plugins import NO_VERSION

SOURCE = """
NAME = "Demo"
DESCRIPTION = "A demonstration."
VERSION = "2.1"


def register(app):
    pass
"""


@pytest.fixture
def plugin(tmp_path):
    """A plugin folder, as it looks once it is installed."""
    folder = tmp_path / "source" / "demo"
    folder.mkdir(parents=True)
    (folder / "plugin.py").write_text(SOURCE, encoding="utf-8")
    (folder / "extra.py").write_text("HELPER = 1\n", encoding="utf-8")
    return folder


@pytest.fixture
def into(tmp_path):
    """Where installed plugins go: the workspace's plugins folder."""
    return tmp_path / "workspace" / "plugins"


def zip_of(path, members: dict):
    with zipfile.ZipFile(path, "w") as archive:
        for name, text in members.items():
            archive.writestr(name, text)
    return path


# --- there and back ---------------------------------------------------------------------
def test_a_folder_packs_into_a_file_that_says_what_is_in_it(plugin, tmp_path):
    package = plugin_package.inspect(plugin_package.pack(plugin, tmp_path / "demo.zip"))
    assert (package.name, package.info.title, package.info.version) == ("demo", "Demo", "2.1")
    assert package.info.description == "A demonstration."


def test_what_was_packed_is_what_is_installed(plugin, tmp_path, into):
    plugin_package.install(plugin_package.pack(plugin, tmp_path / "demo.zip"), into)
    assert (into / "demo" / "plugin.py").read_text(encoding="utf-8") == SOURCE
    assert (into / "demo" / "extra.py").is_file(), "a plugin brings its own modules with it"


def test_the_folder_is_the_top_level_inside_the_package(plugin, tmp_path):
    """So that unzipping it by hand gives a plugin folder rather than scattering
    its contents into whatever directory somebody was standing in."""
    with zipfile.ZipFile(plugin_package.pack(plugin, tmp_path / "demo.zip")) as archive:
        assert {name.split("/")[0] for name in archive.namelist()} == {"demo"}


def test_bytecode_is_not_carried_along(plugin, tmp_path):
    """Compiled against whatever Python the sender happened to have."""
    (plugin / "__pycache__").mkdir()
    (plugin / "__pycache__" / "plugin.cpython-312.pyc").write_bytes(b"\x00")
    with zipfile.ZipFile(plugin_package.pack(plugin, tmp_path / "demo.zip")) as archive:
        assert not [n for n in archive.namelist() if "pycache" in n or n.endswith(".pyc")]


def test_a_supplied_plugin_goes_in_through_the_same_door(plugin, into):
    """Packed and unpacked rather than copied, so the path a stranger's plugin
    takes is the path we take every time."""
    package = plugin_package.install_folder(plugin, into)
    assert package.name == "demo"
    assert (into / "demo" / "plugin.py").is_file()
    assert not list(into.glob("*.zip")), "and the package it made on the way is not left behind"


# --- the shapes people actually make ------------------------------------------------------
def test_a_zip_of_the_folder_contents_is_named_after_the_file(tmp_path, into):
    """Somebody who selected the files rather than the folder and zipped those."""
    package = plugin_package.inspect(zip_of(tmp_path / "mything.zip", {"plugin.py": SOURCE}))
    assert package.name == "mything"


def test_the_way_some_archivers_spell_a_path_is_not_a_refusal(tmp_path):
    package = plugin_package.inspect(zip_of(tmp_path / "d.zip", {"./demo/plugin.py": SOURCE}))
    assert package.name == "demo"


def test_a_plugin_that_says_nothing_about_itself_is_still_a_plugin(tmp_path):
    package = plugin_package.inspect(zip_of(tmp_path / "quiet.zip", {"quiet/plugin.py": "x = 1\n"}))
    assert package.label == "quiet", "its folder name, until it is loaded and says otherwise"
    assert package.info.version == NO_VERSION


# --- and what it will not take -------------------------------------------------------------
def test_a_zip_that_writes_outside_its_folder_is_refused(tmp_path, into):
    """The oldest trick there is, and this is a file somebody sent you. Refused
    rather than repaired: Python's own extract would quietly drop the .. and
    write the file somewhere else."""
    bad = zip_of(tmp_path / "evil.zip", {"demo/plugin.py": SOURCE, "../taken-over.py": "boom"})
    with pytest.raises(PackageError, match="outside"):
        plugin_package.inspect(bad)
    with pytest.raises(PackageError):
        plugin_package.install(bad, into)
    assert not (tmp_path / "taken-over.py").exists()
    assert not (into / "demo").exists(), "and none of the rest of it was unpacked either"


def test_an_absolute_path_in_a_package_is_refused(tmp_path):
    bad = zip_of(tmp_path / "evil.zip", {"demo/plugin.py": SOURCE, "/etc/passwd": "x"})
    with pytest.raises(PackageError, match="outside"):
        plugin_package.inspect(bad)


def test_a_zip_with_no_plugin_in_it_is_not_a_plugin(tmp_path):
    with pytest.raises(PackageError, match=r"no plugin.py"):
        plugin_package.inspect(zip_of(tmp_path / "photos.zip", {"holiday/beach.jpg": "x"}))


def test_a_plugin_buried_deeper_than_one_folder_is_refused(tmp_path):
    """Rather than hunted for: what would be installed is then a guess."""
    with pytest.raises(PackageError, match=r"no plugin.py"):
        plugin_package.inspect(zip_of(tmp_path / "d.zip", {"demo/src/plugin.py": SOURCE}))


def test_a_package_holding_two_plugins_is_refused(tmp_path):
    """A package installs one thing under one name. A file that quietly
    installs three cannot be reasoned about from its name."""
    two = zip_of(tmp_path / "both.zip", {"one/plugin.py": SOURCE, "two/plugin.py": SOURCE})
    with pytest.raises(PackageError, match="more than one plugin"):
        plugin_package.inspect(two)


def test_something_that_is_not_a_zip_at_all(tmp_path):
    (tmp_path / "notes.txt").write_text("hello", encoding="utf-8")
    with pytest.raises(PackageError, match="not a zip"):
        plugin_package.inspect(tmp_path / "notes.txt")


def test_something_far_too_big_to_be_a_plugin(tmp_path, monkeypatch):
    monkeypatch.setattr(plugin_package, "MAX_BYTES", 32)
    big = zip_of(tmp_path / "big.zip", {"demo/plugin.py": SOURCE})
    with pytest.raises(PackageError, match="more than a plugin should be"):
        plugin_package.inspect(big)


def test_a_name_that_will_not_do_as_a_folder(tmp_path):
    with pytest.raises(PackageError, match="will not do"):
        plugin_package.inspect(zip_of(tmp_path / "my plugin!.zip", {"plugin.py": SOURCE}))


# --- replacing one that is there ------------------------------------------------------------
def test_installing_over_one_that_is_there_is_refused_unless_it_is_asked_for(
    plugin, tmp_path, into
):
    package = plugin_package.pack(plugin, tmp_path / "demo.zip")
    plugin_package.install(package, into)
    with pytest.raises(PackageError, match="already installed"):
        plugin_package.install(package, into)


def test_replacing_leaves_the_new_one_and_nothing_of_the_old(plugin, tmp_path, into):
    plugin_package.install(plugin_package.pack(plugin, tmp_path / "demo.zip"), into)
    (plugin / "extra.py").unlink()
    (plugin / "plugin.py").write_text("NAME = 'Demo'\nVERSION = '3.0'\n", encoding="utf-8")

    plugin_package.install(plugin_package.pack(plugin, tmp_path / "newer.zip"), into, replace=True)
    assert plugin_package.installed(into, "demo").version == "3.0"
    assert not (into / "demo" / "extra.py").exists(), "not merged over the top of the old one"


def test_a_broken_package_leaves_the_installed_one_alone(plugin, tmp_path, into):
    """The reason it is unpacked beside its destination and then moved."""
    plugin_package.install(plugin_package.pack(plugin, tmp_path / "demo.zip"), into)
    broken = zip_of(tmp_path / "broken.zip", {"demo/readme.txt": "no plugin in here"})
    with pytest.raises(PackageError):
        plugin_package.install(broken, into, replace=True)
    assert plugin_package.installed(into, "demo").title == "Demo"


def test_nothing_is_left_lying_about_afterwards(plugin, tmp_path, into):
    plugin_package.install(plugin_package.pack(plugin, tmp_path / "demo.zip"), into)
    assert [p.name for p in into.iterdir()] == ["demo"]


# --- and removing one -------------------------------------------------------------------------
def test_removing_takes_the_folder_with_it(plugin, tmp_path, into):
    plugin_package.install(plugin_package.pack(plugin, tmp_path / "demo.zip"), into)
    assert plugin_package.uninstall(into, "demo")
    assert not (into / "demo").exists()


def test_removing_something_that_is_not_a_plugin_does_nothing(tmp_path, into):
    (into / "notes").mkdir(parents=True)
    assert not plugin_package.uninstall(into, "notes")
    assert (into / "notes").is_dir(), "a folder that is not a plugin is not ours to delete"


# --- reading one without running it -------------------------------------------------------------
def test_what_a_plugin_says_about_itself_is_read_rather_than_executed():
    """Asked about plugins nobody has chosen -- the catalogue, and the switched
    off ones -- and the whole point of those is that the code does not run."""
    info = plugin_package.describe_source(
        "NAME = 'Demo'\nVERSION = '2.1'\nraise SystemExit('this must not happen')\n"
    )
    assert (info.title, info.version) == ("Demo", "2.1")


def test_a_plugin_file_that_will_not_parse_says_nothing_rather_than_failing():
    assert plugin_package.describe_source("def register(app)\n").title == ""
