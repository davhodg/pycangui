# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""A Start menu or applications menu entry, and on Windows one on the desktop.

The installer makes its own. A clone has only the launcher, and the entry made
here points at that rather than at the Python in ``.venv``: the launcher is
what notices a dependency added since the last pull, so starting from the menu
picks up an update exactly as double-clicking the launcher does. A pip
installation has no launcher, and its entry starts the Python it is in.

Made from Tools > Settings, and only there: nothing outside pycangui's own
folder is written without being asked for. ``python -m pycangui.core.shortcut``
makes the menu entry too, and ``... shortcut desktop`` the other. Standard
library only.
"""

from __future__ import annotations

import os
import subprocess
import sys
from importlib import metadata
from pathlib import Path
from typing import NamedTuple

#: Plain, since it starts whatever was last pulled. The installer's entries
#: carry the version they install, which is how the two are told apart.
NAME = "pycangui"
#: What each system calls the menu, for the Tools entry and the messages.
MENU = {"win32": "Start menu", "linux": "applications menu"}
#: Where an entry can go. The desktop on Windows only: a Linux desktop may show
#: no files at all, and where it does, the menu entry is what is looked for.
IN_MENU, ON_DESKTOP = "menu", "desktop"


class Start(NamedTuple):
    """What an entry starts."""

    target: Path
    #: Shown while it starts: a launcher may install libraries, and says so.
    terminal: bool
    arguments: tuple[str, ...] = ()

    @property
    def folder(self) -> Path:
        """Where it is started. A launcher in its own folder; Python at home,
        since ``-m pycangui`` takes a folder of that name in front of it for
        the package, whatever is installed."""
        return Path.home() if self.arguments else self.target.parent


def root() -> Path:
    """The folder the pycangui package sits in: the clone, for a source folder."""
    return Path(__file__).resolve().parent.parent.parent


def launcher(platform: str | None = None) -> Path | None:
    """The launcher to point the entry at, or None where there is no entry to make.

    None for a frozen build, which the installer looks after, for a pip
    installation, which has no launcher beside it, and on macOS, whose menu
    is not made of files like these.
    """
    platform = sys.platform if platform is None else platform
    if getattr(sys, "frozen", False) or platform not in MENU:
        return None
    script = root() / ("pycangui.cmd" if platform == "win32" else "pycangui.sh")
    return script if script.is_file() else None


def interpreter(platform: str | None = None) -> Path | None:
    """The Python that pip installed pycangui into, or None if it did not.

    The interpreter, and not the ``pycangui.exe`` pip makes beside it: that
    program is a stub pip writes on the spot, signed by nobody, where
    ``pythonw.exe`` carries its publisher's signature -- which is what a
    machine that checks what it runs goes by. ``pythonw``, so that no console
    opens behind the window.
    """
    platform = sys.platform if platform is None else platform
    try:
        metadata.distribution("pycangui")
    except metadata.PackageNotFoundError:
        return None  # only a folder: "-m pycangui" would find nothing from elsewhere
    python = Path(sys.executable)
    if platform == "win32" and (windowed := python.with_name("pythonw.exe")).is_file():
        python = windowed
    return python


def start(platform: str | None = None) -> Start | None:
    """What an entry would start, or None where there is no entry to make:
    a frozen build, which the installer looks after, and macOS."""
    platform = sys.platform if platform is None else platform
    if getattr(sys, "frozen", False) or platform not in MENU:
        return None
    if (found := launcher(platform)) is not None:
        return Start(found, terminal=True)
    if (found := interpreter(platform)) is not None:
        return Start(found, terminal=False, arguments=("-m", "pycangui"))
    return None


def places(platform: str | None = None) -> tuple[str, ...]:
    """Where an entry can be made on this system."""
    platform = sys.platform if platform is None else platform
    return (IN_MENU, ON_DESKTOP) if platform == "win32" else (IN_MENU,)


def menu_name(platform: str | None = None) -> str:
    return MENU.get(sys.platform if platform is None else platform, "menu")


def place_name(place: str = IN_MENU, platform: str | None = None) -> str:
    return "desktop" if place == ON_DESKTOP else menu_name(platform)


def desktop_folder(environ=None) -> Path:
    """The user's desktop, which is not always ``Desktop`` in their folder:
    OneDrive moves it. Windows is asked, unless an environment was handed in,
    which is then taken at its word."""
    if environ is None and sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            buffer = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
            # CSIDL_DESKTOPDIRECTORY, the current path of it.
            if ctypes.windll.shell32.SHGetFolderPathW(None, 0x0010, None, 0, buffer) == 0:
                return Path(buffer.value)
        except (OSError, AttributeError):
            pass
    environ = os.environ if environ is None else environ
    return Path(environ.get("USERPROFILE") or Path.home()) / "Desktop"


def entry_path(platform: str | None = None, environ=None, place: str = IN_MENU) -> Path:
    """Where the entry goes: the user's own menu, so no administrator is asked."""
    platform = sys.platform if platform is None else platform
    if place == ON_DESKTOP:
        return desktop_folder(environ) / f"{NAME}.lnk"
    environ = os.environ if environ is None else environ
    if platform == "win32":
        programs = Path(environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        return programs / f"{NAME}.lnk"
    data = environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(data) / "applications" / f"{NAME}.desktop"


def _desktop_quoted(path: Path) -> str:
    """A path as an Exec argument: quoted, with the characters the spec reserves escaped."""
    text = str(path)
    for reserved in ("\\", '"', "`", "$"):
        text = text.replace(reserved, "\\" + reserved)
    return f'"{text}"'


def _resources() -> Path:
    return root() / "pycangui" / "resources"


def desktop_entry(what: Start | Path) -> str:
    """The .desktop file for a launcher, or for a pip installation.

    A launcher is run in a terminal: the first start after a pull may install
    libraries, and anything that goes wrong is said there, as it is when
    started by hand.
    """
    if isinstance(what, Path):
        what = Start(what, terminal=True)
    command = " ".join([_desktop_quoted(what.target), *what.arguments])
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={NAME}\n"
        "Comment=CAN bus tool\n"
        f"Exec={command}\n"
        f"Path={what.folder}\n"
        f"Icon={_resources() / 'pycangui.png'}\n"
        f"Terminal={'true' if what.terminal else 'false'}\n"
        "Categories=Development;Electronics;\n"
    )


