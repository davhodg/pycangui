# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Editing a CAN database: changes held, checked, undone, and saved as a new file.

A database is edited as a list of changes over the file as it was read, and
nothing is written until *Save as*: never over the file it came from, and
never to a file that does not read back as what was meant. Undo takes the
last change off the list, and what is left is applied again to the file as
it was read -- one way of getting to a database, so undo cannot leave one
that no list of changes would have made.

Each change names its message and signal as they are called when it is
made, so a change after a rename names them by their new names.

The file is read in its own order: cantools sorts a message's signals by
start bit unless told not to, and a file saved after a one-character fix
would otherwise come back with every message rearranged.

What is not edited here yet: multiplexing, attributes, nodes and value
tables. All of them are kept as they were, by cantools, when the rest is
saved (see ``dbc_roundtrip``).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pycangui.core import dbc_roundtrip

#: What a message can have changed, and what a signal can.
MESSAGE_FIELDS = (
    "name",
    "frame_id",
    "is_extended_frame",
    "length",
    "cycle_time",
    "senders",
    "comment",
)
SIGNAL_FIELDS = (
    "name",
    "start",
    "length",
    "byte_order",
    "is_signed",
    "is_float",
    "scale",
    "offset",
    "minimum",
    "maximum",
    "unit",
    "comment",
    "choices",
    "receivers",
)
#: The lengths a CAN FD frame can have; a classic one is 0 to 8.
FD_LENGTHS = (0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 20, 24, 32, 48, 64)
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
#: An error stops a save; a warning is said and does not.
ERROR, WARNING = "error", "warning"


# --- the changes ----------------------------------------------------------------------------
@dataclass(frozen=True)
class SetMessage:
    message: str
    field: str
    value: Any

    def described(self) -> str:
        return f"{self.message}: {self.field} set to {_shown(self.value)}"


@dataclass(frozen=True)
class SetSignal:
    message: str
    signal: str
    field: str
    value: Any

    def described(self) -> str:
        return f"{self.message}.{self.signal}: {self.field} set to {_shown(self.value)}"


@dataclass(frozen=True)
class AddMessage:
    name: str
    frame_id: int
    is_extended_frame: bool = False
    length: int = 8

    def described(self) -> str:
        return f"{self.name}: added"


@dataclass(frozen=True)
class RemoveMessage:
    message: str

    def described(self) -> str:
        return f"{self.message}: removed"


@dataclass(frozen=True)
class AddSignal:
    message: str
    name: str
    start: int = 0
    length: int = 8

    def described(self) -> str:
        return f"{self.message}.{self.name}: added"


@dataclass(frozen=True)
class RemoveSignal:
    message: str
    signal: str

    def described(self) -> str:
        return f"{self.message}.{self.signal}: removed"


Change = SetMessage | SetSignal | AddMessage | RemoveMessage | AddSignal | RemoveSignal


def _shown(value: Any) -> str:
    if isinstance(value, dict):
        return ", ".join(f"{k} {v}" for k, v in value.items()) or "none"
    if isinstance(value, list | tuple):
        return ", ".join(str(v) for v in value) or "none"
    if isinstance(value, int) and not isinstance(value, bool) and value > 9:
        return f"{value} (0x{value:X})"
    return repr(value) if isinstance(value, str) else str(value)


@dataclass(frozen=True)
class Problem:
    """Something wrong with the database as it is now."""

    level: str
    where: str
    what: str


class EditError(ValueError):
    """A change that cannot be made: what it names is not there, or it would
    leave two of something that must be one."""


