# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Which packages pycangui runs on, and which versions of them are here.

For About and the diagnostics report. The list is read rather than written
down here, so it cannot fall behind: a source folder's ``pyproject.toml`` is
what the launcher installs from, and anything else -- a pip installation, or
the Windows build, which carries the metadata for this -- has the installed
package's own record of what it requires.

Nothing is imported to find a version. The metadata says, and importing some
of these (asammdf brings pandas) would take longer than the report does.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Callable
from importlib import metadata
from pathlib import Path

#: The folder pyproject.toml is in, for a source folder.
ROOT = Path(__file__).resolve().parent.parent.parent

_REQUIREMENT = re.compile(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[([^\]]*)\])?")
_EXTRA = re.compile(r"""extra\s*==\s*["']([^"']+)["']""")


class Requirement:
    """One line of a requirements list: a name, the extras asked of it, and
    the extra it belongs to, if any."""

    def __init__(self, text: str) -> None:
        spec, _, marker = text.partition(";")
        found = _REQUIREMENT.match(spec)
        if found is None:
            raise ValueError(f"not a requirement: {text!r}")
        self.name = found.group(1)
        self.extras = {e.strip() for e in (found.group(2) or "").split(",") if e.strip()}
        extra = _EXTRA.search(marker)
        self.extra = extra.group(1) if extra else None

    @property
    def key(self) -> str:
        """The name as pip compares them: PySide6_Essentials is PySide6-Essentials."""
        return normalise(self.name)


def normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def declared(root: Path = ROOT) -> list[Requirement]:
    """pycangui's own requirements: the dependencies, then the "all" extra."""
    pyproject = root / "pyproject.toml"
    try:
        project = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]
        lines = list(project["dependencies"])
        lines += [f'{line}; extra == "all"' for line in project["optional-dependencies"]["all"]]
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        lines = metadata.requires("pycangui") or []
    # Dev tools are the contributor's, not the application's.
    return [r for r in map(Requirement, lines) if r.extra in (None, "all")]


def version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def pulled_in(
    direct: list[Requirement],
    requires: Callable[[str], list[str] | None] = metadata.requires,
    have: Callable[[str], str | None] = version,
) -> list[str]:
    """What the direct requirements brought with them, by name, installed ones only.

    A requirement behind an environment marker -- Windows only, an older
    Python -- is counted when it is installed and left out when it is not,
    which answers the marker without having to evaluate it.
    """
    seen = {r.key for r in direct}
    found: dict[str, str] = {}
    queue = [r for r in direct if have(r.name)]
    while queue:
        parent = queue.pop()
        try:
            lines = requires(parent.name) or []
        except metadata.PackageNotFoundError:
            continue
        for line in lines:
            child = Requirement(line)
            if child.extra is not None and child.extra not in parent.extras:
                continue
            if child.key in seen or not have(child.name):
                continue
            seen.add(child.key)
            found[child.key] = child.name
            queue.append(child)
    return sorted(found.values(), key=str.lower)
