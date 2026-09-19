# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""A plugin as one file: the thing you send somebody.

Installed, a plugin is a folder -- it has to be, so that it can bring its own
modules, its own data and its own icons with it. Distributed, a folder is
useless: it cannot be attached to an email, it cannot be put on a share, and
"unzip this into the right place" is an instruction people get wrong.

So a package is a zip, and only a zip. No manifest, no metadata file, no
format of ours to learn: whatever the plugin folder contains, zipped, with
``plugin.py`` at the top of it. Anybody can make one with the file manager
they already have, and anybody can open one to see what they are about to run
before they run it -- which matters more here than anywhere else in pycangui,
because a plugin *is* code that runs with the tool.

The two directions are deliberately the same code. Installing one of the
plugins pycangui ships packs it and unpacks it exactly as though somebody had
sent it, so the path a stranger's plugin takes is the path we take every time,
rather than a rarely used branch beside a comfortable one.

What it refuses, and why:

* **A zip with no ``plugin.py``** is not a plugin; it is a zip.
* **Member names that are absolute, or that climb out with ``..``**  A zip
  that writes outside the folder it claims is the oldest trick there is, and
  this is a file somebody sent you.
* **More than one plugin in one file.**  A package installs one thing under
  one name, and a file that quietly installs three is a file whose contents
  cannot be reasoned about from its name.
* **Something far too big to be a plugin**, which is the other half of the
  same worry: a few kilobytes of zip need not become a full disc.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path

from pycangui.core.plugins import ENTRY, Info, describe_source

#: What the file dialogs offer. A plain zip rather than an extension of our
#: own: it is what everybody already has a tool for, and a package somebody
#: cannot open to look inside is a package they have to take on trust.
SUFFIX = ".zip"
FILTER = "Plugin packages (*.zip);;All files (*)"

#: A plugin's folder name is also its identity -- the key its panes are named
#: after, the module name it is imported under -- so it has to survive being
#: both. Letters, digits, dot, dash and underscore, starting with a letter or
#: a digit.
NAME_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
MAX_NAME = 64

#: Unpacked. A plugin larger than this is not a plugin, and a zip that claims
#: to be small and is not is the reason for checking before extracting rather
#: than after.
MAX_BYTES = 64 * 1024 * 1024

#: Not worth carrying, and actively unhelpful at the far end: bytecode compiled
#: against whatever Python the sender happened to have.
SKIP_DIRS = {"__pycache__", ".git", ".svn", ".idea", ".vscode"}
SKIP_SUFFIXES = {".pyc", ".pyo"}


class PackageError(Exception):
    """Why this file will not be installed. The message is shown as it is."""


@dataclass(frozen=True)
class Package:
    """What is in a package file, read without extracting any of it."""

    name: str
    info: Info
    #: The folder inside the zip everything lives under, or "" for one whose
    #: ``plugin.py`` is at the top level. Both shapes are made by hand often
    #: enough that refusing either would be refusing over a detail.
    root: str
    path: Path

    @property
    def label(self) -> str:
        return self.info.title or self.name


# --- what any zip of ours is checked for ----------------------------------------------------
# Public because a workspace file is read by the same rules (core/workspace_package.py).
# Two sets of zip checks would be two chances to get one of them wrong.
def normal(member: str) -> str:
    """A member name with the noise some zip tools add taken out.

    Leading ``./`` and doubled separators are how one archiver spells what
    another spells plainly, and a package should not be refused over which
    program made it.
    """
    return "/".join(part for part in member.replace("\\", "/").split("/") if part not in ("", "."))


def safe(member: str) -> bool:
    """Whether a member name stays inside the folder it is extracted into.

    Refused rather than repaired. Python's own ``extract`` would quietly drop
    the ``..`` and write the file somewhere else, and a package that was trying
    to escape is one whose remaining contents are not worth unpacking either.
    """
    if not member or member.startswith(("/", "\\")) or ":" in member:
        return False
    return ".." not in member.replace("\\", "/").split("/")


