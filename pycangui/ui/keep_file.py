# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Whether a file just chosen should be copied into the workspace.

Asked, never done unasked: people keep their files where they keep them, and a
tool that quietly copies them somewhere else makes two versions of each and
leaves somebody editing the one that is no longer read. The question is only
worth asking because of export -- a copy in the workspace travels with it, and
a file elsewhere does not -- so that is what it says.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtWidgets import QMessageBox, QWidget

from pycangui.core import workspace_files
from pycangui.core.context import Context
from pycangui.ui import messages

QUESTION = "Copy {name} into the workspace?"
TEXT = (
    "{name} is in {folder}.\n\n"
    "A copy in the workspace travels with it when the workspace is exported, "
    "and a file kept elsewhere does not. No keeps using the file where it is."
)


def offer(parent: QWidget | None, ctx: Context, path: str | Path, kind: str) -> str:
    """The value to remember for a file just chosen.

    A file already in the workspace is simply written relative to it. One
    elsewhere is asked about: yes copies it in, no keeps it where it is.
    """
    workspace = ctx.workspace_dir
    source = Path(path)
    if workspace_files.is_inside(source, workspace):
        return workspace_files.stored(source, workspace)
    answer = messages.question(
        parent,
        QUESTION.format(name=source.name),
        TEXT.format(name=source.name, folder=source.parent),
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    if answer != QMessageBox.Yes:
        return str(path)
    try:
        target = workspace_files.copy_in(source, kind, workspace)
    except OSError as exc:
        messages.warning(
            parent, "The file was not copied", f"{source.name} is still used where it is: {exc}"
        )
        return str(path)
    ctx.log(f"Copied {source.name} into the workspace: {target}")
    return workspace_files.stored(target, workspace)


def offer_and_load(
    parent: QWidget | None, ctx: Context, path: str | Path, kind: str, load: Callable[[str], bool]
) -> str | None:
    """Offer the copy first, then load the file that will be used from now on.

    In that order so that a copy, once made, is the file in use: loading the
    original and remembering the copy left the two disagreeing for the rest
    of the session -- a database removed by the path it was remembered under
    stayed loaded under the one it was read from.

    Returns the value to remember, or None when it did not load. A copy made
    by this call is taken away again then, so a file that could not be read
    does not stay behind in the workspace.
    """
    workspace = ctx.workspace_dir
    folder = Path(workspace) / kind
    before = set(folder.iterdir()) if folder.is_dir() else set()
    value = offer(parent, ctx, path, kind)
    used = workspace_files.resolve(value, workspace)
    if load(str(used)):
        return value
    if used.parent == folder and used not in before:
        try:
            used.unlink()
        except OSError:
            pass
        else:
            ctx.log(f"{used.name} did not load, so the copy in the workspace was removed")
    return None
