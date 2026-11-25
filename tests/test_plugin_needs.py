# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Packages a plugin needs: REQUIRES, and a lib/ folder of its own (plugin API 3)."""

import sys

import pytest

from pycangui.core import plugin_needs
from pycangui.core.plugins import Plugins


class FakeApp:
    def remove_all(self) -> None:
        pass


def plugin(folder, name, source, lib=None):
    """A plugin folder, with ``lib`` as {relative path: text} in its lib/."""
    where = folder / name
    where.mkdir(parents=True)
    (where / "plugin.py").write_text(source, encoding="utf-8")
    for path, text in (lib or {}).items():
        target = where / "lib" / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return where


def dist_info(package, version):
    """What pip install --target leaves beside a package."""
    return {
        f"{package}-{version}.dist-info/METADATA": f"Name: {package}\nVersion: {version}\n",
        f"{package}-{version}.dist-info/top_level.txt": f"{package}\n",
        f"{package}/__init__.py": f"VERSION = {version!r}\n",
    }


USES = """
NAME = "{name}"
API_VERSION = 3
{requires}
import {package}


def register(app):
    app.version = {package}.VERSION
"""


@pytest.fixture
def plugins(tmp_path):
    loaded = Plugins(folder=tmp_path, make_app=lambda _name: FakeApp(), warn=lambda _s: None)
    yield loaded
    loaded.unload_all()


def test_what_is_missing_is_named_as_written(app):
    assert plugin_needs.missing(["python-can"]) == []
    assert plugin_needs.missing(["no-such-package-here>=1"]) == ["no-such-package-here>=1"]
    too_new = plugin_needs.missing(["python-can>=999"])
    assert len(too_new) == 1 and too_new[0].startswith("python-can>=999")
    other = "sys_platform == 'nothing'"
    assert plugin_needs.missing([f"no-such-package-here; {other}"]) == [], "not for here"


def test_a_lib_folder_says_what_it_provides(tmp_path):
    lib = tmp_path / "lib"
    for path, text in {
        **dist_info("pkg_a", "1.2"),
        "single.py": "",
        "pkg_b/__init__.py": "",
    }.items():
        (lib / path).parent.mkdir(parents=True, exist_ok=True)
        (lib / path).write_text(text, encoding="utf-8")
    assert plugin_needs.contents(lib) == {"pkg_a": "1.2", "pkg_b": None, "single": None}


def test_a_package_in_lib_is_imported_and_gone_once_unloaded(tmp_path, plugins):
    plugin(
        tmp_path,
        "carrier",
        USES.format(name="Carrier", requires="", package="lib_needs_one"),
        dist_info("lib_needs_one", "1.0"),
    )
    plugins.load_all()
    record = plugins.loaded["carrier"]
    assert record.ok, record.error
    assert record.app.version == "1.0"
    plugins.unload_all()
    assert "lib_needs_one" not in sys.modules
    assert not any(path.endswith("lib") and "carrier" in path for path in sys.path)


def test_two_versions_of_one_package_are_not_loaded_together(tmp_path, plugins):
    for name, version in (("first", "1.0"), ("second", "2.0")):
        plugin(
            tmp_path,
            name,
            USES.format(name=name, requires="", package="lib_needs_two"),
            dist_info("lib_needs_two", version),
        )
    plugins.load_all()
    first, second = plugins.loaded["first"], plugins.loaded["second"]
    assert first.ok, first.error
    assert not second.ok, "it would have been given the first one's"


def test_the_same_version_in_two_plugins_is_shared(tmp_path, plugins):
    for name in ("first", "second"):
        plugin(
            tmp_path,
            name,
            USES.format(name=name, requires="", package="lib_needs_three"),
            dist_info("lib_needs_three", "1.0"),
        )
    plugins.load_all()
    assert all(plugins.loaded[name].ok for name in ("first", "second"))


def test_a_plugin_missing_what_it_requires_is_listed_and_not_run(tmp_path, plugins):
    plugin(
        tmp_path,
        "wanting",
        'NAME = "Wanting"\nREQUIRES = ["no-such-package-here>=1"]\nraise SystemExit("ran")\n',
    )
    plugins.load_all()
    record = plugins.loaded["wanting"]
    assert not record.ok
    assert record.missing == ["no-such-package-here>=1"]


def test_what_it_requires_may_be_in_its_own_lib(tmp_path, plugins):
    plugin(
        tmp_path,
        "self_sufficient",
        USES.format(
            name="Self-sufficient",
            requires='REQUIRES = ["lib_needs_four>=1"]',
            package="lib_needs_four",
        ),
        dist_info("lib_needs_four", "1.5"),
    )
    plugins.load_all()
    record = plugins.loaded["self_sufficient"]
    assert record.ok, record.error
    assert record.missing == []
