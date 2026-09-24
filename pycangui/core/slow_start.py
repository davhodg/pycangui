# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Say so when a start was slow: why, if it is known, and where the time went.

The first start after an update is much slower than the rest: every changed
file is compiled to bytecode again, and every file is read for the first
time. Thirteen seconds where two is normal, once, and then it cures itself --
but somebody watching a blank screen has no way of knowing that, and "it has
got slow" is what sticks.

This is not a guess about the clock. Python records where each module's
compiled copy lives, so counting the ones written during this start says
exactly what happened: a hundred files compiled means the update is the
reason and the next start is already quicker.

A start can be slow with nothing compiled, too -- the first after the
computer started, with nothing in the disk cache, or with an antivirus
checking every file -- and that one used to go unmentioned, which is the
start that most needs explaining. So past ``SLOW_S`` the clock is enough on
its own: the start says how long it took, and the step-by-step report
follows it whether or not ``--timing`` was asked for.
"""

from __future__ import annotations

import sys
from pathlib import Path

#: Below this, something small was compiled -- a hook file somebody edited,
#: a plugin installed this morning -- which is not what this message is for.
ENOUGH_TO_MENTION = 5
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


def message(since: float, took: float, modules=None, detailed: bool = False) -> str | None:
    """The line for the Event Log, or None when this start was an ordinary one.

    ``detailed`` is whether imports were timed package by package, which is
    only when ``--timing`` was asked for; without it, the line says how.
    """
    written = compiled_this_start(since, modules)
    if written >= ENOUGH_TO_MENTION:
        return (
            f"Starting took {took:.1f} s: Python compiled {written} changed files, which "
            "it does once after an update. The next start will be quicker."
        )
    if not is_slow(took):
        return None
    said = f"Starting took {took:.1f} s. Where the time went is below."
    if not detailed:
        said += " Starting with --timing also says which libraries took longest to load."
    return said
