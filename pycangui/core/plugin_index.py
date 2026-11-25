# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Plugins there are to install: lists of them at URLs, and the zips they name.

pycangui supplies no plugins of its own any more; each lives where its author
keeps it. What pycangui has is a list of where to find them, ``plugins/index.json``
in its repository, read when somebody presses *Check for plugins* in *Manage
plugins* -- never otherwise, as with *Check for updates*.

A list is JSON at any URL, and nothing here knows or cares which host or
version-control system serves it::

    {
      "format": 1,
      "plugins": [
        {"name": "cia402", "title": "...", "description": "...",
         "version": "2.0", "api_version": 3,
         "url": "cia402-2.0.zip", "sha256": "<64 hex digits>",
         "page": "https://..."}
      ],
      "include": ["https://example.org/more-plugins.json"]
    }

``url``, ``include`` and ``page`` may be relative to the list they are in, so a
list can sit beside its zips. ``include`` brings in other lists, which is how
pycangui's list names each plugin repository's own -- a repository with several
plugins lists several -- and how a site somebody adds by hand is read. A zip is
checked against its ``sha256`` once downloaded: a list and a zip that disagree
are refused rather than installed.

``https`` URLs only, besides local files (a path, or ``file://``) for a list
kept on disk and for the tests: a plain ``http`` list could be altered on the
way and name any zip and any checksum it liked.
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from pycangui.core import plugin_package

#: pycangui's own list, which includes each plugin repository's.
INDEX = "https://raw.githubusercontent.com/davhodg/pycangui/master/plugins/index.json"
#: The format this pycangui reads. A list of a newer format is refused, saying so.
FORMAT = 1
#: How deep includes are followed: a list including a list including a list.
MAX_DEPTH = 4
#: More than any list or plugin zip should be, so a wrong URL cannot fill the disk.
LIST_LIMIT = 1 << 20
ZIP_LIMIT = 64 << 20
TIMEOUT_S = 10.0
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


class ListError(Exception):
    """A list or a zip that could not be read, or was not what it said."""


@dataclass(frozen=True)
class Offer:
    """One plugin a list offers."""

    name: str
    version: str
    url: str
    sha256: str
    title: str = ""
    description: str = ""
    api_version: int = 1
    page: str = ""
    #: The list it came from, for saying where.
    source: str = ""

    @property
    def label(self) -> str:
        return self.title or self.name


def as_url(where: str) -> str:
    """A URL for ``where``: itself if it is one, a ``file://`` URL if it is a path."""
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]+://", where):
        return where
    return Path(where).expanduser().resolve().as_uri()


def fetch(url: str, limit: int, timeout: float = TIMEOUT_S) -> bytes:
    """The bytes at ``url``, no more than ``limit`` of them."""
    scheme = urllib.parse.urlsplit(url).scheme.lower()
    if scheme not in ("https", "file"):
        raise ListError(f"{url}: only https and local files are read")
    request = urllib.request.Request(url, headers={"User-Agent": "pycangui"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read(limit + 1)
    except urllib.error.HTTPError as exc:
        raise ListError(f"{url}: {exc.code} {exc.reason}") from None
    except urllib.error.URLError as exc:
        raise ListError(f"{url}: could not be reached ({exc.reason})") from None
    except (TimeoutError, OSError) as exc:
        raise ListError(f"{url}: could not be reached ({exc})") from None
    if len(data) > limit:
        raise ListError(f"{url}: larger than the {limit >> 20} MB a plugin is allowed")
    return data


def read(lists: list[str], get=fetch) -> tuple[list[Offer], list[str]]:
    """Every plugin the ``lists`` offer, with their includes, and what went wrong.

    A list that cannot be read is said and skipped: one site being down is no
    reason not to show the others. Where two lists offer a plugin of the same
    name, the first read is kept -- pycangui's own list comes first -- and the
    other is said, since the same name from two places is worth knowing about.
    """
    offers: dict[str, Offer] = {}
    problems: list[str] = []
    seen: set[str] = set()
    queue = [(as_url(where), 0) for where in lists]
    while queue:
        url, depth = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        try:
            listing = json.loads(get(url, LIST_LIMIT).decode("utf-8"))
        except ListError as why:
            problems.append(str(why))
            continue
        except (UnicodeDecodeError, json.JSONDecodeError):
            problems.append(f"{url}: not a plugin list (not JSON)")
            continue
        if not isinstance(listing, dict):
            problems.append(f"{url}: not a plugin list")
            continue
        if not isinstance(listing.get("format"), int) or listing["format"] > FORMAT:
            shape = listing.get("format")
            problems.append(f"{url}: a list of format {shape}; a newer pycangui reads it")
            continue
        for raw in listing.get("plugins") or []:
            try:
                offer = _offer(raw, url)
            except ListError as why:
                problems.append(f"{url}: {why}")
                continue
            if (first := offers.get(offer.name)) is not None:
                if first.source != url:
                    problems.append(
                        f"{offer.name} is offered by {first.source} and {url}; the first is used"
                    )
                continue
            offers[offer.name] = offer
        for more in listing.get("include") or []:
            if not isinstance(more, str):
                continue
            if depth + 1 >= MAX_DEPTH:
                problems.append(f"{url}: includes nested too deeply to follow further")
                continue
            queue.append((urllib.parse.urljoin(url, more), depth + 1))
    return sorted(offers.values(), key=lambda o: o.label.lower()), problems


def _offer(raw, url: str) -> Offer:
    if not isinstance(raw, dict):
        raise ListError("an entry that is not an object")
    name = raw.get("name")
    try:
        plugin_package.check_name(str(name))
    except plugin_package.PackageError:
        raise ListError(f"an entry whose name {name!r} cannot be a plugin folder") from None
    for key in ("version", "url", "sha256"):
        if not isinstance(raw.get(key), str) or not raw[key].strip():
            raise ListError(f"{name}: no {key}")
    if not _SHA256.match(raw["sha256"]):
        raise ListError(f"{name}: its sha256 is not 64 hex digits")
    api = raw.get("api_version", 1)
    page = raw.get("page")
    return Offer(
        name=str(name),
        version=raw["version"].strip(),
        url=urllib.parse.urljoin(url, raw["url"].strip()),
        sha256=raw["sha256"].lower(),
        title=str(raw.get("title") or ""),
        description=str(raw.get("description") or ""),
        api_version=api if isinstance(api, int) and not isinstance(api, bool) else 1,
        page=urllib.parse.urljoin(url, page) if isinstance(page, str) and page else "",
        source=url,
    )


def download(offer: Offer, folder: Path, get=fetch) -> Path:
    """Download an offer's zip into ``folder``, checked against its sha256."""
    data = get(offer.url, ZIP_LIMIT)
    if hashlib.sha256(data).hexdigest() != offer.sha256:
        raise ListError(
            f"{offer.url} is not the file its list describes (its sha256 differs), "
            "so it was not installed"
        )
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{offer.name}-{offer.version}{plugin_package.SUFFIX}"
    path.write_bytes(data)
    return path
