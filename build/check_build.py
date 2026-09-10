"""Check a built distribution before it is wrapped in an installer.

Two things are worth failing the build over:

* a **GPL-only Qt module** having crept in.  PySide6 ships Qt Charts, Qt Data
  Visualization and friends in the same wheel as the LGPL modules, and they are
  easy to pull in by accident.  Shipping one would change the licence of the
  whole application, quietly.
* a **missing adapter backend or data file**.  python-can imports its backends
  by name, so a missing one only shows up when somebody plugs in that adapter;
  the sample device files are read from disk and a missing one breaks first run.

The import check is done by running the built executable's own ``--selftest``,
which is the only honest way to know what the bundle can import.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
DIST = PROJECT / "dist" / "pycangui"

GPL_QT_BINARIES = (
    "Qt6Charts",
    "Qt6DataVisualization",
    "Qt6Graphs",
    "Qt6VirtualKeyboard",
    "Qt6WaylandCompositor",
)

REQUIRED_FILES = (
    "pycangui/resources/demo.eds",
    "pycangui/resources/demo.dbc",
    "pycangui/resources/demo.a2l",
    "pycangui/hooks/canopen.py",
    "pycangui/nodes/canopen_device.py",
    "pycangui/nodes/gateway.py",
    "pycangui/hooks/uds.py",
    "pycangui/hooks/j1939.py",
    "pycangui/hooks/xcp.py",
    "pycangui/hooks/trace.py",
    "LICENSE",
    "NOTICE",
    "THIRD-PARTY-NOTICES.txt",
)

SELFTEST_TIMEOUT_S = 120


def main() -> int:
    if not DIST.is_dir():
        print(f"FAIL: {DIST} does not exist; run the build first")
        return 1

    problems: list[str] = []
    names = [p.name for p in DIST.rglob("*")]
    # PyInstaller 6 puts everything but the executable under _internal/
    internal = DIST / "_internal"
    payload = internal if internal.is_dir() else DIST

    for banned in GPL_QT_BINARIES:
        hits = [n for n in names if n.startswith(banned)]
        if hits:
            problems.append(
                f"GPL-only Qt module in the build: {', '.join(sorted(set(hits)))}. "
                "Exclude it in pycangui.spec -- shipping it would make the "
                "whole application GPL."
            )

    for relative in REQUIRED_FILES:
        if not (payload / relative).is_file():
            problems.append(f"missing from the build: {relative}")

    executable = DIST / "pycangui.exe"
    if not executable.is_file():
        problems.append("pycangui.exe is missing")
    else:
        result = subprocess.run(
            [str(executable), "--selftest"],
            capture_output=True,
            text=True,
            timeout=SELFTEST_TIMEOUT_S,
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            problems.append("the built application failed its self test:\n" + detail)

    size_mb = sum(p.stat().st_size for p in DIST.rglob("*") if p.is_file()) / 1e6
    if problems:
        for problem in problems:
            print(f"FAIL: {problem}")
        return 1
    print(f"build checks passed: {len(names)} files, {size_mb:.0f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
