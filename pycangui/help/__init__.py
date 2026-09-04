"""The manual, shipped with the application.

Inside the package rather than beside the README so that it survives a
``pip install`` and lands in the frozen build.  A Help menu that needs the
internet is worth nothing on a bench, in a workshop or on a production line,
which is where somebody is most likely to want it.

Markdown, because it is the same text the repository serves on the web and Qt
renders it directly -- ``QTextBrowser.setMarkdown`` with the GitHub dialect,
so the tables come out as tables.  No web engine: the GPL-only Qt modules are
deliberately not installed, and a manual is not worth 160 MB.
"""

from __future__ import annotations

from importlib import resources

MANUAL = "manual.md"


def manual_text() -> str:
    """The manual as Markdown, or "" if this build did not ship it.

    Empty rather than raising: a missing manual should cost the Help menu one
    item, not the application. ``pycangui --selftest`` fails on it instead, so
    a build that forgot the file is caught where it can still be fixed.
    """
    try:
        return resources.files(__name__).joinpath(MANUAL).read_text(encoding="utf-8")
    except (OSError, ModuleNotFoundError):
        return ""
