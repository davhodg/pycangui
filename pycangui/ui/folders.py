# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Where a file dialog opens.

A dialog that always opens in the same place is a dialog you navigate out of
every single time. Windows remembers a last-used folder per *application*,
which is not much help here: an EDS, a firmware image and a captured log live
in three different places, and one shared memory means every dialog opens
where the last unrelated one left off.

So the folder is remembered per *sort of file*. Open an EDS and the next EDS
dialog starts where that one was; that has no effect on where a HEX file or a
log opens. It is kept in the workspace's settings rather than globally,
because which folder a product's files are in is a fact about that product.

The chosen file *type* is remembered the same way and for the same reason. A
dialog offering Intel HEX, S-record, raw binary and All files reopened on the
first of those every time, however many times somebody had picked another --
a small thing that happens on every single open.

Getting back to where it started: a remembered folder that no longer exists is
ignored and the default is used, and *Tools > Forget remembered folders* puts
every one of them back at once. Nothing here overrides an explicit path a
caller passes for a particular dialog.
"""

from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QWidget

from pycangui.core.context import Context

#: One per sort of file, so that opening an EDS does not move where a firmware
#: image opens. The value is only a settings key, but it is also the promise:
#: two dialogs sharing a kind share a folder, and that should be true of two
#: dialogs about the same sort of file.
EDS = "eds"  # EDS and DCF: both are device configuration
DBC = "dbc"
IMAGE = "image"  # Intel HEX, S-record, raw binary
LOG = "log"  # captured traffic, recorded and replayed
MEASUREMENT = "measurement"  # MDF/MF4: decoded signals rather than frames
A2L = "a2l"
SCRIPT = "script"
EXPORT = "export"
PLUGIN = "plugin"  # plugin packages, installed and exported
WORKSPACE = "workspace"  # workspace files, exported and imported

PREFIX = "folders."
#: The chosen file type is kept beside the folder and under the same
#: prefix, so that Forget remembered folders clears both: they are two
#: halves of "where the last one of these came from".
TYPE_SUFFIX = ".type"


def key(kind: str) -> str:
    return f"{PREFIX}{kind}"


def type_key(kind: str) -> str:
    return f"{PREFIX}{kind}{TYPE_SUFFIX}"


def remembered(ctx: Context, kind: str) -> Path | None:
    """The folder this sort of file was last used in, if it is still there."""
    stored = ctx.settings.get(key(kind))
    if not stored:
        return None
    folder = Path(str(stored))
    # A folder on a memory stick that has been unplugged, or one somebody has
    # since deleted: fall back rather than opening a dialog at nowhere.
    return folder if folder.is_dir() else None


def start_in(ctx: Context, kind: str, default: Path | str) -> Path:
    return remembered(ctx, kind) or Path(default)


def remember(ctx: Context, kind: str, chosen: str | Path, chosen_type: str = "") -> None:
    """Note where a file was picked, and which type was picked with it."""
    folder = Path(chosen).parent
    if folder.is_dir():
        ctx.settings.set(key(kind), str(folder))
    if chosen_type:
        ctx.settings.set(type_key(kind), chosen_type)


def remembered_type(ctx: Context, kind: str, offered: str) -> str:
    """The type last chosen for this sort of file, if it is still offered.

    A dialog that reopened on a type no longer in its list would show
    nothing at all, so a remembered type that has since been renamed or
    dropped is ignored and the dialog starts at the first entry as before.
    """
    stored = str(ctx.settings.get(type_key(kind), "") or "")
    return stored if stored and stored in offered.split(";;") else ""


def forget_all(ctx: Context) -> int:
    """Put every dialog back to its default folder. Returns how many moved."""
    keys = [k for k in ctx.settings.keys() if k.startswith(PREFIX)]
    for name in keys:
        ctx.settings.remove(name)
    return len(keys)


# --- the two dialogs ---------------------------------------------------------------------
def open_file(
    parent: QWidget | None,
    ctx: Context,
    kind: str,
    caption: str,
    filter: str,
    default: Path | str,
) -> str:
    """Ask for a file to read, as this sort of file was last asked for.

    The type as well as the folder: a dialog offering HEX, S-record, raw
    binary and All files reopened on the first of them every time, however
    many times somebody had picked the third.
    """
    path, chosen_type = QFileDialog.getOpenFileName(
        parent,
        caption,
        str(start_in(ctx, kind, default)),
        filter,
        remembered_type(ctx, kind, filter),
    )
    if path:
        remember(ctx, kind, path, chosen_type)
    return path


def named_for(suggested: str, file_type: str) -> str:
    """The suggested name, with the extension of the type the dialog opens on.

    Reported: the recorder remembered ASC but still suggested capture.blf, so
    the dialog opened showing a .blf name under an ASC type -- and Windows only
    swaps an extension that matches the type selected before, so changing the
    type never touched it either. A type with no extension of its own (All
    files) leaves the name as it is.
    """
    match = re.search(r"\*\.(\w+)", file_type)
    if not suggested or match is None:
        return suggested
    return Path(suggested).with_suffix("." + match.group(1)).name


def save_file(
    parent: QWidget | None,
    ctx: Context,
    kind: str,
    caption: str,
    filter: str,
    default: Path | str,
    suggested: str = "",
) -> str:
    """Ask where to write a file, as this sort of file was last written."""
    folder = start_in(ctx, kind, default)
    opens_on = remembered_type(ctx, kind, filter)
    suggested = named_for(suggested, opens_on or filter.split(";;")[0])
    path, chosen_type = QFileDialog.getSaveFileName(
        parent,
        caption,
        str(folder / suggested if suggested else folder),
        filter,
        opens_on,
    )
    if path:
        remember(ctx, kind, path, chosen_type)
    return path
