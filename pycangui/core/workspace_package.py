# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""A workspace as one file: the thing you hand to someone else.

A workspace was always meant to travel -- the hooks, the EDS files, the custom
panes and the arrangement that make sense of one product, in one folder. But
a folder cannot be attached to an email, zipping it by hand carries along
whatever else has collected in it, and "unzip this into the right place" at the
other end is an instruction people get wrong.

So *Export* writes a zip and *Import* makes a new workspace from one. A plain
zip rather than a format of our own, so that somebody can open it and see what
they are being handed before they take it. It is read by the same rules as a
plugin package, through the same code in :mod:`pycangui.core.plugin_package`:
nothing that climbs out of its folder, nothing absurdly large, and nothing that
is not what it says it is.

What an export leaves out, and why. Everything here describes *this machine*
or *this person's history* rather than the product:

* **``__pycache__``, ``.pyc`` and a version-control or editor folder** --
  bytecode compiled by whatever Python the sender had, and tools' own clutter.
  The same list a plugin package leaves out.
* **Numbered ``.bak`` backups** -- ``canopen.py.bak``, ``canopen.py.bak2``:
  what *Restore supplied files* kept of somebody's own edits. A safety net for
  the person who made them, not part of what someone else is being given.
* **A plugin install left half done** -- a ``.name.installing`` folder or a
  ``.name.packing.zip``, which exist only if pycangui stopped part way.
* **Where the file dialogs were last pointed, and the recently replayed logs**
  -- the ``folders.*`` and ``replay.recent`` keys in ``settings.json``. They
  are paths on this disc, and at the far end they point nowhere, or somewhere
  unrelated.

Where the window sits on the screen, and every "do not ask me this again"
answer, are not in the folder to begin with: both are kept in QSettings, per
machine, for exactly this reason.

Import applies the same filter, so that a zip somebody made by hand from the
folder arrives the way an exported one would. And import only ever makes a
*new* workspace: taking one in over the top of an existing one would be the one
operation here with no way back.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from pycangui.core import paths, workspaces
from pycangui.core.plugin_package import (
    SUFFIX,
    PackageError,
    normal,
    root_of,
    safe,
    screen,
    wanted,
)

FILTER = "Workspace files (*.zip);;All files (*)"

#: What makes a zip a workspace. Every workspace has one as soon as anything
#: has been changed in it, and an export writes an empty one for a workspace
#: that has not, so a zip without it was made from something else.
MARKER = "settings.json"

#: Unpacked. Far more than a workspace of hooks, EDS files and panes comes to
#: -- a plugin bringing data files with it is the large case -- and still a
#: limit, so a few kilobytes of zip cannot become a full disc.
MAX_BYTES = 256 * 1024 * 1024
#: Files in it. Generous for the same reason, and finite for the same reason:
#: a zip of a hundred thousand empty entries is small and fills a folder with
#: nothing anybody wanted.
MAX_ENTRIES = 10_000

#: The settings that are paths on this machine. ``folders.`` is the prefix
#: :mod:`pycangui.ui.folders` keeps its remembered dialog folders under.
_LOCAL_PREFIXES = ("folders.",)
_LOCAL_KEYS = frozenset({"replay.recent"})

#: ``name.bak``, ``name.bak2``, ...: the shape :meth:`Supplied.restore` keeps an
#: edited file under when it puts the shipped one back.
_BACKUP = re.compile(r"\.bak\d*$")
#: What :func:`plugin_package.install` and ``install_folder`` stage beside the
#: plugins folder, and tidy away unless pycangui is stopped part way through.
_INSTALLING = ".installing"
_PACKING = f".packing{SUFFIX}"


@dataclass(frozen=True)
class WorkspacePackage:
    """What is in a workspace file, read without extracting any of it."""

    #: The name it would be made under: the file's own name, as whoever sent
    #: it chose to call it. Not necessarily free, or even usable -- that is
    #: asked separately, because the answer depends on what is here already.
    name: str
    #: The folder inside the zip everything lives under, or "" for one whose
    #: ``settings.json`` is at the top level.
    root: str
    path: Path
    #: What will be written, relative to the new workspace folder.
    files: tuple[str, ...]

    @property
    def code(self) -> tuple[str, ...]:
        """The files that are Python: hooks, simulated nodes, plugins."""
        return tuple(name for name in self.files if name.endswith(".py"))


# --- what travels ------------------------------------------------------------------------
def shared(inside: str) -> bool:
    """Whether a file in a workspace belongs in a copy of it given to somebody else."""
    if not wanted(inside):
        return False
    parts = normal(inside).split("/")
    if _BACKUP.search(parts[-1]) or parts[-1].endswith(_PACKING):
        return False
    return not any(part.startswith(".") and part.endswith(_INSTALLING) for part in parts)


def _machine_local(key: str) -> bool:
    return key in _LOCAL_KEYS or key.startswith(_LOCAL_PREFIXES)


def shareable_settings(data: bytes) -> bytes:
    """``settings.json`` without the keys that are paths on this machine.

    Returned exactly as it was when there is nothing to take out, so that an
    export of a hand-formatted file does not come back reformatted. One that
    will not parse is carried as it is: pycangui reads a damaged settings file
    as empty anyway, and quietly replacing it would lose what somebody might
    still recover by hand.
    """
    try:
        settings = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return data
    if not isinstance(settings, dict) or not any(_machine_local(k) for k in settings):
        return data
    kept = {key: value for key, value in settings.items() if not _machine_local(key)}
    # The layout Settings.save writes, so the file reads the same as any other.
    return json.dumps(kept, indent=2, sort_keys=True).encode("utf-8")


