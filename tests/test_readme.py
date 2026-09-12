"""The README's badges, checked against what they claim to describe.

A badge is a fact stated somewhere nobody will think to update.  The two
that can be wrong rather than merely out of fashion -- the version and the
Python floor -- are read back here, so a release that moves one and not the
other fails the build instead of shipping a README that lies.

The tests badge is static because the repository is private: GitHub fetches
a README's images anonymously through its own proxy, so the Actions status
badge would render as "repo not found".  The live one is in a comment at the
top of the README, ready for the day that changes.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from pycangui import __version__

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


def test_the_version_badge_is_the_version():
    assert badge("version") == f"v{__version__}"


def test_the_python_badge_is_the_floor_the_project_declares():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    floor = pyproject["project"]["requires-python"].lstrip(">=").strip()
    assert badge("python") == f"{floor}+"


def test_the_licence_badge_is_the_licence():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert badge("license").replace("--", "-") == pyproject["project"]["license"]


def test_the_live_badges_are_recorded_for_when_the_repo_is_public():
    """Kept in a comment rather than dropped: finding the URLs again is the
    sort of small research nobody wants to repeat."""
    assert "actions/workflows/ci.yml/badge.svg" in README
    assert "img.shields.io/github/v/release" in README
