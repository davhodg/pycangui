# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Named workspaces: one per product, and one called ``default`` nobody need meet.

A workspace is everything about *what you are working on*: the settings, the
hooks that say what this maker's objects mean, the EDS files, and the dock
layout. One folder, so it can be copied, backed up or sent to someone else
whole. What stays outside it is everything about *this machine*: the back
ends that let it talk to a bus at all, and where the window sits on the
screen.

The design constraint is that somebody with one product must never have to
know the word exists. On first run there is a workspace called ``default``,
created silently, and pycangui behaves exactly as it did before there were
any. The name appears in the title bar only when it is *not* ``default``.

There is no Save, and therefore no unsaved changes: a workspace saves
continuously, which is what ``settings.json`` has always done. The whole
surface is *Save as...* (fork what is on screen into a new name), *Switch
to*, and *Manage*.

One is active at a time, and that is not a simplification. The most
load-bearing thing a workspace holds is which channels at what bitrate, and
the tool owns one set of adapter handles: two workspaces would either share
those channels, in which case they are not independent, or want different
bitrates on the same adapter, which is a contradiction rather than a feature.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from pycangui.core import paths

#: The one every installation has. Created silently, never deleted, and never
#: named in the window title -- somebody who only ever has this one should not
#: be able to tell that workspaces were built.
DEFAULT = "default"

#: Under the user folder: the workspaces themselves, and the note saying which
#: of them is in use. The pointer is the only thing here that is not inside a
#: workspace, because it is the one question asked before there is one.
ROOT = "workspaces"
POINTER = "workspaces.json"

#: What was in the user folder before workspaces existed, and what therefore
#: moves into ``default`` the first time this runs. Anything else -- back
#: ends, a recorded log somebody left there -- belongs to the machine and is
#: left exactly where it is.
MIGRATED = ("settings.json", "hooks", "eds")

MAX_NAME = 64
#: A workspace name is a folder name, so it has to survive being one. Letters,
#: digits, space, dot, dash and underscore, starting with a letter or a digit.
_ALLOWED = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]*$")
#: Windows refuses these as filenames whatever the extension, and has since
#: DOS. A workspace called "con" would be created, apparently, and then be
#: unopenable.
_RESERVED = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)


# --- where things are -------------------------------------------------------------------
def root() -> Path:
    return paths.user_dir() / ROOT


def dir_for(name: str) -> Path:
    return root() / name


def _pointer() -> Path:
    return paths.user_dir() / POINTER


def _ensure() -> None:
    """Make sure there is somewhere to work, migrating an older setup if there is one."""
    if not root().exists():
        migrate()
    paths.made(dir_for(DEFAULT))


def names() -> list[str]:
    """Every workspace, ``default`` first and the rest by name."""
    _ensure()
    found = sorted(p.name for p in root().iterdir() if p.is_dir())
    return [DEFAULT, *(n for n in found if n != DEFAULT)]


def exists(name: str) -> bool:
    return bool(name) and dir_for(name).is_dir()


def active() -> str:
    """Which workspace is in use. ``default`` when nothing says otherwise."""
    _ensure()
    try:
        chosen = json.loads(_pointer().read_text(encoding="utf-8")).get("active")
    except (OSError, ValueError, AttributeError):
        return DEFAULT
    # A workspace deleted by hand, or a pointer typed wrong, is not a reason
    # to start with nothing: fall back to the one that is always there.
    return chosen if isinstance(chosen, str) and exists(chosen) else DEFAULT


def set_active(name: str) -> None:
    _pointer().write_text(json.dumps({"active": name}, indent=2), encoding="utf-8")


def active_dir() -> Path:
    _ensure()
    return _made(dir_for(active()))


def hooks_dir() -> Path:
    return _made(active_dir() / "hooks")


def eds_dir() -> Path:
    return _made(active_dir() / "eds")


def nodes_dir() -> Path:
    """Simulated nodes: the devices a user writes to stand in for real ones.

    Beside the hooks, and for the same reason -- a node is knowledge about a
    product, and travels with the workspace that describes that product.
    """
    return _made(active_dir() / "nodes")


def custom_panes_dir() -> Path:
    """The panes the user built. Beside the hooks, because one of these is
    knowledge about a product in exactly the way a hook is, and the two travel
    together.

    Renamed from ``panels`` once, in place: a workspace folder is something a
    person browses, so it says what the tool says.
    """
    folder = active_dir() / "custom_panes"
    older = active_dir() / "panels"
    if older.is_dir() and not folder.exists():
        older.rename(folder)
    return _made(folder)


def settings_path() -> Path:
    return active_dir() / "settings.json"


def layout_path() -> Path:
    return active_dir() / "layout.json"


