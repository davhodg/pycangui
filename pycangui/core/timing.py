# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Where the seconds go while pycangui starts.

Starting up is a chain of things that each look cheap: Qt, a workspace's
settings, hook files, plugins, a database or two, asking every backend what is
plugged in. When it adds up to ten seconds, guessing which one is at fault
costs more than measuring it, and the answer is different on every machine --
a large DBC here, an antivirus scanner there, a driver that takes its time
enumerating adapters somewhere else.

So the marks are always in the code and the measuring is off unless asked
for: ``pycangui --timing``, which the launchers pass through, or
``PYCANGUI_TIMING=1`` to leave it on. Off, ``mark`` is a comparison and a
return; there is no reason to make somebody rebuild anything to get a number.

The report goes to the Event Log and to ``startup-timing.txt`` in pycangui's
own folder, because the launcher starts pythonw and there is no console to
print it to.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

FLAG = "--timing"
ENV = "PYCANGUI_TIMING"
REPORT_NAME = "startup-timing.txt"

#: The clock starts when this module is imported, which ``__main__`` does
#: before anything heavy. Whatever ran before that -- the interpreter itself,
#: and the launcher's dependency check -- is outside anything Python here can
#: see, so the report says so rather than pretending to a total.
_STARTED = time.perf_counter()

_marks: list[tuple[str, float]] = []
_on = FLAG in sys.argv or os.environ.get(ENV, "") not in ("", "0")


def enabled() -> bool:
    return _on


def mark(label: str) -> None:
    """Note that this step has just finished."""
    if _on:
        _marks.append((label, time.perf_counter()))


def report_lines() -> list[str]:
    """Each step and what it cost, slowest last so the answer is visible."""
    if not _marks:
        return []
    steps = []
    previous = _STARTED
    for label, at in _marks:
        steps.append((label, at - previous))
        previous = at
    lines = ["startup timing (seconds), from the first import in __main__:"]
    lines += [f"  {took:6.3f}  {label}" for label, took in steps]
    lines.append(f"  {_marks[-1][1] - _STARTED:6.3f}  total")
    label, took = max(steps, key=lambda step: step[1])
    lines.append(f"the longest step was {label}, at {took:.3f} s")
    return lines


def write_report(folder: Path) -> Path | None:
    """Leave the report where it can be found again and pasted into a report."""
    lines = report_lines()
    if not lines:
        return None
    path = Path(folder) / REPORT_NAME
    try:
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:  # a read-only folder is not worth failing a start over
        return None
    return path
