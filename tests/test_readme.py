# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""The README's badges, checked against what they claim to describe.

A badge is a fact stated somewhere nobody will think to update. The static
ones -- the Python floor and the licence -- are read back from pyproject.toml
here; the CI and release badges are fetched live from GitHub and only need to
be present.

The release badge asks for pre-releases too. Without that, a repository with
no releases reads "no releases or repo not found", which looks like a fault
rather than the plain fact that nothing is tagged yet.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")


def badge(label: str) -> str:
    """The message part of ``![label](.../badge/label-message-colour)``."""
    # Greedy, and the colour anchors the end: a message can itself contain a
    # dash, which shields writes doubled -- "Apache--2.0".
    found = re.search(
        rf"!\[{label}\]\(https://img\.shields\.io/badge/{label}-(.+)-[a-z]+\)", README
    )
    assert found, f"no {label} badge in the README"
    return found.group(1)


def test_the_python_badge_is_the_floor_the_project_declares():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    floor = pyproject["project"]["requires-python"].lstrip(">=").strip()
    assert badge("python") == f"{floor}+"


def test_the_licence_badge_is_the_licence():
    """The badge names pycangui's licence, which is the first term of the
    expression. The MIT-0 after it covers the templates copied into a
    workspace -- more permissive, so leaving it off the badge understates
    nothing anybody could rely on."""
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    main, *rest = pyproject["project"]["license"].split(" AND ")
    assert badge("license").replace("--", "-") == main
    assert rest == ["MIT-0"], "a new licence term needs a decision about the badge"


def test_the_ci_badge_is_the_live_one():
    """A public repository's Actions badge can be fetched by GitHub's image
    proxy, so a static "passing" is now the badge that could lie."""
    live = "[![CI](https://github.com/davhodg/pycangui/actions/workflows/ci.yml/badge.svg)]"
    assert live in README
    assert "img.shields.io/badge/tests-" not in README


def test_the_release_badge_is_live():
    """Live rather than a static version number: until something is tagged it
    says so, and after that it cannot disagree with the releases page."""
    live = "https://img.shields.io/github/v/release/davhodg/pycangui?include_prereleases"
    assert f"[![release]({live})](https://github.com/davhodg/pycangui/releases)" in README
    assert "img.shields.io/badge/version-" not in README