def wanted(member: str) -> bool:
    """Whether a file is worth carrying at all: not bytecode, not a tool's own folder."""
    parts = normal(member).split("/")
    return not (
        member.endswith("/")
        or any(part in SKIP_DIRS for part in parts)
        or Path(member).suffix in SKIP_SUFFIXES
    )


def screen(
    archive: zipfile.ZipFile,
    path: Path,
    what: str,
    max_bytes: int,
    max_entries: int | None = None,
) -> list[str]:
    """The member names, once nothing in the zip escapes and the whole is not too big.

    Everything is checked before anything is extracted, because the point is
    that a file which fails part way through has written nothing. ``what`` is
    the word the refusal uses: "plugin", "workspace".
    """
    infos = archive.infolist()
    if max_entries is not None and len(infos) > max_entries:
        raise PackageError(
            f"{path.name} holds {len(infos)} files, which is more than a {what} should."
        )
    members = [item.filename for item in infos]
    for member in members:
        if not safe(member):
            raise PackageError(
                f"{path.name} contains {member!r}, which would be written outside "
                f"the {what} folder. It has not been unpacked."
            )
    total = sum(item.file_size for item in infos)
    if total > max_bytes:
        raise PackageError(
            f"{path.name} unpacks to {total // (1024 * 1024)} MB, which is more "
            f"than a {what} should be."
        )
    return members


def root_of(path: Path, members: list[str], entry: str = ENTRY, what: str = "plugin") -> str:
    """The folder inside the zip that holds ``entry``, "" for a flat one, or a refusal.

    Both shapes are made by hand often enough that refusing either would be
    refusing over a detail: zipping a folder gives the first, selecting its
    contents and zipping those gives the second.
    """
    tidy = [normal(m) for m in members]
    flat = [m for m in tidy if m == entry]
    at_top = f"/{entry}"
    nested = sorted({m.split("/")[0] for m in tidy if m.count("/") == 1 and m.endswith(at_top)})
    if flat and not nested:
        return ""
    if len(nested) == 1 and not flat:
        return nested[0]
    if not flat and not nested:
        raise PackageError(
            f"{path.name} has no {entry} in it, at the top level or in a single "
            f"folder, so it is not a pycangui {what}."
        )
    raise PackageError(
        f"{path.name} holds more than one {what}. One file brings in one thing "
        "under one name; send them separately."
    )


def check_name(name: str) -> str:
    """The folder name to install under, or a reason it will not do."""
    name = name.strip()
    if not name:
        raise PackageError("A plugin has to have a name.")
    if len(name) > MAX_NAME:
        raise PackageError(f"{name!r} is too long for a folder name.")
    if not NAME_OK.match(name):
        raise PackageError(
            f"{name!r} will not do as a plugin name: letters, digits, dot, dash "
            "and underscore, starting with a letter or a digit."
        )
    return name


def inspect(path: str | Path) -> Package:
    """What this file would install, or why it will not be installed."""
    path = Path(path)
    if not path.is_file():
        raise PackageError(f"{path} is not there.")
    if not zipfile.is_zipfile(path):
        raise PackageError(f"{path.name} is not a zip file, so it is not a plugin package.")
    with zipfile.ZipFile(path) as archive:
        members = screen(archive, path, "plugin", MAX_BYTES)
        root = root_of(path, members)
        entry = f"{root}/{ENTRY}" if root else ENTRY
        # By its tidied name rather than the one asked for: an archive that
        # spells it "./demo/plugin.py" holds the same plugin as one that does
        # not, and reading it back by the wrong spelling would fail.
        member = next(m for m in members if normal(m) == entry)
        source = archive.read(member).decode("utf-8", errors="replace")
    name = check_name(root or path.stem)
    return Package(name=name, info=describe_source(source), root=root, path=path)


def inspect_folder(folder: Path) -> Package:
    """The same answer for a plugin that is still a folder -- one of ours.

    So that the question asked before installing a supplied plugin can name
    what is in it without packing it up first merely to look.
    """
    folder = Path(folder)
    entry = folder / ENTRY
    if not entry.is_file():
        raise PackageError(f"{folder.name} has no {ENTRY} in it, so it is not a plugin.")
    source = entry.read_text(encoding="utf-8", errors="replace")
    return Package(
        name=check_name(folder.name), info=describe_source(source), root=folder.name, path=folder
    )


