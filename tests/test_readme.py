# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""The badges on the README and the PyPI page, checked against what they
claim to describe, and the classifiers PyPI shows beside them.

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

import pytest

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")
#: What PyPI shows as the project's page, which carries the same badges.
PYPI = (ROOT / "PYPI.md").read_text(encoding="utf-8")
PROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
PAGES = pytest.mark.parametrize("page", [README, PYPI], ids=["README", "PyPI"])


def badge(label: str, page: str = README) -> str:
    """The message part of ``![label](.../badge/label-message-colour)``."""
    # Greedy, and the colour anchors the end: a message can itself contain a
    # dash, which shields writes doubled -- "Apache--2.0".
    found = re.search(rf"!\[{label}\]\(https://img\.shields\.io/badge/{label}-(.+)-[a-z]+\)", page)
    assert found, f"no {label} badge on the page"
    return found.group(1)


@PAGES
def test_the_python_badge_is_the_floor_the_project_declares(page):
    floor = PROJECT["requires-python"].lstrip(">=").strip()
    assert badge("python", page) == f"{floor}+"


@PAGES
def test_the_licence_badge_is_the_licence(page):
    """The badge names pycangui's licence, which is the first term of the
    expression. The MIT-0 after it covers the templates copied into a
    workspace -- more permissive, so leaving it off the badge understates
    nothing anybody could rely on."""
    main, *rest = PROJECT["license"].split(" AND ")
    assert badge("license", page).replace("--", "-") == main
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


@PAGES
def test_both_pages_carry_the_live_badges(page):
    assert "[![CI](https://github.com/davhodg/pycangui/actions/workflows/ci.yml/badge.svg)]" in page
    assert "https://img.shields.io/pypi/v/pycangui" in page


def test_every_classifier_is_one_pypi_knows():
    """PyPI refuses an upload with a classifier it does not know, and the
    upload is the last step of a release: better found here."""
    trove = pytest.importorskip("trove_classifiers")
    unknown = [c for c in PROJECT["classifiers"] if c not in trove.classifiers]
    assert unknown == []


def test_the_python_classifiers_start_at_the_floor():
    """The versions listed are the ones requires-python allows, from its floor up."""
    floor = PROJECT["requires-python"].lstrip(">=").strip()
    listed = [
        c.rsplit(" :: ", 1)[1]
        for c in PROJECT["classifiers"]
        if re.fullmatch(r"Programming Language :: Python :: 3\.\d+", c)
    ]
    assert listed and listed[0] == floor
    assert "Programming Language :: Python :: 3 :: Only" in PROJECT["classifiers"]
