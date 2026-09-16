# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""The dock arrangement, kept with the workspace rather than in QSettings.

Qt hands out an opaque block of bytes for a window's dock layout and for a
splitter's position, and the obvious home for those is QSettings, which is
where they used to live.  But QSettings is a per-machine store -- a registry
hive on Windows -- and a workspace has to be one folder: something that can be
copied to a backup, kept in version control, or sent to someone else along with
the hooks and the EDS files that make sense of it.  A layout left behind in
the registry would be the one part of a workspace that could not travel.

So the bytes go in a small JSON file beside the settings, base64 encoded
because that is what JSON can carry.  Nobody is meant to read them; what
matters is that they are *in* the folder.

What stays in QSettings is where the window sits on the screen, which belongs
to this desk and these monitors rather than to the work: switching product
should rearrange the panes, not move the window to where it was when somebody
last used that product on a different machine.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path


class Layout:
    """Opaque Qt state, by key, saved in one file with the workspace."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._data: dict[str, str] = {}
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                self._data = {k: v for k, v in loaded.items() if isinstance(v, str)}
            except (OSError, ValueError, AttributeError):
                self._data = {}  # a truncated write, or a hand-edited file

    def get(self, key: str) -> bytes | None:
        """The bytes stored under this key, or None if there are none.

        None rather than empty bytes: Qt's restoreState treats an empty state
        as a state, and answers it by leaving the window with no docks at all.
        """
        encoded = self._data.get(key)
        if not encoded:
            return None
        try:
            return base64.b64decode(encoded)
        except ValueError:
            return None

    def set(self, key: str, value: bytes | None) -> None:
        if value is None or len(value) == 0:
            self.remove(key)
            return
        self._data[key] = base64.b64encode(bytes(value)).decode("ascii")
        self.save()

    def remove(self, key: str) -> None:
        if self._data.pop(key, None) is not None:
            self.save()

    def keys(self) -> list[str]:
        return sorted(self._data)

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._data, indent=2, sort_keys=True), encoding="utf-8")
