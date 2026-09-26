# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Supplied files: untouched copies keep up, edited ones are never touched.

Every case is about the same question -- whose file is this now? -- asked of
a folder, a record and a shipped version that each test changes by hand.
"""

import pytest

from pycangui.core.settings import Settings
from pycangui.core.supplied import RESTORE_ENTRY, Supplied, fingerprint


@pytest.fixture
def shipped(tmp_path):
    source = tmp_path / "package" / "thing.py"
    source.parent.mkdir()
    source.write_text("VERSION = 1\n", encoding="utf-8")
    return source


@pytest.fixture
def folder(tmp_path):
    made = tmp_path / "workspace" / "hooks"
    made.mkdir(parents=True)
    return made


@pytest.fixture
def settings(tmp_path):
    return Settings(tmp_path / "workspace" / "settings.json")


def supplied(folder, shipped, settings):
    return Supplied("hooks", folder, {"thing.py": shipped}, settings)


def run(folder, shipped, settings):
    lines: list[str] = []
    outcome = supplied(folder, shipped, settings).update(lines.append)
    return outcome, lines


def test_a_missing_file_is_copied(folder, shipped, settings):
    outcome, lines = run(folder, shipped, settings)
    assert outcome.copied == ["thing.py"]
    assert (folder / "thing.py").read_text() == "VERSION = 1\n"
    assert lines == [], "a first copy is not news"


def test_an_untouched_copy_takes_the_new_version(folder, shipped, settings):
    run(folder, shipped, settings)
    shipped.write_text("VERSION = 2\n", encoding="utf-8")

    outcome, lines = run(folder, shipped, settings)
    assert outcome.updated == ["thing.py"]
    assert (folder / "thing.py").read_text() == "VERSION = 2\n"
    assert lines == ["hooks/thing.py updated to this version of pycangui's; you had not changed it"]

    shipped.write_text("VERSION = 3\n", encoding="utf-8")
    run(folder, shipped, settings)
    assert (folder / "thing.py").read_text() == "VERSION = 3\n", "and keeps up after that"


def test_an_edited_copy_is_left_and_mentioned_once_per_version(folder, shipped, settings):
    run(folder, shipped, settings)
    (folder / "thing.py").write_text("VERSION = 1  # mine\n", encoding="utf-8")
    shipped.write_text("VERSION = 2\n", encoding="utf-8")

    outcome, lines = run(folder, shipped, settings)
    assert outcome.kept == ["thing.py"] and outcome.updated == []
    assert (folder / "thing.py").read_text() == "VERSION = 1  # mine\n"
    assert len(lines) == 1 and RESTORE_ENTRY in lines[0]

    assert run(folder, shipped, settings)[1] == [], "not every start"
    shipped.write_text("VERSION = 3\n", encoding="utf-8")
    assert len(run(folder, shipped, settings)[1]) == 1, "but again for the next version"


def test_editing_an_up_to_date_copy_says_nothing(folder, shipped, settings):
    """Reported: editing a hook that was already current brought "this version
    of pycangui ships a newer one" on the next start -- when nothing newer
    shipped at all."""
    run(folder, shipped, settings)
    (folder / "thing.py").write_text("VERSION = 1  # mine\n", encoding="utf-8")

    outcome, lines = run(folder, shipped, settings)
    assert (outcome.updated, outcome.kept, lines) == ([], [], [])
    assert (folder / "thing.py").read_text() == "VERSION = 1  # mine\n", "and it is kept"

    shipped.write_text("VERSION = 2\n", encoding="utf-8")
    outcome, lines = run(folder, shipped, settings)
    assert outcome.kept == ["thing.py"] and len(lines) == 1, "once something newer does ship"


def test_it_knows_whether_a_newer_version_ships(folder, shipped, settings):
    """What the Restore dialog needs to say whether restoring brings new work
    or only undoes your own."""
    run(folder, shipped, settings)
    files = supplied(folder, shipped, settings)
    assert files.newer_ships("thing.py") is False

    (folder / "thing.py").write_text("VERSION = 1  # mine\n", encoding="utf-8")
    assert files.newer_ships("thing.py") is False, "editing it ships nothing new"

    shipped.write_text("VERSION = 2\n", encoding="utf-8")
    assert files.newer_ships("thing.py") is True


def test_with_no_record_it_cannot_say(folder, shipped, settings):
    (folder / "thing.py").write_text("VERSION = 0\n", encoding="utf-8")
    assert supplied(folder, shipped, settings).newer_ships("thing.py") is None


def test_an_edit_that_matches_what_ships_is_simply_current(folder, shipped, settings):
    run(folder, shipped, settings)
    (folder / "thing.py").write_text("VERSION = 2\n", encoding="utf-8")
    shipped.write_text("VERSION = 2\n", encoding="utf-8")
    outcome, lines = run(folder, shipped, settings)
    assert (outcome.updated, outcome.kept, lines) == ([], [], [])

    shipped.write_text("VERSION = 3\n", encoding="utf-8")
    run(folder, shipped, settings)
    assert (folder / "thing.py").read_text() == "VERSION = 3\n", "recorded as the copy"


def test_a_workspace_from_before_the_record_keeps_what_differs(folder, shipped, settings):
    """No fingerprint, so no way to tell an edit from an older version: keep it."""
    (folder / "thing.py").write_text("VERSION = 0\n", encoding="utf-8")
    outcome, lines = run(folder, shipped, settings)
    assert outcome.kept == ["thing.py"]
    assert (folder / "thing.py").read_text() == "VERSION = 0\n"
    assert "an older pycangui" in lines[0]


def test_line_endings_do_not_make_a_file_look_edited(folder, shipped, settings):
    """The same file from a Windows checkout and from a wheel."""
    run(folder, shipped, settings)
    (folder / "thing.py").write_bytes(b"VERSION = 1\r\n")
    assert supplied(folder, shipped, settings).edited() == []
    shipped.write_text("VERSION = 2\n", encoding="utf-8")
    assert run(folder, shipped, settings)[0].updated == ["thing.py"]


def test_edited_lists_what_differs_from_what_ships(folder, shipped, settings):
    files = supplied(folder, shipped, settings)
    files.update()
    assert files.edited() == []
    (folder / "thing.py").write_text("mine\n", encoding="utf-8")
    assert files.edited() == ["thing.py"]


def test_restoring_keeps_every_old_copy_and_the_result_keeps_up(folder, shipped, settings):
    files = supplied(folder, shipped, settings)
    files.update()
    (folder / "thing.py").write_text("first\n", encoding="utf-8")
    assert files.restore("thing.py").name == "thing.py.bak"
    (folder / "thing.py").write_text("second\n", encoding="utf-8")
    assert files.restore("thing.py").name == "thing.py.bak2"
    assert (folder / "thing.py.bak").read_text() == "first\n"
    assert (folder / "thing.py").read_text() == "VERSION = 1\n"

    shipped.write_text("VERSION = 2\n", encoding="utf-8")
    assert run(folder, shipped, settings)[0].updated == ["thing.py"], "restored means untouched"


def test_the_record_is_kept_per_folder(folder, shipped, settings):
    supplied(folder, shipped, settings).update()
    saved = settings.get("supplied.hooks")
    assert saved["copied"] == {"thing.py": fingerprint(b"VERSION = 1\n")}
    assert settings.get("supplied.nodes") is None
