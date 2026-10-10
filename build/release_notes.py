# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""One version's notes from the changelog, for the GitHub release of that version.

The changelog is the one place a release is described: it ships in the
manual, and the release job takes the tag's section from it for the release
page, so the two cannot say different things. A tag with no section fails
here rather than publishing a release with no description.

    python build/release_notes.py v0.1.0 release-notes.md
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

CHANGELOG = Path(__file__).resolve().parent.parent / "pycangui" / "help" / "changelog.md"

#: "## [0.1.0] - 2026-09-27", and "## [Unreleased]".
HEADING = re.compile(r"^## \[(?P<version>[^\]]+)\].*$", re.M)
#: "[0.1.0]: https://...", the link definitions at the end of the file.
LINK = re.compile(r"^\[[^\]]+\]: \S+\s*$", re.M)


def section(version: str, text: str) -> str | None:
    """The body of one version's section, without its heading, or None."""
    headings = list(HEADING.finditer(text))
    for index, heading in enumerate(headings):
        if heading.group("version") == version:
            end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
            body = LINK.sub("", text[heading.end() : end]).strip()
            return unwrap(body) + "\n" if body else None
    return None


def unwrap(text: str) -> str:
    """Each paragraph and list item on one line.

    The changelog is wrapped like every page of the manual, and the manual's
    viewer joins the lines again. A GitHub release page does not: it keeps
    each line break, which would break every item part way through a sentence.
    """
    lines: list[str] = []
    for line in text.splitlines():
        # An indented item is one of a list inside an item, and starts a line too.
        starts_something = not line.strip() or line.lstrip().startswith(("#", "- ", "* "))
        if lines and lines[-1].strip() and not lines[-1].startswith("#") and not starts_something:
            lines[-1] = f"{lines[-1]} {line.strip()}"
        else:
            lines.append(line)
    return "\n".join(lines)


#: On every release page: the policy by name. Who signs, and with what, is
#: said there and not here, since it is not yet settled.
POLICY = "https://github.com/davhodg/pycangui/blob/master/CODE_SIGNING.md"


def signing_line(signed: bool) -> str:
    """The release page's code signing line, saying whether this one is signed.

    CI says, in the environment, whether it signed the build it is releasing:
    the policy is the same either way, and a release from before signing was
    in place should not look like one that has it.
    """
    if signed:
        return (
            f"**[Code signing policy]({POLICY}):** the Windows build of this release is "
            "code-signed."
        )
    return (
        f"**[Code signing policy]({POLICY}):** the Windows build of this release is not "
        "code-signed, so Windows SmartScreen warns about the installer; "
        "*More info > Run anyway* goes past it. Where an unsigned program is a problem, "
        "`pip install pycangui` from PyPI instead."
    )


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__.strip().splitlines()[-1].strip(), file=sys.stderr)
        return 2
    version = argv[1].removeprefix("v")
    notes = section(version, CHANGELOG.read_text(encoding="utf-8"))
    if notes is None:
        print(f"{CHANGELOG.name} has no section for {version}.", file=sys.stderr)
        return 1
    signed = os.environ.get("PYCANGUI_SIGNED", "").lower() == "true"
    Path(argv[2]).write_text(
        notes.rstrip() + "\n\n" + signing_line(signed) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
