# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Which commit a source checkout is running, read from .git without git."""

from pycangui.core import checkout

COMMIT = "61e7129b01273ff951f28e7c0d8d28840a159792"
OLDER = "741129f18c085cb97651ddf3f28370b17e36a747"


def repo(root, head="ref: refs/heads/master", loose=None, packed=None, tags=None):
    """A .git directory with only the files this reads."""
    git = root / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text(head, encoding="utf-8")
    for ref, commit in (loose or {}).items():
        path = git / ref
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(commit + "\n", encoding="utf-8")
    if packed:
        lines = ["# pack-refs with: peeled fully-peeled sorted "]
        lines += [f"{commit} {ref}" for ref, commit in packed.items()]
        (git / "packed-refs").write_text("\n".join(lines) + "\n", encoding="utf-8")
    for name, commit in (tags or {}).items():
        path = git / "refs" / "tags" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(commit + "\n", encoding="utf-8")
    return root


def test_a_branch_and_its_commit(tmp_path):
    root = repo(tmp_path, loose={"refs/heads/master": COMMIT})
    assert checkout.describe(root) == f"master {COMMIT[:7]}"


def test_a_loose_ref_wins_over_a_packed_one(tmp_path):
    """Packing leaves the old value behind; the loose file is the current one."""
    root = repo(
        tmp_path,
        loose={"refs/heads/master": COMMIT},
        packed={"refs/heads/master": OLDER},
    )
    assert checkout.describe(root) == f"master {COMMIT[:7]}"


def test_a_branch_that_has_only_been_packed(tmp_path):
    root = repo(tmp_path, packed={"refs/heads/master": COMMIT})
    assert checkout.describe(root) == f"master {COMMIT[:7]}"


def test_a_tag_is_the_better_name_for_the_same_commit(tmp_path):
    root = repo(
        tmp_path,
        loose={"refs/heads/master": COMMIT},
        tags={"v1.2.0": COMMIT},
    )
    assert checkout.describe(root) == f"v1.2.0 {COMMIT[:7]}"


def test_a_tag_on_another_commit_is_not_claimed(tmp_path):
    root = repo(tmp_path, loose={"refs/heads/master": COMMIT}, tags={"v1.0.0": OLDER})
    assert checkout.describe(root) == f"master {COMMIT[:7]}"


def test_sitting_on_a_commit_rather_than_a_branch(tmp_path):
    root = repo(tmp_path, head=COMMIT)
    assert checkout.describe(root) == f"detached {COMMIT[:7]}"


def test_a_worktree_points_at_the_real_git_directory(tmp_path):
    """A linked worktree has a .git file rather than a directory."""
    real = repo(tmp_path / "main", loose={"refs/heads/side": COMMIT})
    (real / ".git" / "HEAD").write_text("ref: refs/heads/side", encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    (work / ".git").write_text(f"gitdir: {real / '.git'}", encoding="utf-8")

    assert checkout.describe(work) == f"side {COMMIT[:7]}"


def test_nothing_is_claimed_where_there_is_no_repository(tmp_path):
    """An installed or frozen build, which has a version number instead."""
    assert checkout.describe(tmp_path) is None


def test_an_unreadable_repository_is_not_an_error(tmp_path):
    (tmp_path / ".git").mkdir()  # no HEAD in it at all
    assert checkout.describe(tmp_path) is None


def test_a_ref_pointing_at_nothing_is_not_an_error(tmp_path):
    root = repo(tmp_path, head="ref: refs/heads/gone")
    assert checkout.describe(root) is None


def test_the_report_names_the_commit_when_run_from_a_checkout(app, tmp_path, monkeypatch):
    from pycangui.ui.help_menu import environment_report

    monkeypatch.setattr(checkout, "describe", lambda root=None: "master 1234567")
    assert "master 1234567" in environment_report()

    monkeypatch.setattr(checkout, "describe", lambda root=None: None)
    assert "Source" not in environment_report(), "an installed build has nothing to say here"
