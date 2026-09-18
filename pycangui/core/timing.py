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
#: Stamped by the launchers before they do anything, so the report can say
#: what happened before Python: the dependency check, and the interpreter
#: starting. A shell writes epoch seconds; cmd writes %TIME%, which is
#: HH:MM:SS.ss in whatever the machine's separators are.
LAUNCH_ENV = "PYCANGUI_LAUNCH_AT"
#: Stamped again by the launchers immediately before they start Python, so
#: that the script's own work and the interpreter starting are separate
#: numbers: one is fixed by doing less in the script, the other is not.
PYTHON_ENV = "PYCANGUI_PYTHON_AT"
#: Longer than this and the stamp is from an earlier run left in the
#: environment, not from the launch that is starting now.
SANE_LAUNCH_S = 600.0
REPORT_NAME = "startup-timing.txt"

#: The clock starts when this module is imported, which ``__main__`` does
#: before anything heavy. Whatever ran before that -- the interpreter itself,
#: and the launcher's dependency check -- is outside anything Python here can
#: see, so the report says so rather than pretending to a total.
_STARTED = time.perf_counter()

_marks: list[tuple[str, float]] = []
_on = FLAG in sys.argv or os.environ.get(ENV, "") not in ("", "0")


#: Seconds spent executing each top-level package's modules, when imports are
#: being watched. Exclusive of the imports a module does itself, so the totals
#: add up rather than counting the same second under three names.
_imports: dict[str, float] = {}
_depth: list[float] = []


def enabled() -> bool:
    return _on


class _TimedImports:
    """A finder that times what the real finders load.

    Put in front of ``sys.meta_path``, it lets the normal machinery find a
    module and then wraps the loader, so the cost lands under the package it
    belongs to. Time spent importing something else is subtracted, so a
    package that merely imports numpy is not blamed for numpy.
    """

    def find_spec(self, name, path=None, target=None):
        for finder in sys.meta_path:
            if finder is self or not hasattr(finder, "find_spec"):
                continue
            spec = finder.find_spec(name, path, target)
            if spec is None or spec.loader is None:
                continue
            spec.loader = _TimedLoader(spec.loader, name.split(".")[0])
            return spec
        return None


class _TimedLoader:
    def __init__(self, loader, package: str) -> None:
        self._loader = loader
        self._package = package

    def __getattr__(self, attribute):  # create_module, is_package, get_data, ...
        return getattr(self._loader, attribute)

    def exec_module(self, module) -> None:
        began = time.perf_counter()
        _depth.append(0.0)
        try:
            self._loader.exec_module(module)
        finally:
            spent_below = _depth.pop()
            mine = time.perf_counter() - began - spent_below
            _imports[self._package] = _imports.get(self._package, 0.0) + mine
            if _depth:  # tell whoever imported us not to count our time twice
                _depth[-1] += mine + spent_below


def watch_imports() -> None:
    """Start timing imports, if timing is on at all."""
    if _on:
        sys.meta_path.insert(0, _TimedImports())


def import_lines(most: int = 8) -> list[str]:
    """The packages that cost the most to import, largest first."""
    if not _imports:
        return []
    worst = sorted(_imports.items(), key=lambda pair: pair[1], reverse=True)[:most]
    lines = ["the packages that took longest to import:"]
    lines += [f"  {took:6.3f}  {package}" for package, took in worst if took >= 0.001]
    return lines


def _since(stamp: str) -> float | None:
    """Seconds since a launcher's stamp, in either shape it can take."""
    try:
        if ":" in stamp:  # cmd's %TIME%, which is a time of day
            hours, minutes, seconds = stamp.split(":")
            began = int(hours) * 3600 + int(minutes) * 60 + float(seconds.replace(",", "."))
            now = time.localtime()
            since_midnight = now.tm_hour * 3600 + now.tm_min * 60 + now.tm_sec
            since_midnight += time.time() % 1
            took = since_midnight - began
            if took < 0:  # started before midnight, running after it
                took += 86400
        else:
            took = time.time() - float(stamp.replace(",", "."))
    except (ValueError, TypeError):
        return None
    return took if 0 <= took <= SANE_LAUNCH_S else None


def launcher_seconds() -> tuple[float | None, float | None]:
    """(the script's own work, starting Python), as far as either is known."""
    began = _since(os.environ.get(LAUNCH_ENV, "").strip())
    started_python = _since(os.environ.get(PYTHON_ENV, "").strip())
    if began is None:
        return None, started_python
    if started_python is None:
        return began, None
    # The second stamp is later, so less time has passed since it.
    return max(began - started_python, 0.0), started_python


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
    lines = [
        "startup timing (seconds), from the first import in __main__",
        "(the notice line is how long it waited for you, not work):",
    ]
    # Before the clock above started, and often the largest part of the wait:
    # the launcher's own checks, then the interpreter itself starting.
    script, interpreter = launcher_seconds()
    if script is not None:
        lines.append(f"  {script:6.3f}  the launcher script (before the rest)")
    if interpreter is not None:
        lines.append(f"  {interpreter:6.3f}  starting Python (before the rest)")
    lines += [f"  {took:6.3f}  {label}" for label, took in steps]
    lines.append(f"  {_marks[-1][1] - _STARTED:6.3f}  total")
    label, took = max(steps, key=lambda step: step[1])
    lines.append(f"the longest step was {label}, at {took:.3f} s")
    return lines + import_lines()


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
