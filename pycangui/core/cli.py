# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""What pycangui was asked to do on its command line.

    pycangui [--workspace NAME] [--run SCRIPT] [--skip-start-warning] [FILE ...]

Read by hand rather than with argparse, which prints and exits on a mistake:
started from the installer's shortcut, or by pip's launcher, there is nothing
to print to, so a mistake has to come back as text for somewhere that can be
seen.

Arguments with one dash are Qt's (``-platform offscreen``, ``-style``) and are
left for it. ``--selftest`` and ``--timing`` are read where they are used.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pycangui.core import workspaces

USAGE = """pycangui [--workspace NAME] [--run SCRIPT] [--skip-start-warning] [FILE ...]

  --workspace NAME   open that workspace, this time only
  --run SCRIPT       run a Python script once the window is up, then close;
                     the exit code is the script's: 0, sys.exit(n), or 1 for
                     an exception
  --skip-start-warning  with --run only: start without the notice about
                     real equipment, for a run nobody is there to answer.
                     Every other question is still asked unless its box
                     was ticked before
  FILE               a DCF or EDS to open in the CANopen pane. Used alone,
                     it goes to the pycangui already open, if there is one."""

#: What a file on the command line can be: a double-clicked DCF or EDS.
OPENS = (".dcf", ".eds")

#: With underscores as well: both get typed, and neither is worth refusing.
SKIP_NOTICE = frozenset({"--skip-start-warning", "--skip_start_warning"})

#: Ours, and read elsewhere.
ELSEWHERE = frozenset({"--selftest", "--timing"})

#: Qt's options that take a value, so the value is not taken for a file.
QT_WITH_VALUE = frozenset(
    {
        "-platform",
        "-platformpluginpath",
        "-platformtheme",
        "-plugin",
        "-qmljsdebugger",
        "-qwindowgeometry",
        "-qwindowicon",
        "-qwindowtitle",
        "-session",
        "-style",
        "-stylesheet",
        "-display",
        "-geometry",
        "-title",
        "-name",
    }
)


@dataclass
class Options:
    workspace: str = ""
    run: Path | None = None
    files: list[Path] = field(default_factory=list)
    help: bool = False
    skip_notice: bool = False
    #: Why the command line cannot be followed; empty when it can.
    problem: str = ""

    @property
    def hand_over(self) -> bool:
        """Only files: they can go to a pycangui that is already open."""
        return bool(self.files) and not self.workspace and self.run is None


def parse(argv: list[str]) -> Options:
    """The command line, ``argv[0]`` included, as it comes in ``sys.argv``."""
    options = Options()
    args = list(argv[1:])
    while args:
        arg = args.pop(0)
        name, equals, value = arg.partition("=")
        if name in ("--workspace", "--run"):
            if not equals:
                if not args:
                    options.problem = f"{name} needs a value."
                    return options
                value = args.pop(0)
            if name == "--workspace":
                options.workspace = value
            else:
                options.run = Path(value).resolve()
        elif arg in ("--help", "-h", "/?"):
            options.help = True
        elif arg in SKIP_NOTICE:
            options.skip_notice = True
        elif arg in ELSEWHERE:
            pass
        elif arg.startswith("--"):
            options.problem = f"{arg} is not something pycangui understands."
            return options
        elif arg.startswith("-"):
            if arg in QT_WITH_VALUE and args:
                args.pop(0)
        else:
            options.files.append(Path(arg).resolve())
    options.problem = _check(options)
    return options


def _check(options: Options) -> str:
    if options.workspace and not workspaces.exists(options.workspace):
        there = ", ".join(workspaces.names())
        return f"There is no workspace called {options.workspace}. There are: {there}."
    if options.run is not None and not options.run.is_file():
        return f"There is no script at {options.run}."
    # The notice is the one thing pycangui will not let be switched off, so
    # it is skipped only for a script nobody is there to watch -- never for
    # a session somebody is about to sit in front of.
    if options.skip_notice and options.run is None:
        return "--skip-start-warning is for a run nobody is there to answer: use it with --run."
    return ""
