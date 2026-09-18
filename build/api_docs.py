# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""API pages for people extending pycangui, built with pdoc.

Not the whole package. Somebody writing a hook, a simulated node or a plugin
touches a handful of modules, and a reference that also lists every Qt slot in
every pane buries those few under two hundred they will never call. The
manual (Help > Documentation) says how to extend pycangui; these pages are
where to look up exactly what an object offers once you are doing it.

The docstrings are written as prose about why, with reStructuredText literals,
and are rendered as they are -- nothing in the source is shaped for the tool.

    python build/api_docs.py        # writes dist/api-docs/index.html
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "dist" / "api-docs"

#: The extension surface: what a hook is handed, how hooks are called, what a
#: simulated node can do, and what a plugin's ``register(app)`` receives.
MODULES = (
    "pycangui.core.context",
    "pycangui.core.hooks",
    "pycangui.core.simnodes",
    "pycangui.ui.plugin_app",
)
DOCFORMAT = "restructuredtext"


def main() -> int:
    return subprocess.call(
        [
            sys.executable,
            "-m",
            "pdoc",
            "--docformat",
            DOCFORMAT,
            "--output-directory",
            str(OUT),
            *MODULES,
        ],
        cwd=ROOT,
    )


if __name__ == "__main__":
    sys.exit(main())
