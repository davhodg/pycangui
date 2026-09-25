# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""DBC (and other cantools formats: KCD, SYM, ARXML) decoding.

Holds zero or more loaded databases. ``decode(frame)`` returns the message
name and signal values, or None if no database knows the id. Decoding errors
(wrong length, bad multiplexer) are counted, not raised.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pycangui.core.bus import Frame
from pycangui.j1939 import pgn_mask

if TYPE_CHECKING:  # for the annotations only, which are strings at run time
    from cantools.database import Database, Message

# cantools is needed only once a database is actually loaded. Most sessions
# never load one, and the ones that do are already waiting for a file dialog by
# the time it matters -- so it is imported where it is used rather than on the
# way to the first window.


class DbcDecoder:
    def __init__(self) -> None:
        self.databases: dict[str, Database] = {}  # path -> db
        self._by_id: dict[tuple[int, bool], Message] = {}
        self._by_pgn: dict[int, Message] = {}  # J1939 messages keyed by masked 29-bit id
        self.errors = 0

    # --- files ---------------------------------------------------------------------
    def load(self, path: str | Path, strict: bool = True) -> Database:
        """Load a database.

        ``strict`` is cantools' own check that the file is well formed --
        signals that do not overlap, and none running past the end of its
        message. Plenty of working databases fail it: it is a statement about
        the file, not about whether the messages in it can be used. So it
        stays on by default, and relaxing it is offered when a load fails
        rather than being the silent default.
        """
        path = str(path)
        import cantools.database

        db = cantools.database.load_file(path, strict=strict)
        self.databases[path] = db
        self._rebuild()
        return db

    def unload(self, path: str | Path) -> None:
        self.databases.pop(str(path), None)
        self._rebuild()

    def _rebuild(self) -> None:
        self._by_id = {}
        self._by_pgn = {}
        for db in self.databases.values():
            for msg in db.messages:
                self._by_id.setdefault((msg.frame_id, msg.is_extended_frame), msg)
                if msg.is_extended_frame and (msg.protocol == "j1939" or _looks_j1939(msg)):
                    pgn = (msg.frame_id >> 8) & 0x3FFFF
                    self._by_pgn.setdefault(msg.frame_id & pgn_mask(pgn), msg)

    @property
    def loaded(self) -> bool:
        return bool(self._by_id)

    # --- decoding --------------------------------------------------------------------
    def message_for(self, frame: Frame) -> Message | None:
        msg = self._by_id.get((frame.can_id, frame.extended))
        if msg is None and frame.extended and self._by_pgn:
            pgn = (frame.can_id >> 8) & 0x3FFFF
            msg = self._by_pgn.get(frame.can_id & pgn_mask(pgn))
        return msg

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

    def messages(self) -> list[Message]:
        """Every message from every loaded database, sorted by name."""
        seen: dict[str, Message] = {}
        for db in self.databases.values():
            for msg in db.messages:
                seen.setdefault(msg.name, msg)
        return sorted(seen.values(), key=lambda m: m.name)

    def message_by_name(self, name: str) -> Message | None:
        for msg in self.messages():
            if msg.name == name:
                return msg
        return None

    @staticmethod
    def units(msg: Message) -> dict[str, str]:
        return {s.name: s.unit or "" for s in msg.signals}


def _looks_j1939(msg: Message) -> bool:
    """DBCs without VFrameFormat: treat 29-bit ids with a J1939-shaped PGN as J1939."""
    return msg.frame_id > 0x7FF and (msg.frame_id >> 26) & 0x7 in (3, 6, 7)
