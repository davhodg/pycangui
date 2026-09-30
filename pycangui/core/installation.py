# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Where pycangui is running from: which Python, which environment, which copy.

For About and the diagnostics report. A machine with more than one Python --
one in Program Files, one in the user's folder, a virtual environment made
for testing -- can start pycangui from any of them, and the version numbers
alone say nothing about which. Worse, ``python -m pycangui`` run inside a
source folder imports the package from that folder whether or not the
environment has pycangui installed, and an installed copy in the same
environment is then silently not the one running.

Read from the interpreter and the package metadata, nothing imported or run.
"""

from __future__ import annotations

import json
import site
import sys
from importlib import metadata
from pathlib import Path
from urllib.parse import unquote, urlparse

import pycangui

#: The folder the running pycangui package was imported from.
PACKAGE = Path(pycangui.__file__).resolve().parent


def in_virtual_environment() -> bool:
    return sys.prefix != sys.base_prefix


def site_folders() -> list[str]:
    """Where this environment installs packages, the user's own folder included."""
    folders = list(site.getsitepackages())
    if site.ENABLE_USER_SITE:
        folders.append(site.getusersitepackages())
    return folders


def installed(folders: list[str] | None = None) -> metadata.Distribution | None:
    """pycangui as this environment has it installed, or None.

    Looked for in the environment's own folders only. The whole of sys.path
    would include the folder Python was started in, and a source folder has
    setuptools' ``pycangui.egg-info`` lying in it from a build, which is not
    an installation of anything.
    """
    found = metadata.distributions(name="pycangui", path=folders or site_folders())
    return next(iter(found), None)


def _editable_root(dist: metadata.Distribution) -> Path | None:
    """The source folder of an editable install, from pip's direct_url.json."""
    try:
        said = json.loads(dist.read_text("direct_url.json") or "{}")
    except (OSError, ValueError):
        return None
    if not said.get("dir_info", {}).get("editable"):
        return None
    url = urlparse(said.get("url", ""))
    if url.scheme != "file":
        return None
    path = unquote(url.path)
    # file:///C:/Git/pycangui parses to /C:/Git/pycangui on Windows.
    if len(path) > 2 and path[0] == "/" and path[2] == ":":
        path = path[1:]
    return Path(url.netloc + path if url.netloc else path)


#: How the running copy got here: what origin() answers.
FROZEN = "frozen"  # the Windows installer's build
PIP = "pip"  # installed with pip, and the copy running
EDITABLE = "editable"  # an editable install of a source folder, and the copy running
NOT_INSTALLED = "not installed"  # nothing in this environment: a folder's copy
OTHER_PIP = "other pip"  # a folder's copy, beside a pip install that is not running
OTHER_EDITABLE = "other editable"  # a folder's copy, beside an editable install of another


def origin(package: Path = PACKAGE) -> tuple[str, Path | None, str]:
    """(kind, the editable install's source folder, the installed version).

    The case worth catching is the running copy not being the installed one:
    started from inside a source folder, Python imports the folder, and the
    environment's own pycangui -- or the absence of one -- is not what is
    running.
    """
    if getattr(sys, "frozen", False):
        return FROZEN, None, ""
    if (dist := installed()) is None:
        return NOT_INSTALLED, None, ""
    if (root := _editable_root(dist)) is not None:
        return (EDITABLE if _same(root / "pycangui", package) else OTHER_EDITABLE), root, ""
    if _same(Path(str(dist.locate_file("pycangui"))), package):
        return PIP, None, dist.version
    return OTHER_PIP, None, dist.version


def how_installed(package: Path = PACKAGE) -> str:
    """How the running copy of pycangui got here, as a report would say it."""
    kind, root, version = origin(package)
    return {
        FROZEN: "the Windows installer's build",
        PIP: "pip",
        EDITABLE: f"pip, editable, from {root}",
        NOT_INSTALLED: "not installed in this environment: imported from the folder above",
        OTHER_PIP: (
            f"imported from the folder above, not the pip install ({version}) in this environment"
        ),
        OTHER_EDITABLE: (
            f"imported from the folder above, not the editable install of {root} "
            "in this environment"
        ),
    }[kind]


def _same(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return False


def report() -> list[tuple[str, str]]:
    """(label, value) lines: the interpreter, its environment, and the package."""
    if getattr(sys, "frozen", False):
        return [("Installed", f"{how_installed()}, in {Path(sys.executable).parent}")]
    lines = [("Interpreter", sys.executable)]
    if in_virtual_environment():
        lines.append(("Environment", f"{sys.prefix} (made from {sys.base_prefix})"))
    else:
        lines.append(("Environment", f"{sys.prefix} (no virtual environment)"))
    lines.append(("Package", str(PACKAGE)))
    lines.append(("Installed", how_installed()))
    return lines