# --- the editor --------------------------------------------------------------------------------
@dataclass
class DatabaseEdit:
    """A database being edited: the file it came from, and the changes."""

    path: Path
    changes: list[Change] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        self._text = self.path.read_text(encoding="utf-8", errors="replace")
        self.database = self._read()

    # --- reading and replaying --------------------------------------------------------
    def _read(self):
        import cantools.database

        return cantools.database.load_string(
            self._text, database_format="dbc", strict=False, sort_signals=None
        )

    def _replay(self) -> None:
        database = self._read()
        for change in self.changes:
            _apply(database, change)
        self.database = database

    # --- changing ---------------------------------------------------------------------
    def apply(self, change: Change) -> None:
        """Make a change, or raise EditError and change nothing.

        Made on the database as it stands: a large file is not read again
        for every change. Each change looks before it touches anything, so a
        refusal leaves nothing half done; anything else going wrong part way
        puts the database back the long way, from the file and the list.
        """
        try:
            _apply(self.database, change)
        except EditError:
            raise
        except Exception as exc:  # cantools, part way through a change
            self._replay()
            raise EditError(f"Not changed: {exc}") from exc
        self.changes.append(change)

    def apply_all(self, changes: Iterable[Change]) -> None:
        """Make several changes as one -- the fields of one dialog -- or none of them."""
        made = 0
        try:
            for change in changes:
                self.apply(change)
                made += 1
        except EditError:
            del self.changes[len(self.changes) - made :]
            self._replay()
            raise

    def undo(self) -> Change | None:
        """Take back the last change; the one taken back, or None if none."""
        if not self.changes:
            return None
        change = self.changes.pop()
        self._replay()
        return change

    def discard(self) -> None:
        self.changes.clear()
        self.database = self._read()

    @property
    def changed(self) -> bool:
        return bool(self.changes)

    def described(self) -> list[str]:
        """The changes, a line each, in the order they were made."""
        return [change.described() for change in self.changes]

    def touched(self) -> set[str]:
        """The messages changed or added, by the names they have now."""
        names: set[str] = set()
        for change in self.changes:
            name = change.name if isinstance(change, AddMessage) else change.message
            if isinstance(change, SetMessage) and change.field == "name":
                names.discard(name)
                name = change.value
            if isinstance(change, RemoveMessage):
                names.discard(name)
                continue
            names.add(name)
        return {name for name in names if any(m.name == name for m in self.database.messages)}

    def renamed_messages(self) -> dict[str, str]:
        """Old name -> new name, for each message renamed: what refers to a
        message by name -- a transmit row, a plotted signal -- needs to know."""
        return _renames(self.changes, SetMessage)

    # --- looking ----------------------------------------------------------------------
    def message(self, name: str):
        return _message(self.database, name)

    def problems(self) -> list[Problem]:
        return problems(self.database)

    # --- saving -----------------------------------------------------------------------
    def save_as(self, path: str | Path) -> list[dbc_roundtrip.Finding]:
        """Write the database to a new file, and read it back.

        Never over the file it came from, never with an error in it. What
        comes back is what reading the file written found to differ from what
        was meant: empty, or the same multiplexing and long names said the
        format's longer way -- see ``dbc_roundtrip``.
        """
        target = Path(path)
        if target.resolve() == self.path.resolve():
            raise EditError("A database is saved as a new file, never over the one it came from.")
        errors = [p for p in self.problems() if p.level == ERROR]
        if errors:
            raise EditError(f"Not saved: {errors[0].where}: {errors[0].what}")
        text = self.database.as_dbc_string()
        # As cantools wrote it, line endings and all: written as text on
        # Windows, each of its CR LFs gained another CR.
        target.write_text(text, encoding="utf-8", newline="")
        import cantools.database

        back = cantools.database.load_string(
            target.read_text(encoding="utf-8"),
            database_format="dbc",
            strict=False,
            sort_signals=None,
        )
        # What a message or signal reads back as. Not the attributes: some
        # are how the format writes what was set -- a cycle time is written
        # as GenMsgCycleTime -- and are not on the database until it is read.
        return [
            f
            for f in dbc_roundtrip.compare(self.database, back, text, text)
            if f.kind not in ("statement", "attribute")
        ]


def _renames(changes: Iterable[Change], kind) -> dict[str, str]:
    out: dict[str, str] = {}
    for change in changes:
        if isinstance(change, kind) and change.field == "name":
            first = next((k for k, v in out.items() if v == change.message), change.message)
            out[first] = change.value
    return {old: new for old, new in out.items() if old != new}


# --- applying a change ----------------------------------------------------------------------------
def _message(database, name: str):
    for message in database.messages:
        if message.name == name:
            return message
    raise EditError(f"There is no message called {name}.")


def _signal(message, name: str):
    for signal in message.signals:
        if signal.name == name:
            return signal
    raise EditError(f"{message.name} has no signal called {name}.")


def _apply(database, change: Change) -> None:
    if isinstance(change, SetMessage):
        _set_message(database, _message(database, change.message), change.field, change.value)
    elif isinstance(change, SetSignal):
        message = _message(database, change.message)
        _set_signal(message, _signal(message, change.signal), change.field, change.value)
        message.refresh(strict=False)
    elif isinstance(change, AddMessage):
        _add_message(database, change)
    elif isinstance(change, RemoveMessage):
        database.messages.remove(_message(database, change.message))
    elif isinstance(change, AddSignal):
        _add_signal(_message(database, change.message), change)
    elif isinstance(change, RemoveSignal):
        message = _message(database, change.message)
        signal = _signal(message, change.signal)
        if signal.is_multiplexer and any(
            s.multiplexer_signal == signal.name for s in message.signals
        ):
            raise EditError(f"{signal.name} chooses which of the others are sent: not removed.")
        message.signals.remove(signal)
        for group in message.signal_groups or []:
            group.signal_names = [n for n in group.signal_names if n != signal.name]
        message.refresh(strict=False)
    else:  # pragma: no cover - every kind is above
        raise EditError(f"Not a change: {change!r}")
    database.refresh()


def _set_message(database, message, name: str, value: Any) -> None:
    if name not in MESSAGE_FIELDS:
        raise EditError(f"A message has no {name} to set.")
    if name == "name" and value != message.name:
        if any(m.name == value for m in database.messages):
            raise EditError(f"There is a message called {value} already.")
    if name == "senders":
        message.senders[:] = list(value)  # the list itself: cantools has no setter
        return
    setattr(message, name, value)
    if name in ("length", "frame_id", "is_extended_frame"):
        message.refresh(strict=False)


