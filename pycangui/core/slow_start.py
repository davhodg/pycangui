# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Two lines after starting, when there is something to say: what Python had to
compile, and what made a slow start slow.

**Compiling.** The first start after an update is much slower than the rest:
every changed file is compiled to bytecode again. Thirteen seconds where two is
normal, once, and then it cures itself -- but somebody watching a blank screen
has no way of knowing that, and "it has got slow" is what sticks. This is not a
guess about the clock: Python records where each module's compiled copy lives,
so counting the ones written during this start says exactly what happened. It
is said whenever any file was compiled, fast start or slow, as information.

**Slow.** A start can be slow with nothing compiled, too -- the first after the
computer started, with nothing in the disk cache, or with an antivirus checking
every file. So past ``SLOW_S`` the clock is enough on its own: the start says
how long it took and what took over a second -- the steps, and inside the one
the libraries load in, the packages -- and points to *Help > Diagnostics* for
the rest, rather than putting the whole report in the Event Log.
"""

from __future__ import annotations

import sys
from pathlib import Path

#: A step or a package at least this long is named in the slow-start line.
OVER_S = 1.0
#: Seconds of work, the notice not counted, past which a start is worth
#: explaining whatever the cause. A normal start is two or three.
SLOW_S = 10.0


def is_slow(took: float) -> bool:
    return took >= SLOW_S


def compiled_this_start(since: float, modules=None) -> int:
    """How many imported modules were compiled during this start.

    ``since`` is the wall clock as the process began. A ``.pyc`` written
    after that is one this start had to make, whether because the source
    changed, the file was new, or the cache had been cleared.
    """
    written = 0
    for module in list((sys.modules if modules is None else modules).values()):
        cached = getattr(module, "__cached__", None)
        if not cached:
            continue
        try:
            if Path(cached).stat().st_mtime >= since:
                written += 1
        except OSError:  # deleted since, or on a read-only installation
            pass
    return written


def compiled_note(since: float, modules=None) -> str | None:
    """The compiling line, or None when nothing was compiled."""
    written = compiled_this_start(since, modules)
    if not written:
        return None
    files = "file" if written == 1 else "files"
    return f"Python compiled {written} changed {files}, which it only does after files change."


def message(
    took: float,
    steps: list[tuple[str, float]] | None = None,
    packages: dict[str, float] | None = None,
) -> str | None:
    """The slow-start line, or None when the start was not slow."""
    if not is_slow(took):
        return None
    said = f"Starting took {took:.1f} s."
    if slow := culprits(steps or [], packages or {}):
        said += f" Over a second: {slow}."
    return said + " Help > Diagnostics has the full breakdown."


def culprits(steps: list[tuple[str, float]], packages: dict[str, float]) -> str:
    """The steps over ``OVER_S``, longest first, with the slow packages beside
    the libraries step -- "the libraries" on its own says nothing about which
    one. Empty when no step was."""
    from pycangui.core.timing import LIBRARIES_STEP

    named = []
    for label, seconds in sorted(steps, key=lambda step: step[1], reverse=True):
        if seconds < OVER_S:
            break
        part = f"{label} {seconds:.1f} s"
        if label == LIBRARIES_STEP:
            slow = sorted(
                ((name, s) for name, s in packages.items() if s >= OVER_S),
                key=lambda package: package[1],
                reverse=True,
            )
            if slow:
                part += " (" + ", ".join(f"{name} {s:.1f} s" for name, s in slow) + ")"
        named.append(part)
    return ", ".join(named)
