# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Where the machine's own things live.

The hooks, the EDS files, the settings and the layout moved out of here and
into a workspace -- see ``core/workspaces.py`` -- because they are about the
product being worked on rather than about this computer. What is left is the
folder they all sit under and the back ends, which are about being able to
talk to a bus at all.

Windows:  %APPDATA%\\pycangui        (e.g. C:\\Users\\you\\AppData\\Roaming\\pycangui)
Linux:    $XDG_CONFIG_HOME/pycangui  (default ~/.config/pycangui)
macOS:    ~/Library/Application Support/pycangui

Set ``PYCANGUI_HOME`` to override (used by the tests).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from pycangui import APP_NAME


def made(path: Path) -> Path:
    """The folder, made if it is not there. Asked about before it is made.

    ``mkdir(exist_ok=True)`` is one call and looks cheaper than a question
    followed by a call, and on a local disc it is. On a profile redirected
    to a network share, or one a scanner is watching, creating a directory
    is the expensive operation and asking whether it exists is not: half a
    second went on two of these at startup on such a machine, and these
    functions are called all over the place.
    """
    if not path.is_dir():
        path.mkdir(parents=True, exist_ok=True)
    return path


def user_dir() -> Path:
    if override := os.environ.get("PYCANGUI_HOME"):
        base = Path(override)
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / APP_NAME
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP_NAME
    return made(base)


def backends_dir() -> Path:
    return made(user_dir() / "backends")
