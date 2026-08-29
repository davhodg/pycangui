"""Whether .venv has everything pyproject.toml asks for.

The launchers used to set up only when .venv was missing.  That is fine until
a dependency is added: every existing checkout then starts a Python that is
short of a library, and because the launcher runs pythonw there is no console
for the ImportError to appear in.  The window simply never opens.

So the launchers ask this first, on every start.  It is deliberately cheap --
it reads installed metadata rather than importing anything, so it costs
milliseconds and cannot be fooled by a module that imports but is the wrong
version of itself.

Prints the missing distributions and exits 1, or prints nothing and exits 0.
"""

from __future__ import annotations

import sys
import tomllib
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent


def name_of(requirement: str) -> str:
    """The distribution name alone: "python-can[serial]>=4.4" -> "python-can"."""
    for separator in "[<>=!~;, ":
        requirement = requirement.split(separator)[0]
    return requirement.strip()


def wanted(requirement: str, platform: str = sys.platform) -> bool:
    """Whether this requirement applies here.

    Only ``sys_platform`` markers are understood, because that is the only
    kind pyproject.toml uses.  Anything else is treated as not applying: the
    cost of missing one is a library the launcher does not offer to install,
    while the cost of guessing the other way is a reinstall on every single
    start.
    """
    _, _, marker = requirement.partition(";")
    marker = marker.strip()
    if not marker:
        return True
    if marker.startswith("sys_platform"):
        for operator in ("==", "!="):
            if operator in marker:
                value = marker.split(operator, 1)[1].strip().strip("'\"")
                return (platform == value) if operator == "==" else (platform != value)
    return False


def missing(pyproject: Path | None = None) -> list[str]:
    """The dependencies named in pyproject.toml that are not installed here."""
    path = pyproject or PROJECT / "pyproject.toml"
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    absent = []
    for requirement in data.get("project", {}).get("dependencies", []):
        if not wanted(requirement):
            continue
        try:
            distribution(name_of(requirement))
        except PackageNotFoundError:
            absent.append(name_of(requirement))
    return absent


def main() -> int:
    absent = missing()
    if absent:
        print(" ".join(absent))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