def _made(path: Path) -> Path:
    return paths.made(path)


# --- naming one -------------------------------------------------------------------------
def clean(name: str) -> str:
    """The name as it will actually be used.

    Only the space around it, which nobody typed on purpose. Everything else
    is left alone and refused by name below rather than quietly repaired:
    turning "CAN 1/2" into "CAN 12" would leave somebody looking for a
    workspace that is not called what they called it.
    """
    return name.strip()


def why_not(name: str) -> str:
    """Why this name cannot be used, or "" if it can."""
    name = clean(name)
    if reason := _not_a_folder_name(name):
        return reason
    if exists(name):
        return f"There is already a workspace called {name}."
    return ""


def _not_a_folder_name(name: str) -> str:
    """Why this could never be a workspace, taken or not. "" if it could."""
    if not name:
        return "A workspace needs a name."
    if len(name) > MAX_NAME:
        return f"Names are at most {MAX_NAME} characters."
    if not _ALLOWED.match(name) or name.endswith((" ", ".")):
        return (
            "A workspace name is also a folder name: letters, digits, spaces, "
            "dots, dashes and underscores, starting with a letter or a digit."
        )
    if name.lower() in _RESERVED:
        return f"Windows will not accept a folder called {name}."
    return ""


def next_free(base: str = "workspace") -> str:
    """A name nobody has used, as close to ``base`` as it can be.

    Offered, never imposed: it is what a name dialog opens on, so the easy
    answer is one that will be accepted. A ``base`` that could never be a
    folder name -- a downloaded file called "drive (1)", say -- is not tidied
    into something that looks like it; the suggestion falls back to plain
    "workspace" and the person can type what they meant.
    """
    base = clean(base)
    if _not_a_folder_name(base):
        base = "workspace"
    if not exists(base):
        return base
    number = 2
    while True:
        suffix = f" {number}"
        # Shortened to fit rather than refused for length, which would never end.
        candidate = base[: MAX_NAME - len(suffix)].rstrip(" .") + suffix
        if why_not(candidate) == "":
            return candidate
        number += 1


# --- making, renaming, removing -----------------------------------------------------------
def create(name: str, copy_from: str | None = None) -> Path:
    """A new workspace, optionally forked from an existing one.

    Save as... forks, because what somebody means by it is "keep what I have
    and start calling it something else". An empty new one would throw away
    the arrangement they were looking at when they asked.
    """
    if (reason := why_not(name)) != "":
        raise ValueError(reason)
    name = clean(name)
    target = dir_for(name)
    if copy_from and exists(copy_from):
        shutil.copytree(dir_for(copy_from), target)
    else:
        target.mkdir(parents=True)
    return target


def rename(old: str, new: str) -> None:
    if old == DEFAULT:
        raise ValueError(f"{DEFAULT} cannot be renamed: it is the one that is always there.")
    if not exists(old):
        raise ValueError(f"There is no workspace called {old}.")
    if (reason := why_not(new)) != "":
        raise ValueError(reason)
    # Asked before the folder moves. Afterwards the pointer names something
    # that is no longer there, so active() has already fallen back to default
    # and the rename would quietly send somebody somewhere else.
    was_active = active() == old
    new = clean(new)
    dir_for(old).rename(dir_for(new))
    if was_active:
        set_active(new)


def delete(name: str) -> None:
    """Remove a workspace and everything in it.

    Not the active one: pulling the settings, hooks and layout out from under
    a running window is a worse answer than asking somebody to switch away
    first, which is one click and leaves them somewhere they chose.
    """
    if name == DEFAULT:
        raise ValueError(f"{DEFAULT} cannot be deleted: it is the one that is always there.")
    if name == active():
        raise ValueError(f"{name} is the workspace in use. Switch to another one first.")
    if not exists(name):
        raise ValueError(f"There is no workspace called {name}.")
    shutil.rmtree(dir_for(name))


# --- the setup that came before -----------------------------------------------------------
def migrate() -> list[str]:
    """Move a pre-workspace setup into ``default``, in place.

    The first thing this feature does, not the last. Done any other way it
    announces itself by losing an existing user's settings, hooks and EDS
    files, which is the opposite of the intent: somebody who never asked for
    workspaces should not be able to tell that anything happened.

    Moved rather than copied, so there is one of everything afterwards and no
    question about which copy is being read.
    """
    if root().exists():
        return []  # already done, or never needed
    home = paths.user_dir()
    target = dir_for(DEFAULT)
    target.mkdir(parents=True, exist_ok=True)
    moved = []
    for name in MIGRATED:
        source = home / name
        if source.exists():
            shutil.move(str(source), str(target / name))
            moved.append(name)
    return moved
