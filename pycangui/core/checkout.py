# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Which commit a source checkout is running, for a report to name.

The version number says 0.0.1 for every build between two releases, which is
no use when somebody is running what they pulled this morning: "it does X" and
"it did X yesterday" are the same sentence about two different programs. A
checkout knows exactly what it is, and the answer is in ``.git`` for the
reading.

Read from the files rather than by running ``git``: the launcher starts
pythonw, so a subprocess would flash a console window on Windows, it would be
a process start on the way to the first window, and a frozen build has no git
to run. Reading two small files costs nothing and works the same everywhere.

Nothing here reports whether the working tree has been edited. That needs the
index compared against the files, which is what git is for; the commit is what
somebody needs to tell one build from another.
"""

from __future__ import annotations

from pathlib import Path

#: Enough of a hash to be unambiguous in any repository this size, and short
#: enough to be read out over a telephone.
SHORT = 7


def _git_dir(root: Path) -> Path | None:
    """The .git directory, which for a worktree is a file pointing at one."""
    git = root / ".git"
    if git.is_dir():
        return git
    if git.is_file():
        # A linked worktree: ".git" holds "gitdir: <path>".
        try:
            said = git.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if said.startswith("gitdir:"):
            where = Path(said.split(":", 1)[1].strip())
            if not where.is_absolute():
                where = (root / where).resolve()
            return where if where.is_dir() else None
    return None


def _packed(git: Path, ref: str) -> str | None:
    """A ref that has been packed away, which is where an old branch ends up."""
    try:
        lines = (git / "packed-refs").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        if line.startswith("#") or line.startswith("^"):
            continue
        parts = line.split()
        if len(parts) == 2 and parts[1] == ref:
            return parts[0]
    return None


def _resolve(git: Path, ref: str) -> str | None:
    """A ref's commit: the loose file first, because packed can be stale."""
    loose = git / ref
    try:
        if loose.is_file():
            return loose.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None
    return _packed(git, ref)


def _tag_for(git: Path, commit: str) -> str | None:
    """A tag pointing at this commit, if one does: that is the better name."""
    tags = git / "refs" / "tags"
    if tags.is_dir():
        for path in sorted(tags.rglob("*")):
            try:
                if path.is_file() and path.read_text(encoding="utf-8").strip() == commit:
                    return path.name
            except OSError:
                continue
    try:
        lines = (git / "packed-refs").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        parts = line.split()
        if len(parts) == 2 and parts[1].startswith("refs/tags/") and parts[0] == commit:
            return parts[1][len("refs/tags/") :]
    return None


def describe(root: Path | None = None) -> str | None:
    """The branch or tag and the short hash, or None outside a checkout.

    None is the ordinary answer for an installed or frozen build, which has
    no repository to ask and a version number that means something anyway.
    """
    root = Path(__file__).resolve().parents[2] if root is None else Path(root)
    git = _git_dir(root)
    if git is None:
        return None
    try:
        head = (git / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if head.startswith("ref:"):
        ref = head.split(":", 1)[1].strip()
        commit = _resolve(git, ref)
        name = ref.rsplit("/", 1)[-1]
    else:
        # Detached: sitting on a commit rather than following a branch,
        # which is what checking out a tag or an old commit leaves.
        commit, name = head, "detached"
    if not commit:
        return None
    return f"{_tag_for(git, commit) or name} {commit[:SHORT]}"
