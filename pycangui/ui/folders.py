# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Where a file dialog opens.

A dialog that always opens in the same place is a dialog you navigate out of
every single time.  Windows remembers a last-used folder per *application*,
which is not much help here: an EDS, a firmware image and a captured log live
in three different places, and one shared memory means every dialog opens
where the last unrelated one left off.

So the folder is remembered per *sort of file*.  Open an EDS and the next EDS
dialog starts where that one was; that has no effect on where a HEX file or a
log opens.  It is kept in the workspace's settings rather than globally,
because which folder a product's files are in is a fact about that product.

Getting back to where it started: a remembered folder that no longer exists is
ignored and the default is used, and *Tools > Forget remembered folders* puts
every one of them back at once.  Nothing here overrides an explicit path a
caller passes for a particular dialog.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QWidget

from pycangui.core.context import Context

#: One per sort of file, so that opening an EDS does not move where a firmware
#: image opens.  The value is only a settings key, but it is also the promise:
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


def key(kind: str) -> str:
    return f"{PREFIX}{kind}"


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


def remember(ctx: Context, kind: str, chosen: str | Path) -> None:
    """Note where a file was actually picked, once one has been."""
    folder = Path(chosen).parent
    if folder.is_dir():
        ctx.settings.set(key(kind), str(folder))


def forget_all(ctx: Context) -> int:
    """Put every dialog back to its default folder.  Returns how many moved."""
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
    """Ask for a file to read, starting where this sort of file was last found."""
    path, _ = QFileDialog.getOpenFileName(
        parent, caption, str(start_in(ctx, kind, default)), filter
    )
    if path:
        remember(ctx, kind, path)
    return path


def save_file(
    parent: QWidget | None,
    ctx: Context,
    kind: str,
    caption: str,
    filter: str,
    default: Path | str,
    suggested: str = "",
) -> str:
    """Ask where to write a file, starting where this sort of file last went."""
    folder = start_in(ctx, kind, default)
    path, _ = QFileDialog.getSaveFileName(
        parent, caption, str(folder / suggested if suggested else folder), filter
    )
    if path:
        remember(ctx, kind, path)
    return path
