"""What ships, ships.

A wheel carries the .py files whatever anyone does; everything else has to be
declared, and forgetting is silent.  `pip install pycangui` shipped without
demo.eds, demo.dbc or demo.a2l for exactly that reason -- so the "Without
hardware" walkthrough in the README could not find the files it tells you to
open -- and nobody noticed, because the launchers install editable and read
the source tree, and the frozen build copies those folders by hand in the
spec.

Two packaging paths means two places to forget.  These tests walk the package
looking for files that are not code and insist that both know about them, so
the next thing put in a resources or help folder either ships or fails here.
"""

import fnmatch
import tomllib
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parent.parent
PACKAGE = PROJECT / "pycangui"


def data_files() -> list[Path]:
    """Every file under the package that a wheel would not carry by itself."""
    return sorted(
        path
        for path in PACKAGE.rglob("*")
        if path.is_file()
        and path.suffix != ".py"
        and "__pycache__" not in path.parts
        and not path.name.endswith(".pyc")
    )


def owning_package(path: Path) -> tuple[str, str]:
    """(dotted package name, path within it) for a file inside the package.

    The owner is the nearest directory above it holding an __init__.py, which
    is what setuptools keys package-data on.
    """
    folder = path.parent
    while not (folder / "__init__.py").exists() and folder != PROJECT:
        folder = folder.parent
    dotted = ".".join(folder.relative_to(PROJECT).parts)
    return dotted, path.relative_to(folder).as_posix()


def package_data() -> dict[str, list[str]]:
    config = tomllib.loads((PROJECT / "pyproject.toml").read_text(encoding="utf-8"))
    return config["tool"]["setuptools"]["package-data"]


def test_there_is_something_to_check():
    """If this fails the walk is wrong, and the tests below pass vacuously."""
    assert data_files(), "no non-Python files found under pycangui/"


@pytest.mark.parametrize("path", data_files(), ids=lambda p: p.name)
def test_every_shipped_file_is_declared_for_the_wheel(path):
    declared = package_data()
    package, inside = owning_package(path)
    patterns = declared.get(package, [])
    assert any(fnmatch.fnmatch(inside, pattern) for pattern in patterns), (
        f"{path.relative_to(PROJECT)} would not be in a wheel: add a pattern "
        f'matching {inside!r} under [tool.setuptools.package-data] "{package}"'
    )


@pytest.mark.parametrize("path", data_files(), ids=lambda p: p.name)
def test_every_shipped_file_is_declared_for_the_frozen_build(path):
    """The installer takes a different route and has to be told separately."""
    spec = (PROJECT / "build" / "pycangui.spec").read_text(encoding="utf-8")
    folder = path.parent.relative_to(PROJECT).as_posix()
    assert folder in spec.replace("\\", "/"), (
        f"{path.relative_to(PROJECT)} would not be in the installer: add "
        f"{folder} to DATA in build/pycangui.spec"
    )


def test_the_declarations_do_not_name_folders_that_are_gone():
    """A pattern for a package that no longer exists is a lie left lying about.

    A package that does not exist *yet* is allowed: pycangui.help is declared
    ahead of the manual landing there, so that dropping the file in is all it
    takes.
    """
    for package in package_data():
        folder = PROJECT / Path(*package.split("."))
        if folder.exists():
            assert folder.is_dir(), f"{package} is declared but is not a folder"
