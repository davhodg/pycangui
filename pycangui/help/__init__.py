# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""The manual, shipped with the application: a page per topic.

Inside the package rather than beside the README so that it survives a
``pip install`` and lands in the frozen build. A Help menu that needs the
internet is worth nothing on a bench, in a workshop or on a production line,
which is where somebody is most likely to want it.

Markdown, because it is the same text the repository serves on the web and Qt
renders it directly -- ``QTextBrowser.setMarkdown`` with the GitHub dialect,
so the tables come out as tables. No web engine: the GPL-only Qt modules are
deliberately not installed, and a manual is not worth a web engine.

**A page per pane, rather than one long file.**  Nobody reads a manual; people
look one thing up in it, and a single document answers that by making them
scroll past nineteen topics they did not ask about. The pages are joined by
ordinary relative Markdown links, so the same files read as pages on the
repository's web view -- there is no navigation of ours to keep working in two
places.

The files are flat in this folder rather than in a ``pages/`` subdirectory,
because the package data is declared as one glob and a subdirectory is exactly
the kind of thing that ships in a wheel one release and not the next. The
pictures the pages show sit beside them for the same reason, and the README
uses the same files, so there is one copy of each.
"""

from __future__ import annotations

from importlib import resources

#: The front page, and the only name anything outside this module needs.
MANUAL = "manual.md"

#: Every page, the front one first. Listed rather than discovered so that a
#: build that lost one fails with a name in it: ``--selftest`` checks this list
#: against what actually shipped, and a manual quietly missing its UDS page
#: would otherwise be found by a user.
PAGES = (
    MANUAL,
    "files.md",
    "workspaces.md",
    "panes.md",
    "trace.md",
    "transmit.md",
    "signals.md",
    "event-log.md",
    "canopen.md",
    "compare.md",
    "firmware.md",
    "cia402.md",
    "uds.md",
    "j1939.md",
    "xcp.md",
    "ascii-log.md",
    "custom-panes.md",
    "console.md",
    "channels.md",
    "virtual.md",
    "hooks.md",
    "plugins.md",
    "components.md",
    "about.md",
)


def page_text(name: str) -> str:
    """One page as Markdown, or "" if this build did not ship it.

    Empty rather than raising: a missing page should cost the reader that page,
    not the application. ``pycangui --selftest`` fails on it instead, so a
    build that dropped one is caught where it can still be fixed.

    Only the pages named above are served. The name arrives from a link inside
    a document, and a document is a thing that can be edited: reading whatever
    path it asked for would turn a manual page into a way to open files.
    """
    if name not in PAGES:
        return ""
    try:
        return resources.files(__name__).joinpath(name).read_text(encoding="utf-8")
    except (OSError, ModuleNotFoundError):
        return ""


#: What a picture in the manual may be.
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif")


def image_bytes(name: str) -> bytes:
    """A picture a page shows, or b"" if it is not one this build shipped.

    The same rule as ``page_text``, for the same reason: the name comes from a
    document. Only a plain file name with an image suffix is read, from this
    package and nowhere else -- no folders, and nothing that is not a picture.
    """
    if (
        not name
        or "/" in name
        or "\\" in name
        or name.startswith(".")
        or not name.lower().endswith(IMAGE_SUFFIXES)
    ):
        return b""
    try:
        return resources.files(__name__).joinpath(name).read_bytes()
    except (OSError, ModuleNotFoundError):
        return b""


def images_in(text: str) -> list[str]:
    """Every picture a page shows, by the name it asks for."""
    import re

    return re.findall(r"!\[[^\]]*\]\(([^)\s]+)", text)


def missing_images() -> list[str]:
    """The pictures some page shows that this build cannot find."""
    return sorted(
        {name for page in PAGES for name in images_in(page_text(page)) if not image_bytes(name)}
    )


def manual_text() -> str:
    """The front page: the contents, and the way in to every other page."""
    return page_text(MANUAL)


def title_of(name: str) -> str:
    """What a page calls itself, taken from its first heading."""
    for line in page_text(name).splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return name


def all_text() -> str:
    """Every page, joined. For asking whether the manual covers something."""
    return "\n\n".join(page_text(name) for name in PAGES)


def missing_pages() -> list[str]:
    """The pages that ought to have shipped and did not."""
    return [name for name in PAGES if not page_text(name).strip()]