def _set_signal(message, signal, name: str, value: Any) -> None:
    if name not in SIGNAL_FIELDS:
        raise EditError(f"A signal has no {name} to set.")
    if name == "name" and value != signal.name:
        if any(s.name == value for s in message.signals):
            raise EditError(f"{message.name} has a signal called {value} already.")
        for other in message.signals:  # what it multiplexes follows it
            if other.multiplexer_signal == signal.name:
                other.multiplexer_signal = value
        for group in message.signal_groups or []:  # and the groups it is in
            group.signal_names = [value if n == signal.name else n for n in group.signal_names]
    if name == "receivers":
        signal.receivers[:] = list(value)
        return
    if name == "choices":
        value = dict(value) or None
    setattr(signal, name, value)


def _add_message(database, change: AddMessage) -> None:
    from cantools.database import Message

    if any(m.name == change.name for m in database.messages):
        raise EditError(f"There is a message called {change.name} already.")
    database.messages.append(
        Message(
            frame_id=change.frame_id,
            name=change.name,
            length=change.length,
            signals=[],
            is_extended_frame=change.is_extended_frame,
            strict=False,
            sort_signals=None,
        )
    )


def _add_signal(message, change: AddSignal) -> None:
    from cantools.database import Signal

    if any(s.name == change.name for s in message.signals):
        raise EditError(f"{message.name} has a signal called {change.name} already.")
    message.signals.append(Signal(name=change.name, start=change.start, length=change.length))
    message.refresh(strict=False)


# --- what is wrong with a database ----------------------------------------------------------------
def bits(signal) -> set[int]:
    """The bits a signal has, numbered as the format numbers them."""
    out, at = set(), signal.start
    for _ in range(signal.length):
        out.add(at)
        if signal.byte_order == "little_endian":
            at += 1
        elif at % 8 == 0:
            at += 15
        else:
            at -= 1
    return out


def _conditions(signal, by_name: dict) -> dict[str, set[int]]:
    """Multiplexer -> the values it must have for the signal to be sent, up
    the chain: a signal under a multiplexer that is itself under another is
    sent for one value of each."""
    out: dict[str, set[int]] = {}
    while signal is not None and signal.multiplexer_ids:
        out.setdefault(signal.multiplexer_signal, set(signal.multiplexer_ids))
        signal = by_name.get(signal.multiplexer_signal)
    return out


def _together(a, b, by_name: dict) -> bool:
    """Whether two signals can be in one frame: not when a multiplexer they
    both depend on must have different values for each."""
    first, second = _conditions(a, by_name), _conditions(b, by_name)
    return all(first[mux] & second[mux] for mux in first.keys() & second.keys())


def problems(database) -> list[Problem]:
    """Errors, which stop a save, and warnings, which are said."""
    found: list[Problem] = []
    names: dict[str, int] = {}
    ids: dict[tuple[int, bool], str] = {}
    for message in database.messages:
        where = message.name
        names[message.name] = names.get(message.name, 0) + 1
        key = (message.frame_id, message.is_extended_frame)
        if key in ids:
            found.append(Problem(ERROR, where, f"the same identifier as {ids[key]}"))
        ids.setdefault(key, message.name)
        if not NAME.match(message.name):
            found.append(Problem(ERROR, where, "not a name the format allows"))
        top = 0x1FFFFFFF if message.is_extended_frame else 0x7FF
        if not 0 <= message.frame_id <= top:
            found.append(Problem(ERROR, where, f"identifier past 0x{top:X}"))
        allowed = FD_LENGTHS if message.is_fd else range(9)
        if message.length not in allowed:
            found.append(Problem(ERROR, where, f"{message.length} bytes is not a frame's length"))
        signal_names: set[str] = set()
        for signal in message.signals:
            at = f"{message.name}.{signal.name}"
            if signal.name in signal_names:
                found.append(Problem(ERROR, at, "two signals of this name"))
            signal_names.add(signal.name)
            if not NAME.match(signal.name):
                found.append(Problem(ERROR, at, "not a name the format allows"))
            if signal.length < 1:
                found.append(Problem(ERROR, at, "no bits"))
            elif max(bits(signal)) >= 8 * message.length or min(bits(signal)) < 0:
                found.append(Problem(ERROR, at, "runs past the end of the message"))
            if signal.is_float and signal.length not in (32, 64):
                found.append(Problem(ERROR, at, "a float is 32 or 64 bits"))
            if signal.scale == 0:
                found.append(Problem(ERROR, at, "a scale of 0"))
            low, high = signal.minimum, signal.maximum
            if low is not None and high is not None and low > high:
                found.append(Problem(WARNING, at, "its minimum is above its maximum"))
        by_name = {s.name: s for s in message.signals}
        for i, a in enumerate(message.signals):
            for b in message.signals[i + 1 :]:
                if _together(a, b, by_name) and bits(a) & bits(b):
                    found.append(
                        Problem(WARNING, f"{message.name}.{a.name}", f"shares bits with {b.name}")
                    )
    for name, count in names.items():
        if count > 1:
            found.append(Problem(ERROR, name, "two messages of this name"))
    return found
