# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""A bundled plugin whose code changes gets a new VERSION.

A plugin is installed into a workspace as a copy, and *Update...* offers the
bundled one only when its VERSION is newer. Change the code and leave the
VERSION, and every workspace that has the plugin keeps the old code with no
way offered to get the new.

plugin_versions.json records each plugin's VERSION and a fingerprint of its
code. When the code changes this fails until the VERSION is raised and the
record updated, which running this file does:

    python tests/test_plugin_versions.py
"""

import hashlib
import json
from pathlib import Path

import pytest

from pycangui.core.plugins import supplied

RECORD = Path(__file__).with_name("plugin_versions.json")


def fingerprint(folder: Path) -> str:
    """The plugin's Python files, line endings aside, in a stable order."""
    digest = hashlib.sha256()
    for path in sorted(folder.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        digest.update(path.relative_to(folder).as_posix().encode())
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()


def current() -> dict[str, dict[str, str]]:
    return {
        plugin.name: {"version": plugin.info.version, "code": fingerprint(plugin.folder)}
        for plugin in supplied()
    }


def test_every_bundled_plugin_is_recorded():
    assert set(json.loads(RECORD.read_text())) == set(current())


@pytest.mark.parametrize("name", sorted(current()))
def test_changed_code_has_a_new_version(name):
    recorded = json.loads(RECORD.read_text()).get(name, {})
    now = current()[name]
    if now["code"] == recorded.get("code"):
        assert now["version"] == recorded.get("version"), "the record says otherwise"
        return
    assert now["version"] != recorded.get("version"), (
        f"{name}'s code changed and its VERSION is still {now['version']}: raise it, so "
        "workspaces are offered the update, then run: python tests/test_plugin_versions.py"
    )
    pytest.fail(
        f"{name} is now VERSION {now['version']}: run python tests/test_plugin_versions.py "
        "to record it, so the next change is checked against this one."
    )


if __name__ == "__main__":
    RECORD.write_text(json.dumps(current(), indent=2) + "\n", encoding="utf-8")
    print("Recorded", ", ".join(f"{name} {v['version']}" for name, v in current().items()))