# --- out ---------------------------------------------------------------------------------
def pack(name: str, dest: str | Path) -> Path:
    """Write a workspace out as one zip, ready to send.

    The workspace folder becomes the top level inside the zip, which is the
    shape people expect: unzipping it anywhere gives the folder rather than
    scattering its contents into wherever they were standing.
    """
    if not workspaces.exists(name):
        raise PackageError(f"There is no workspace called {name}.")
    folder = workspaces.dir_for(name)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as archive:
            # Resolved once the zip exists: saved into the workspace itself, it
            # would otherwise be found by the walk and try to contain itself.
            itself = dest.resolve()
            for item in sorted(folder.rglob("*")):
                if not item.is_file() or item.resolve() == itself:
                    continue
                inside = item.relative_to(folder).as_posix()
                if not shared(inside):
                    continue
                if inside == MARKER:
                    archive.writestr(f"{name}/{inside}", shareable_settings(item.read_bytes()))
                else:
                    archive.write(item, f"{name}/{inside}")
            if not (folder / MARKER).is_file():
                # A workspace nothing has been changed in yet has no settings
                # file, and the file is how an import knows what it has.
                archive.writestr(f"{name}/{MARKER}", "{}")
    except BaseException:
        dest.unlink(missing_ok=True)  # half a zip is worse than none
        raise
    return dest


# --- and in ------------------------------------------------------------------------------
def inspect(path: str | Path) -> WorkspacePackage:
    """What this file would make, or why it will not be imported."""
    path = Path(path)
    if not path.is_file():
        raise PackageError(f"{path} is not there.")
    if not zipfile.is_zipfile(path):
        raise PackageError(f"{path.name} is not a zip file, so it is not a pycangui workspace.")
    try:
        with zipfile.ZipFile(path) as archive:
            members = screen(archive, path, "workspace", MAX_BYTES, MAX_ENTRIES)
    except zipfile.BadZipFile as why:
        raise PackageError(f"{path.name} is damaged and cannot be read: {why}") from why
    root = root_of(path, members, MARKER, "workspace")
    files = sorted(
        {
            inside
            for member in members
            if (inside := _inside(member, root)) and not member.endswith("/") and shared(inside)
        }
    )
    return WorkspacePackage(
        name=workspaces.clean(path.stem), root=root, path=path, files=tuple(files)
    )


def _inside(member: str, root: str) -> str:
    """Where a member lands in the new workspace, or "" if it is not part of it.

    Outside the single top folder is not part of it: the ``__MACOSX`` folder
    one archiver adds beside the real contents, for one.
    """
    tidy = normal(member)
    if not root:
        return tidy
    prefix = f"{root}/"
    return tidy[len(prefix) :] if tidy.startswith(prefix) else ""


def install(path: str | Path, name: str) -> Path:
    """Make a new workspace called ``name`` from a workspace file. Returns its folder.

    Never over an existing one, whatever is asked: the name is checked here as
    well as by whoever asked for it, since a workspace of that name could have
    appeared in between. Unpacked into a folder of its own and then moved into
    place, so that a file which turns out to be broken half way through leaves
    no half-made workspace in the Switch to menu.
    """
    package = inspect(path)
    if (reason := workspaces.why_not(name)) != "":
        raise PackageError(reason)
    name = workspaces.clean(name)
    workspaces.names()  # makes the workspaces folder, migrating an older setup first
    target = workspaces.dir_for(name)

    # Beside the workspaces folder rather than in it, where a leftover would
    # be listed as a workspace; on the same disc, so the move is a rename.
    staging = Path(tempfile.mkdtemp(prefix=".importing-", dir=paths.user_dir()))
    try:
        budget = MAX_BYTES
        with zipfile.ZipFile(package.path) as archive:
            for info in archive.infolist():
                inside = _inside(info.filename, package.root)
                if info.is_dir() or not inside or not safe(info.filename) or not shared(inside):
                    continue
                dest = staging.joinpath(*inside.split("/"))
                dest.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source:
                    budget -= _copy(source, dest, budget, package.path)
                if inside == MARKER:
                    dest.write_bytes(shareable_settings(dest.read_bytes()))
        if not (staging / MARKER).is_file():
            raise PackageError(f"{package.path.name} did not unpack into a workspace.")
        shutil.move(str(staging), str(target))
    except zipfile.BadZipFile as why:
        raise PackageError(f"{package.path.name} is damaged and cannot be read: {why}") from why
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return target


def _copy(source, dest: Path, budget: int, path: Path) -> int:
    """Copy one member out, counting as it goes. Returns the bytes written.

    The sizes a zip declares were checked before anything was unpacked, and
    this is the other half: a member that turns out larger than it said is
    stopped at the limit rather than believed.
    """
    written = 0
    with dest.open("wb") as out:
        while chunk := source.read(1024 * 1024):
            written += len(chunk)
            if written > budget:
                raise PackageError(
                    f"{path.name} unpacks to more than it says it does. It has not been imported."
                )
            out.write(chunk)
    return written
