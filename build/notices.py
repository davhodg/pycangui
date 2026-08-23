"""Generate THIRD-PARTY-NOTICES.txt from what is actually installed.

Reading the licences out of the installed package metadata means the file
cannot drift from the build: if a dependency changes or is added, the notice
changes with it.  Run from build.cmd before packaging.
"""

from __future__ import annotations

import sys
from importlib.metadata import Distribution, distributions
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
OUTPUT = PROJECT / "THIRD-PARTY-NOTICES.txt"

#: Packages that are build or test tooling: present in the environment but not
#: shipped, so they do not belong in the notices.
NOT_SHIPPED = {
    "pycangui",
    "pyinstaller",
    "pyinstaller-hooks-contrib",
    "pytest",
    "ruff",
    "iniconfig",
    "pluggy",
    "setuptools",
    "pip",
    "altgraph",
    "pefile",
}

HEADER = """pycangui -- third party notices
===============================

pycangui is copyright 2026 davhodg and is licensed under the Apache
License 2.0; see LICENSE and NOTICE.

This distribution includes the following third party packages, each used
unmodified and under its own licence.  The LGPL licensed components are
dynamically linked and remain separately replaceable; their sources are
available from their projects.  Only LGPL Qt modules are included -- the
GPL-only Qt modules (Qt Charts, Qt Data Visualization, Qt Graphs, Qt Virtual
Keyboard) are deliberately excluded.

Adapter drivers (PCAN, Kvaser, Vector, IXXAT and others) are not distributed
with pycangui; python-can loads the vendor's own driver at run time.

"""


def licence_of(dist: Distribution) -> str:
    meta = dist.metadata
    if expression := meta.get("License-Expression"):
        return expression
    classifiers = [
        c.split("::")[-1].strip()
        for c in meta.get_all("Classifier") or []
        if c.startswith("License")
    ]
    if classifiers:
        return ", ".join(classifiers)
    text = (meta.get("License") or "").strip()
    return text.splitlines()[0] if text else "see the project"


def homepage_of(dist: Distribution) -> str:
    meta = dist.metadata
    for key in ("Home-page", "Download-URL"):
        if value := meta.get(key):
            return value
    for entry in meta.get_all("Project-URL") or []:
        label, _, url = entry.partition(",")
        if label.strip().lower() in ("homepage", "source", "repository", "source code"):
            return url.strip()
    return ""


def main() -> int:
    rows = []
    for dist in distributions():
        name = dist.metadata["Name"]
        if not name or name.lower() in NOT_SHIPPED:
            continue
        rows.append((name, dist.version, licence_of(dist), homepage_of(dist)))
    rows.sort(key=lambda r: r[0].lower())

    width = max(len(name) for name, *_ in rows)
    lines = [HEADER]
    lines.append(f"Python {sys.version.split()[0]}    PSF-2.0    https://www.python.org\n")
    for name, version, licence, home in rows:
        lines.append(f"{name:<{width}}  {version:<12}  {licence}")
        if home:
            lines.append(f"{' ' * width}  {home}")
    lines.append("")
    OUTPUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"{OUTPUT.name}: {len(rows)} packages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
