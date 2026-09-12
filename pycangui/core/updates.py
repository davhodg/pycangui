# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Asking GitHub whether there is a newer release.

This runs only when the user picks Help > Check for updates.  pycangui makes
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
import urllib.request
from dataclasses import dataclass

REPO = "davhodg/pycangui"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
PROJECT_PAGE = f"https://github.com/{REPO}"
README_PAGE = f"{PROJECT_PAGE}#readme"
TIMEOUT_S = 6.0


def parse_version(text: str) -> tuple[int, ...]:
    """The numeric parts of a version, so "v1.2.0" and "1.2" compare sensibly.

    Anything that is not a number is ignored rather than guessed at: a release
    candidate suffix is not worth a version grammar of our own.
    """
    return tuple(int(part) for part in re.findall(r"\d+", text)) or (0,)


def is_newer(candidate: str, current: str) -> bool:
    a, b = parse_version(candidate), parse_version(current)
    length = max(len(a), len(b))  # 1.2 is not newer than 1.2.0
    return a + (0,) * (length - len(a)) > b + (0,) * (length - len(b))


@dataclass
class Release:
    version: str
    url: str


def latest_release(timeout: float = TIMEOUT_S) -> tuple[Release | None, str]:
    """Return (release, problem).  Exactly one of the two is meaningful.

    Every failure is reported as text rather than raised: not being able to
    reach GitHub is an ordinary thing to happen, not an error in pycangui.
    """
    request = urllib.request.Request(
        RELEASES_API,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "pycangui"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            # Also what a private repository looks like from outside.
            return None, "No releases have been published yet."
        if exc.code == 403:
            return None, "GitHub declined the request (its rate limit). Try again later."
        return None, f"GitHub returned {exc.code} {exc.reason}."
    except urllib.error.URLError as exc:
        return None, f"Could not reach GitHub: {exc.reason}"
    except (TimeoutError, OSError) as exc:
        return None, f"Could not reach GitHub: {exc}"
    except json.JSONDecodeError:
        return None, "GitHub's reply could not be read."

    tag = str(payload.get("tag_name") or "").strip()
    if not tag:
        return None, "GitHub reported a release with no version."
    return Release(version=tag.lstrip("vV"), url=payload.get("html_url") or RELEASES_PAGE), ""
