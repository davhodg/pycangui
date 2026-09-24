# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""The version an installer is built as, from git.

    python build/version.py          the installer's version, e.g. 0.1.0
    python build/version.py --file   the same as four numbers, e.g. 0.1.0.0

A tagged commit is built as its tag, and the tag has to agree with
``pycangui.__version__`` -- a release whose installer says one version and
whose Help > About says another is refused here rather than shipped. Any
other commit, or a tagged one with uncommitted changes, is built as
``<version>-dev-<commit>``, with ``-dirty`` when there are uncommitted
changes, so an installer that was not a release says so in its name and
says which commit it came from. Without git at all -- a
source zip -- it is ``<version>-dev``.

The second form is for the Windows version resource, which only takes
numbers.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent


class VersionError(Exception):
    pass


def package_version() -> str:
    """``__version__`` from pycangui/__init__.py, read without importing it."""
    tree = ast.parse((PROJECT / "pycangui" / "__init__.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "__version__" for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise VersionError("pycangui/__init__.py has no __version__")


def installer_version(package: str, tag: str | None, commit: str | None, dirty: bool) -> str:
    """What the installer is called, from what git says about this commit.

    A tag with uncommitted changes on top is not that release, so it is
    named as a development build like any other.
    """
    if tag and not dirty:
        tagged = tag.removeprefix("v")
        if tagged != package:
            raise VersionError(
                f"the tag is {tag} but pycangui/__init__.py says {package}: "
                f"change __version__ to {tagged}, or tag v{package}"
            )
        return package
    if not commit:
        return f"{package}-dev"
    return f"{package}-dev-{commit}" + ("-dirty" if dirty else "")


def file_version(package: str) -> str:
    """Four numbers, padded or cut, for the Windows version resource."""
    numbers = [part for part in package.split(".") if part.isdigit()][:4]
    return ".".join(numbers + ["0"] * (4 - len(numbers)))


def _git(*args: str) -> str | None:
    try:
        done = subprocess.run(
            ["git", *args], cwd=PROJECT, capture_output=True, text=True, check=False
        )
    except OSError:  # no git installed
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def this_commit() -> tuple[str | None, str | None, bool]:
    """(tag, short commit, dirty). A CI tag build says which tag it is in
    the environment, which is used first: a shallow checkout can lack it."""
    if os.environ.get("GITHUB_REF_TYPE") == "tag":
        tag = os.environ.get("GITHUB_REF_NAME")
    else:
        tag = _git("describe", "--tags", "--exact-match", "--match", "v*", "HEAD")
    commit = _git("rev-parse", "--short", "HEAD")
    dirty = bool(_git("status", "--porcelain", "--untracked-files=no"))
    return tag, commit, dirty


def main(argv: list[str]) -> int:
    try:
        package = package_version()
        if argv == ["--file"]:
            print(file_version(package))
            return 0
        print(installer_version(package, *this_commit()))
    except VersionError as exc:
        print(f"version: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