def _windows_link(link: Path, what: Start) -> None:
    """Make the .lnk through the shell's own COM object, by way of PowerShell.

    The paths go in the environment rather than the command line, so no
    folder name can be mistaken for PowerShell syntax.
    """
    command = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:PYCANGUI_LINK);"
        "$s.TargetPath = $env:PYCANGUI_TARGET;"
        "$s.Arguments = $env:PYCANGUI_ARGUMENTS;"
        "$s.WorkingDirectory = $env:PYCANGUI_FOLDER;"
        "$s.IconLocation = $env:PYCANGUI_ICON;"
        "$s.Description = 'CAN bus tool, started from ' + $env:PYCANGUI_FROM;"
        "$s.Save()"
    )
    environ = dict(
        os.environ,
        PYCANGUI_LINK=str(link),
        PYCANGUI_TARGET=str(what.target),
        PYCANGUI_ARGUMENTS=subprocess.list2cmdline(what.arguments),
        PYCANGUI_FOLDER=str(what.folder),
        PYCANGUI_FROM=str(what.target.parent),
        PYCANGUI_ICON=str(_resources() / "pycangui.ico"),
    )
    done = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
        env=environ,
        capture_output=True,
        text=True,
        # pycangui runs under pythonw, and a console would flash up otherwise.
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if done.returncode != 0 or not link.is_file():
        raise OSError(done.stderr.strip() or "PowerShell could not make the shortcut.")


def create(platform: str | None = None, environ=None, place: str = IN_MENU) -> Path:
    """Make or replace the entry, and say where it is. OSError if it cannot be."""
    platform = sys.platform if platform is None else platform
    what = start(platform)
    if what is None or place not in places(platform):
        raise OSError("There is nothing here for an entry to start.")
    path = entry_path(platform, environ, place)
    path.parent.mkdir(parents=True, exist_ok=True)
    if platform == "win32":
        _windows_link(path, what)
    else:
        path.write_text(desktop_entry(what), encoding="utf-8")
        # Some desktops start only an entry that is marked executable.
        path.chmod(0o755)
    return path


def made(platform: str | None = None) -> bool:
    """Whether there is an entry already, in any of the places one can go."""
    try:
        return any(entry_path(platform, place=place).is_file() for place in places(platform))
    except (KeyError, OSError):
        return False


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    place = ON_DESKTOP if ON_DESKTOP in argv else IN_MENU
    try:
        path = create(place=place)
    except OSError as exc:
        print(f" Could not add pycangui to the {place_name(place)}: {exc}")
        return 1
    print(f" Added pycangui to the {place_name(place)}: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
