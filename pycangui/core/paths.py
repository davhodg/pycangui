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


def user_dir() -> Path:
    if override := os.environ.get("PYCANGUI_HOME"):
        base = Path(override)
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / APP_NAME
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base


def backends_dir() -> Path:
    d = user_dir() / "backends"
    d.mkdir(exist_ok=True)
    return d
