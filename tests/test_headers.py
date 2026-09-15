# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Every Python file says which licence it is under, and says the right one.

Two licences, by file.  pycangui itself is Apache-2.0 and names its copyright
holder.  The hook and simulated-node templates are MIT-0 and name nobody:
they are copied into a workspace for somebody to fill with their own seed-key
algorithm or vendor tables, and a file that is "yours to edit" should not open
with a claim that it belongs to someone else.

Checked here because a header is exactly the kind of thing a new file is
created without, and because the split is easy to get backwards.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APACHE = ("# SPDX-License-Identifier: Apache-2.0", "# SPDX-FileCopyrightText: 2026 davhodg")
MIT0 = "# SPDX-License-Identifier: MIT-0"
#: Generated, installed or somebody else's -- not ours to stamp.
SKIPPED = {".venv", ".git", "dist", "work", "lib", "__pycache__", ".pytest_cache", ".ruff_cache"}


def sources() -> list[Path]:
    found = []
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT)
        if SKIPPED & set(rel.parts):
            continue
        if any(part.endswith(".egg-info") or part.startswith("bdist.") for part in rel.parts):
            continue
        found.append(rel)
    return found


def is_template(rel: Path) -> bool:
    """A file copied into a workspace for the user to change.

    The package's own ``__init__.py`` is not copied -- the copying skips names
    that start with an underscore -- so it stays pycangui's.
    """
    return (
        len(rel.parts) == 3
        and rel.parts[0] == "pycangui"
        and rel.parts[1] in ("hooks", "nodes")
        and not rel.name.startswith("_")
    )


def head(rel: Path, lines: int = 6) -> list[str]:
    with open(ROOT / rel, encoding="utf-8") as f:
        return [f.readline().rstrip("\r\n") for _ in range(lines)]


def test_there_are_files_to_check():
    """A walk that found nothing would pass everything below."""
    assert len(sources()) > 100
    assert sum(is_template(rel) for rel in sources()) >= 10


def test_pycangui_code_is_apache_and_names_its_holder():
    wrong = [
        str(rel) for rel in sources() if not is_template(rel) and tuple(head(rel, 2)) != APACHE
    ]
    assert not wrong, f"missing or wrong Apache-2.0 header: {wrong}"


def test_templates_are_mit0_and_claim_nothing():
    wrong = []
    for rel in sources():
        if not is_template(rel):
            continue
        top = head(rel, 8)
        if top[0] != MIT0 or any("FileCopyrightText" in line for line in top):
            wrong.append(str(rel))
    assert not wrong, f"templates must be MIT-0 with no copyright line: {wrong}"


def test_every_licence_named_is_shipped_and_declared():
    assert (ROOT / "LICENSE").is_file()
    assert (ROOT / "LICENSES" / "MIT-0.txt").is_file()
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["license"] == "Apache-2.0 AND MIT-0"


def test_a_template_copied_into_a_workspace_keeps_its_header(tmp_path, monkeypatch):
    """The header is for the copy -- the one that travels without LICENSE."""
    from pycangui.core import workspaces
    from pycangui.core.context import Context
    from pycangui.core.hooks import Hooks

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    Hooks(Context(log=lambda _line: None))
    copied = (workspaces.hooks_dir() / "uds.py").read_text(encoding="utf-8")
    assert copied.startswith(MIT0)
    assert "FileCopyrightText" not in copied
