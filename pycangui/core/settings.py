"""Persistent key/value store for things the GUI remembers (identity -> EDS
file, last used adapter, ...).  One JSON file, dotted keys, saved on change.

Kept separate from QSettings (which holds window layout) so the contents are
human readable and easy to hand-edit or version control.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class Settings:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._data: dict[str, Any] = {}
        if path.exists():
            try:
                self._data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self._data = {}

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value
        self.save()

    def keys(self) -> list[str]:
        return sorted(self._data)

    def remove(self, key: str) -> None:
        """Forget a setting, so that whatever default applies applies again."""
        if self._data.pop(key, None) is not None:
            self.save()

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._data, indent=2, sort_keys=True), encoding="utf-8")
