# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""A script given with ``--run``, and the exit code it earns.

The script sees what the console sees -- ``bus``, ``canopen``, ``window`` and
the rest -- and runs on the GUI thread like the console's *Run script...*. The
exit code is what a production line or a CI job reads: 0 when the script runs
to its end, ``sys.exit(n)``'s ``n``, and 1 for an exception or for
``sys.exit("a message")``.

What it prints goes to the terminal when there is one and to the Event Log
always: started from a shortcut, or as the installed pycangui.exe, there is no
terminal, and output nobody can see is no output.
"""

from __future__ import annotations

import contextlib
import io
import sys
import traceback
from collections.abc import Callable
from pathlib import Path


class _Said(io.TextIOBase):
    """Written text, to the terminal as it comes and to ``say`` a line at a time."""

    def __init__(self, terminal, say: Callable[[str], None]) -> None:
        super().__init__()
        self._terminal = terminal
        self._say = say
        self._partial = ""
        self._saying = False

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        if self._terminal is not None:
            with contextlib.suppress(OSError, ValueError):
                self._terminal.write(text)
                self._terminal.flush()
        if self._saying:  # ``say`` printed: it is on the terminal already
            return len(text)
        *lines, self._partial = (self._partial + text).split("\n")
        self._saying = True
        try:
            for line in lines:
                self._say(line)
        finally:
            self._saying = False
        return len(text)

    def finish(self) -> None:
        if self._partial:
            self._say(self._partial)
            self._partial = ""


def exit_code(value) -> int:
    """What ``sys.exit(value)`` means as a process's exit code, as Python has it."""
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    print(value, file=sys.stderr)
    return 1


def run(
    path: Path,
    namespace: dict,
    say: Callable[[str], None],
    complain: Callable[[str], None],
) -> int:
    """Run the script with these names; ``say`` and ``complain`` get its output
    and its errors. Returns the exit code."""
    out = _Said(sys.stdout, say)
    err = _Said(sys.stderr, complain)
    names = {**namespace, "__name__": "__main__", "__file__": str(path)}
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        complain(f"cannot read {path}: {exc}")
        return 1
    code = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            exec(compile(source, str(path), "exec"), names)
        except SystemExit as exc:
            code = exit_code(exc.code)
        except Exception:
            kind, value, tb = sys.exc_info()
            # The first entry is this function's own call into the script.
            sys.stderr.write(
                "".join(traceback.format_exception(kind, value, tb.tb_next if tb else None))
            )
            code = 1
    out.finish()
    err.finish()
    return code
