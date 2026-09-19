# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Say so when this start had to compile Python again, and will not next time.

The first start after an update is much slower than the rest: every changed
file is compiled to bytecode again, and every file is read for the first
time. Thirteen seconds where two is normal, once, and then it cures itself --
but somebody watching a blank screen has no way of knowing that, and "it has
got slow" is what sticks.

What else is reading those files at the same time is not something this can
know. A virus scanner is the usual suspect on Windows and may well be right,
but the message says what was counted rather than what was guessed.

This is not a guess about the clock. Python records where each module's
compiled copy lives, so counting the ones written during this start says
exactly what happened: nothing compiled means nothing to explain, and a
hundred files compiled means the update is the reason and the next start is
already quicker.
"""

from __future__ import annotations

import sys
from pathlib import Path

#: Below this, something small was compiled -- a hook file somebody edited,
#: a plugin installed this morning -- which is not what this message is for.
ENOUGH_TO_MENTION = 5


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


def message(since: float, took: float, modules=None) -> str | None:
    """The line for the Event Log, or None when this start was an ordinary one."""
    written = compiled_this_start(since, modules)
    if written < ENOUGH_TO_MENTION:
        return None
    return (
        f"Starting took {took:.1f} s: Python compiled {written} changed files, which "
        "it does once after an update. The next start will be quicker."
    )
