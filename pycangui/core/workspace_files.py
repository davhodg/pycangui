# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""The files a workspace points at, and whether they travel with it.

A workspace remembers files it did not make: the CAN databases loaded into it,
the A2L, the EDS chosen by hand for a device. People keep those wherever they
keep them -- a shared drive, a project folder, a download -- and pycangui
leaves them there. But a path to somebody's download folder means nothing on
the computer an exported workspace is imported on.

So two things. A file that *is* in the workspace is written relative to it,
and so still points at the right file wherever the workspace is unpacked. And
a file that is not can be copied in -- asked when the file is chosen, and asked
again on export, where it matters -- and is otherwise left exactly where it is.

Nothing here touches Qt; the asking is in :mod:`pycangui.ui.keep_file` and the
workspace menu.
"""

from __future__ import annotations

import filecmp
import shutil
from dataclasses import dataclass
from pathlib import Path

#: The folder inside a workspace each kind of file is copied into.
EDS = "eds"
DBC = "dbc"
A2L = "a2l"

#: Where in settings.json each kind is remembered.
EDS_MAP = "canopen.eds_map"
DBC_PATHS = "dbc.paths"
A2L_PATH = "xcp.a2l"


def is_inside(path: str | Path, workspace: Path) -> bool:
    try:
        Path(path).resolve().relative_to(Path(workspace).resolve())
    except ValueError:
        return False
    return True


def stored(path: str | Path, workspace: Path) -> str:
    """How a file is written into settings.json.

    Relative to the workspace when it is in it, so that it still points at the
    right file wherever the workspace is unpacked; as it was chosen when it is
    not, because that is where its owner keeps it.
    """
    path = Path(path)
    if path.is_absolute() and is_inside(path, workspace):
        return path.resolve().relative_to(Path(workspace).resolve()).as_posix()
    return str(path)


def shown(path: str | Path, workspace: Path) -> str:
    """How a file is named in a message.

    A file in the workspace by where it is in it -- ``dbc/demo.dbc in the
    workspace`` -- because the rest of its path is the same for every file
    there and says nothing. Anywhere else, in full: where somebody keeps a
    file is exactly what they need to see to know it is the one they meant.
    """
    path = Path(path)
    if not path.is_absolute():
        path = Path(workspace) / path
    if is_inside(path, workspace):
        return f"{stored(path, workspace)} in the workspace"
    return str(path)


def resolve(value: str | Path, workspace: Path) -> Path:
    """The file a stored value means, in this workspace."""
    path = Path(value)
    return path if path.is_absolute() else Path(workspace) / path


def copy_in(path: str | Path, kind: str, workspace: Path) -> Path:
    """Copy a file into the workspace's folder for its kind. Returns the copy.

    The same file copied twice is one copy. A different file that happens to
    share a name is kept beside the first rather than over it: two products'
    ``drive.eds`` are not the same file because they are called the same.
    """
    source = Path(path)
    folder = Path(workspace) / kind
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / source.name
    number = 2
    while target.exists() and not filecmp.cmp(source, target, shallow=False):
        target = folder / f"{source.stem} ({number}){source.suffix}"
        number += 1
    if not target.exists():
        shutil.copy2(source, target)
    return target


@dataclass(frozen=True)
class Reference:
    """One file a workspace's settings point at."""

    kind: str
    #: What it is, said to a person: "CAN database", "EDS for 1:2:3".
    label: str
    value: str
    key: str
    #: For the EDS map, which device it is remembered for.
    item: str = ""


def references(settings) -> list[Reference]:
    """Every file the settings point at, forgiving a hand-edited file."""
    out: list[Reference] = []
    eds_map = settings.get(EDS_MAP, {})
    if isinstance(eds_map, dict):
        for identity, value in sorted(eds_map.items()):
            if isinstance(value, str) and value:
                out.append(Reference(EDS, f"EDS for {identity}", value, EDS_MAP, identity))
    databases = settings.get(DBC_PATHS, [])
    if isinstance(databases, list):
        out += [
            Reference(DBC, "CAN database", value, DBC_PATHS)
            for value in databases
            if isinstance(value, str) and value
        ]
    a2l = settings.get(A2L_PATH)
    if isinstance(a2l, str) and a2l:
        out.append(Reference(A2L, "A2L", a2l, A2L_PATH))
    return out


def _rewrite(settings, reference: Reference, value: str) -> None:
    if reference.key == EDS_MAP:
        eds_map = dict(settings.get(EDS_MAP, {}))
        eds_map[reference.item] = value
        settings.set(EDS_MAP, eds_map)
    elif reference.key == DBC_PATHS:
        settings.set(
            DBC_PATHS,
            [value if old == reference.value else old for old in settings.get(DBC_PATHS, [])],
        )
    else:
        settings.set(A2L_PATH, value)


def tidy(settings, workspace: Path) -> int:
    """Rewrite full paths to files inside the workspace as relative ones.

    Nothing is lost doing it, and a full path is exactly what breaks on the
    next computer, so it is done without asking. Returns how many changed.
    """
    changed = 0
    for reference in references(settings):
        if (value := stored(reference.value, workspace)) != reference.value:
            _rewrite(settings, reference, value)
            changed += 1
    return changed


def outside(settings, workspace: Path) -> list[tuple[Reference, Path]]:
    """The files the settings point at that are not in the workspace."""
    return [
        (reference, Path(reference.value))
        for reference in references(settings)
        if Path(reference.value).is_absolute() and not is_inside(reference.value, workspace)
    ]


def bring_in(settings, workspace: Path) -> tuple[list[Path], list[Path]]:
    """Copy in the files kept outside the workspace, and point the settings at the copies.

    Returns what was copied and what could not be, because it is not there any
    more. Those are left pointing where they did: a link that is broken now
    may be a drive that is not mounted, and rewriting it would lose where the
    file was.
    """
    copied: list[Path] = []
    missing: list[Path] = []
    for reference, path in outside(settings, workspace):
        if not path.is_file():
            missing.append(path)
            continue
        target = copy_in(path, reference.kind, workspace)
        _rewrite(settings, reference, stored(target, workspace))
        copied.append(target)
    return copied, missing