def folder_fingerprint(folder: Path) -> str:
    """One hash for a plugin folder, so an edited copy can be told apart.

    Line endings are left out, as they are for the supplied hook files: the
    same file checked out on Windows and on Linux is the same file. What
    Python leaves behind is left out too -- a plugin that has merely been
    run is not a plugin somebody has changed.
    """
    parts = []
    for path in sorted(folder.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        relative = path.relative_to(folder).as_posix()
        body = hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        parts.append(f"{relative}:{body}")
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def keep_a_copy(folder: Path) -> Path:
    """Move a plugin folder aside rather than losing it. Returns where it went.

    Named with a leading underscore so that what is kept is not loaded as a
    plugin of its own: the folder is a copy of somebody's work, not a second
    plugin, and two panes calling themselves the same thing would be a
    puzzle. Never over an earlier copy, in the same way a hook file's .bak
    is never overwritten.
    """
    kept = folder.with_name(f"_{folder.name}.bak")
    number = 2
    while kept.exists():
        kept = folder.with_name(f"_{folder.name}.bak{number}")
        number += 1
    shutil.move(str(folder), str(kept))
    return kept


def installed(into: Path, name: str) -> Info | None:
    """What is installed under this name already, if anything."""
    entry = into / name / ENTRY
    if not entry.is_file():
        return None
    from pycangui.core.plugins import describe

    return describe(entry)


def install(path: str | Path, into: Path, replace: bool = False) -> Package:
    """Unpack a package into the plugins folder and say what went in.

    Unpacked beside its destination and then moved into place, so that a
    package which turns out to be broken half way through leaves the plugin
    that was there before exactly as it was. Replacing is refused unless it
    is asked for: an install that silently overwrote somebody's edited copy
    would be the one operation here with no way back.
    """
    package = inspect(path)
    into.mkdir(parents=True, exist_ok=True)
    target = into / package.name
    if target.exists() and not replace:
        raise PackageError(f"{package.name} is already installed.")

    staging = into / f".{package.name}.installing"
    shutil.rmtree(staging, ignore_errors=True)
    try:
        with zipfile.ZipFile(package.path) as archive:
            for member in archive.namelist():
                if safe(member) and wanted(member):
                    archive.extract(member, staging)
        unpacked = staging / package.root if package.root else staging
        if not (unpacked / ENTRY).is_file():
            raise PackageError(f"{Path(path).name} did not unpack into a plugin.")
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(unpacked), str(target))
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return package


def pack(folder: Path, dest: str | Path) -> Path:
    """Write an installed plugin out as a package, ready to send.

    The folder becomes the top level inside the zip, which is the shape people
    expect: unzipping it anywhere gives the plugin folder rather than scattering
    its contents into whatever directory they were standing in.
    """
    folder = Path(folder)
    if not (folder / ENTRY).is_file():
        raise PackageError(f"{folder.name} has no {ENTRY} in it, so it is not a plugin.")
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as archive:
        for item in sorted(folder.rglob("*")):
            if not item.is_file():
                continue
            inside = item.relative_to(folder).as_posix()
            if wanted(inside):
                archive.write(item, f"{folder.name}/{inside}")
    return dest


def install_folder(folder: Path, into: Path, replace: bool = False) -> Package:
    """Install a plugin that is already a folder -- one of the supplied ones.

    Packed and unpacked rather than copied, deliberately. It is two lines
    either way, and this way the supplied plugins go in through exactly the
    code a downloaded one does, so that path is exercised by everybody rather
    than only by the people it has never been tried on.
    """
    folder = Path(folder)
    staging = into / f".{folder.name}.packing{SUFFIX}"
    staging.parent.mkdir(parents=True, exist_ok=True)
    try:
        pack(folder, staging)
        return install(staging, into, replace=replace)
    finally:
        staging.unlink(missing_ok=True)


def uninstall(into: Path, name: str) -> bool:
    """Delete an installed plugin. Whatever was edited into it goes too."""
    target = into / check_name(name)
    if not (target / ENTRY).is_file():
        return False
    shutil.rmtree(target)
    return True
