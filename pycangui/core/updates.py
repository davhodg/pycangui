# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Asking GitHub whether there is a newer release.

This runs only when the user picks Help > Check for updates. pycangui makes
no network connection of its own accord, and this one sends nothing about the
machine it is on: it is an ordinary unauthenticated GET of a public API URL,
and nothing is downloaded or installed -- a newer version just offers to open
the releases page in a browser.

Only the standard library is used, so no dependency is added for a feature
that is used once in a while.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

REPO = "davhodg/pycangui"
API = f"https://api.github.com/repos/{REPO}"
RELEASES_API = f"{API}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
PROJECT_PAGE = f"https://github.com/{REPO}"
README_PAGE = f"{PROJECT_PAGE}#readme"
COMMITS_PAGE = f"{PROJECT_PAGE}/commits"
PYPI_API = "https://pypi.org/pypi/pycangui/json"
PYPI_PAGE = "https://pypi.org/project/pycangui/"
#: What a checkout on a branch GitHub has never heard of is compared against.
DEFAULT_BRANCH = "master"
SHORT = 7
TIMEOUT_S = 6.0


def parse_version(text: str) -> tuple[int, ...]:
    """The numeric parts of a version, so "v1.2.0" and "1.2" compare sensibly.

    Anything that is not a number is ignored rather than guessed at: a release
    candidate suffix is not worth a version grammar of our own.
    """
    return tuple(int(part) for part in re.findall(r"\d+", text)) or (0,)


def is_newer(candidate: str, current: str) -> bool:
    """Whether ``candidate`` is a later version than ``current``.

    A development build comes *before* the release it is numbered for:
    0.1.0.dev5 is on its way to 0.1.0, so 0.1.0 is newer, although its
    digits alone would say the opposite.
    """

    def key(text: str, length: int) -> tuple:
        release, dev, number = text.lower().partition("dev")
        parts = parse_version(release)
        padded = parts + (0,) * (length - len(parts))  # 1.2 is not newer than 1.2.0
        # A release sorts after every dev build of itself.
        return padded, (0, *parse_version(number)) if dev else (1,)

    length = max(len(parse_version(v.lower().partition("dev")[0])) for v in (candidate, current))
    return key(candidate, length) > key(current, length)


@dataclass
class Release:
    version: str
    url: str
    #: True for a version on PyPI, which is updated with pip rather than by
    #: downloading anything.
    pip: bool = False


def _get(url: str, timeout: float) -> tuple[dict | None, int, str]:
    """(payload, 200, "") or (None, status, problem): a 404 is left to the caller,
    since what it means depends on what was asked.

    Every failure is reported as text rather than raised: not being able to
    reach GitHub is an ordinary thing to happen, not an error in pycangui.
    """
    request = urllib.request.Request(
        url, headers={"Accept": "application/vnd.github+json", "User-Agent": "pycangui"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response), 200, ""
    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            return None, 403, "GitHub declined the request (its rate limit). Try again later."
        return None, exc.code, f"GitHub returned {exc.code} {exc.reason}."
    except urllib.error.URLError as exc:
        return None, 0, f"Could not reach GitHub: {exc.reason}"
    except (TimeoutError, OSError) as exc:
        return None, 0, f"Could not reach GitHub: {exc}"
    except json.JSONDecodeError:
        return None, 0, "GitHub's reply could not be read."


def latest_release(timeout: float = TIMEOUT_S) -> tuple[Release | None, str]:
    """Return (release, problem). Exactly one of the two is meaningful."""
    payload, status, problem = _get(RELEASES_API, timeout)
    if status == 404:
        # Also what a private repository looks like from outside.
        return None, "No releases have been published yet."
    if payload is None:
        return None, problem
    tag = str(payload.get("tag_name") or "").strip()
    if not tag:
        return None, "GitHub reported a release with no version."
    return Release(version=tag.lstrip("vV"), url=payload.get("html_url") or RELEASES_PAGE), ""


def latest_on_pypi(timeout: float = TIMEOUT_S) -> tuple[Release | None, str]:
    """Return (release, problem) for the newest version on PyPI.

    For a pip installation: the release on GitHub is the Windows installer,
    which is the wrong download for somebody who installed with pip.
    """
    payload, status, problem = _get(PYPI_API, timeout)
    if status == 404:
        return None, "pycangui is not on PyPI."
    if payload is None:
        return None, problem
    version = str((payload.get("info") or {}).get("version") or "").strip()
    if not version:
        return None, "PyPI's reply could not be read."
    return Release(version=version, url=f"{PYPI_PAGE}{version}/", pip=True), ""


@dataclass
class Standing:
    """Where a source checkout is against the same branch on GitHub."""

    branch: str
    here: str  # this checkout's commit, short
    latest: str  # GitHub's newest commit on the branch, short
    behind: int = 0  # commits GitHub has that this checkout does not
    ahead: int = 0  # commits here that GitHub does not have
    #: False when GitHub has never seen this checkout's commit: one made here
    #: and not pushed, or one from a fork.
    known: bool = True

    @property
    def url(self) -> str:
        return f"{COMMITS_PAGE}/{self.branch}"


def checkout_standing(
    branch: str | None, commit: str, timeout: float = TIMEOUT_S
) -> tuple[Standing | None, str]:
    """Return (standing, problem) for a checkout on this branch and commit.

    A release says nothing to somebody running what they pulled: between two
    releases every commit calls itself the same version. What they want to
    know is whether there is more to pull.
    """
    branch = branch or DEFAULT_BRANCH
    here = commit[:SHORT]

    def tip(name: str) -> tuple[dict | None, int, str]:
        return _get(f"{API}/commits/{urllib.parse.quote(name)}", timeout)

    payload, status, problem = tip(branch)
    if status in (404, 422) and branch != DEFAULT_BRANCH:
        # A branch made here, which GitHub has not got: the main one is what
        # there is to compare with.
        branch = DEFAULT_BRANCH
        payload, status, problem = tip(branch)
    if payload is None:
        return None, problem
    latest = str(payload.get("sha") or "")
    if not latest:
        return None, "GitHub's reply could not be read."
    if latest == commit:
        return Standing(branch, here, latest[:SHORT]), ""

    compared, status, problem = _get(f"{API}/compare/{commit}...{latest}", timeout)
    if status in (404, 422):
        return Standing(branch, here, latest[:SHORT], known=False), ""
    if compared is None:
        return None, problem
    # Compared from here to GitHub's tip: what it is ahead by is what this
    # checkout is behind by, and the other way about.
    return (
        Standing(
            branch,
            here,
            latest[:SHORT],
            behind=int(compared.get("ahead_by") or 0),
            ahead=int(compared.get("behind_by") or 0),
        ),
        "",
    )
