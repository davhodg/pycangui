# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Whether .venv has everything pyproject.toml asks for.

The launchers used to set up only when .venv was missing. That is fine until
a dependency is added: every existing checkout then starts a Python that is
short of a library, and because the launcher runs pythonw there is no console
for the ImportError to appear in. The window simply never opens.

So the launchers ask this first, on every start. It is deliberately cheap --
it reads installed metadata rather than importing anything, so it costs
milliseconds and cannot be fooled by a module that imports but is the wrong
version of itself.

Prints the missing distributions and exits 1, or prints nothing and exits 0.
Before that, it takes out any distribution pycangui has replaced (``REPLACED``).
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tomllib
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent

#: Distributions pycangui used to depend on -> what replaced them under the same
#: import name. pip cannot be told that one package replaces another (it
#: ignores Obsoletes-Dist), so installing the new one beside the old leaves both
#: registered over the same files, and removing the old one later deletes the
#: new one's. So an old one is taken out before the install -- and its
#: replacement too if both are present, since the pair's files are then mixed
#: -- and the install lays the replacement down whole.
REPLACED = {"can-j1939": "python-can-j1939"}


def installed(name: str) -> bool:
    try:
        distribution(name)
    except PackageNotFoundError:
        return False
    return True


def replaced_here(is_installed: Callable[[str], bool] = installed) -> list[str]:
    """What has to be uninstalled before the install can be trusted."""
    stale = []
    for old, new in REPLACED.items():
        if is_installed(old):
            stale.append(old)
            if is_installed(new):
                stale.append(new)
    return stale


def uninstall_command(names: list[str], python: str = sys.executable) -> list[str]:
    """pip where this environment has it; uv where ``uv venv`` made it, which
    leaves pip out -- the launchers choose between the two the same way."""
    if importlib.util.find_spec("pip") is not None:
        return [python, "-m", "pip", "uninstall", "-y", *names]
    return ["uv", "pip", "uninstall", "--python", python, *names]


def name_of(requirement: str) -> str:
    """The distribution name alone: "python-can[serial]>=4.4" -> "python-can"."""
    for separator in "[<>=!~;, ":
        requirement = requirement.split(separator)[0]
    return requirement.strip()


def wanted(requirement: str, platform: str = sys.platform) -> bool:
    """Whether this requirement applies here.

    Only ``sys_platform`` markers are understood, because that is the only
    kind pyproject.toml uses. Anything else is treated as not applying: the
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
    if stale := replaced_here():
        # Quietly: what this prints is read as the list of missing packages.
        # A failure leaves the old one in place, which still starts; the
        # install that follows puts the replacement beside it.
        try:
            subprocess.run(uninstall_command(stale), capture_output=True, check=False)
        except OSError:
            pass
    absent = missing()
    if absent:
        print(" ".join(absent))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
