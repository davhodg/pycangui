# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Files pycangui supplies into a workspace, kept up to date without eating edits.

Hook files and simulated nodes are copied into the workspace to be changed,
which leaves two things to get right when a later pycangui ships a better
version of one:

* a copy nobody has touched should simply become the new one.  It is still
  pycangui's file, and leaving the old version in place means a fix that
  shipped never arrives.
* a copy somebody has changed must not be touched.  It is code they wrote,
  possibly the only copy of it.

Telling those apart needs a memory of what was copied, so the fingerprint of
each file as supplied is recorded in the workspace settings -- beside the
files it describes, so a workspace handed to a colleague carries a record
that matches its files.  A copy whose fingerprint still matches the record
is untouched; anything else is somebody's work.

Line endings are left out of the fingerprint.  The same file arrives with
CRLF from a Windows checkout and LF from a wheel, and an upgrade from one to
the other must not make every file look edited.

A workspace from before the record existed has no fingerprints, so any copy
that differs from what ships is treated as edited: left alone, and said so.
That errs towards keeping code, which is the direction to err in.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

RESTORE_ENTRY = "Tools > Reset > Restore supplied files..."


class _Source(Protocol):
    def read_bytes(self) -> bytes: ...


def fingerprint(data: bytes) -> str:
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


@dataclass
class Outcome:
    """What one pass of :meth:`Supplied.update` did, by file name."""

    copied: list[str] = field(default_factory=list)  #: there was none
    updated: list[str] = field(default_factory=list)  #: untouched, so replaced
    kept: list[str] = field(default_factory=list)  #: edited, and a newer one ships


class Supplied:
    """The supplied files of one kind in one workspace folder.

    ``label`` is the folder's name as the user sees it -- ``hooks``,
    ``nodes`` -- and keys the record in the workspace settings.
    """

    def __init__(
        self,
        label: str,
        folder: Path,
        sources: Mapping[str, _Source],
        settings,
    ) -> None:
        self.label = label
        self.folder = folder
        self.sources = dict(sources)
        self._settings = settings

    def names(self) -> list[str]:
        return sorted(self.sources)

    # --- the record ------------------------------------------------------------
    def _key(self) -> str:
        return f"supplied.{self.label}"

    def _record(self) -> dict[str, dict[str, str]]:
        saved = self._settings.get(self._key()) or {}
        return {
            "copied": dict(saved.get("copied", {})),
            #: The shipped version each edited file has already been told
            #: about, so the log says it once per new version, not every start.
            "told": dict(saved.get("told", {})),
        }

    def _save(self, record: dict[str, dict[str, str]]) -> None:
        self._settings.set(self._key(), record)

    # --- keeping up --------------------------------------------------------------
    def update(self, log: Callable[[str], None] | None = None) -> Outcome:
        """At startup: copy what is missing, replace what is untouched, and
        say once about each edited file a newer version exists for."""
        outcome = Outcome()
        record = self._record()
        copied, told = record["copied"], record["told"]
        for name in self.names():
            data = self.sources[name].read_bytes()
            shipped = fingerprint(data)
            dest = self.folder / name
            if not dest.exists():
                dest.write_bytes(data)
                copied[name] = shipped
                outcome.copied.append(name)
                continue
            mine = fingerprint(dest.read_bytes())
            if mine == shipped:
                copied[name] = shipped
            elif copied.get(name) == mine:
                dest.write_bytes(data)
                copied[name] = shipped
                outcome.updated.append(name)
            elif told.get(name) != shipped:
                told[name] = shipped
                outcome.kept.append(name)
        self._save(record)
        if log is not None:
            for message in self.describe(outcome, record):
                log(message)
        return outcome

    def describe(self, outcome: Outcome, record: dict | None = None) -> list[str]:
        record = record or self._record()
        lines = [
            f"{self.label}/{name} updated to this version of pycangui's; you had not changed it"
            for name in outcome.updated
        ]
        for name in outcome.kept:
            if name in record["copied"]:
                why = "yours has changes of your own"
            else:
                why = "yours differs from it -- your own changes, or a copy from an older pycangui"
            lines.append(
                f"{self.label}/{name}: this version of pycangui ships a newer one, but "
                f"{why}, so it was left as it is.  {RESTORE_ENTRY} takes the new one "
                "and keeps yours as a .bak."
            )
        return lines

    # --- by hand -------------------------------------------------------------------
    def edited(self) -> list[str]:
        """Which files differ from the ones this pycangui ships.

        Read as "which of these has somebody worked on", so a hook file that
        has only grown the stubs a later version appended counts as edited
        too: it is not the file that was shipped, and saying otherwise to
        somebody deciding what to restore would be the wrong sort of tidy.
        """
        return [
            name
            for name in self.names()
            if (dest := self.folder / name).exists()
            and fingerprint(dest.read_bytes()) != fingerprint(self.sources[name].read_bytes())
        ]

    def restore(self, name: str) -> Path:
        """Put the supplied version back, keeping the old one.  Returns where
        the old one went: renamed, never deleted, and never over an earlier
        ``.bak``."""
        dest = self.folder / name
        kept = dest.with_name(f"{name}.bak")
        number = 2
        while kept.exists():
            kept = dest.with_name(f"{name}.bak{number}")
            number += 1
        if dest.exists():
            dest.rename(kept)
        data = self.sources[name].read_bytes()
        dest.write_bytes(data)
        record = self._record()
        record["copied"][name] = fingerprint(data)
        self._save(record)
        return kept
