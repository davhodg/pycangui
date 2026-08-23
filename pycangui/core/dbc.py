"""DBC (and other cantools formats: KCD, SYM, ARXML) decoding.

Holds zero or more loaded databases.  ``decode(frame)`` returns the message
name and signal values, or None if no database knows the id.  Decoding errors
(wrong length, bad multiplexer) are counted, not raised.
"""

from __future__ import annotations

from pathlib import Path

import cantools
from cantools.database import Database, Message

from pycangui.core.bus import Frame


class DbcDecoder:
    def __init__(self) -> None:
        self.databases: dict[str, Database] = {}  # path -> db
        self._by_id: dict[tuple[int, bool], Message] = {}
        self.errors = 0

    # --- files ---------------------------------------------------------------------
    def load(self, path: str | Path) -> Database:
        path = str(path)
        db = cantools.database.load_file(path)
        self.databases[path] = db
        self._rebuild()
        return db

    def unload(self, path: str | Path) -> None:
        self.databases.pop(str(path), None)
        self._rebuild()

    def _rebuild(self) -> None:
        self._by_id = {}
        for db in self.databases.values():
            for msg in db.messages:
                self._by_id.setdefault((msg.frame_id, msg.is_extended_frame), msg)

    @property
    def loaded(self) -> bool:
        return bool(self._by_id)

    # --- decoding --------------------------------------------------------------------
    def message_for(self, frame: Frame) -> Message | None:
        return self._by_id.get((frame.can_id, frame.extended))

    def message_name(self, frame: Frame) -> str | None:
        msg = self.message_for(frame)
        return None if msg is None else msg.name

    def decode(self, frame: Frame) -> tuple[Message, dict[str, float]] | None:
        msg = self.message_for(frame)
        if msg is None:
            return None
        try:
            values = msg.decode(frame.data, decode_choices=False, allow_truncated=True)
        except Exception:  # cantools raises several kinds for malformed frames
            self.errors += 1
            return None
        return msg, values

    @staticmethod
    def units(msg: Message) -> dict[str, str]:
        return {s.name: s.unit or "" for s in msg.signals}
