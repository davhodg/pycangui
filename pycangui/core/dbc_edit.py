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

Multiplexing is two fields of a signal: whether it chooses which of the
others are sent, and which multiplexer and values it is sent for. Value
tables, nodes, signal groups, attributes and the attributes' definitions are
changes of their own.

A rename or a removal follows through what names the thing: a node renamed
is renamed where it sends and receives, and in the relation attributes that
name it; a signal or an identifier changed, in those relation attributes
too. A node still sending or receiving is not removed, nor is a multiplexer
still choosing.

Four attributes are not set as attributes, because the format keeps them in
step with something else: GenMsgCycleTime is a message's cycle time and
GenSigStartValue a signal's initial value, so setting either sets that; and
VFrameFormat follows a message's identifier and FD, and Baudrate the bus,
so neither is set here at all.

What is not edited: environment variables and the relation attributes
themselves (``BA_REL_``), which are kept as they were.
"""

from __future__ import annotations

import re
from collections import OrderedDict
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
    # Whether it chooses which of the others are sent.
    "is_multiplexer",
    # (multiplexer, values) it is sent for; (None, ()) for every frame.
    "multiplexed_by",
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
        if self.field == "multiplexed_by":
            mux, values = self.value
            sent = f"sent when {mux} is {values_text(values)}" if mux else "sent in every frame"
            return f"{self.message}.{self.signal}: {sent}"
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


@dataclass(frozen=True)
class SetValueTable:
    """A value table's ``name`` or its ``values``, {number: name}."""

    table: str
    field: str
    value: Any

    def described(self) -> str:
        return f"Value table {self.table}: {self.field} set to {_shown(self.value)}"


@dataclass(frozen=True)
class AddValueTable:
    name: str
    values: dict = field(default_factory=dict)

    def described(self) -> str:
        return f"Value table {self.name}: added"


@dataclass(frozen=True)
class RemoveValueTable:
    table: str

    def described(self) -> str:
        return f"Value table {self.table}: removed"


#: Where an attribute is set: () the database, ("node", name), ("message",
#: name), ("signal", message, name).
Target = tuple
#: What each kind of target is called in an attribute's definition.
KINDS = {"database": None, "node": "BU_", "message": "BO_", "signal": "SG_"}
TYPES = ("INT", "HEX", "FLOAT", "STRING", "ENUM")
#: Set as something else, by the format, and not as attributes; see the top.
FOLLOWS = {
    "VFrameFormat": "follows whether the message is 29-bit and CAN FD",
    "Baudrate": "follows the bus's bit rate",
}
#: Definitions the format adds and keeps itself, so not removed or renamed here.
KEPT_BY_THE_FORMAT = (
    "GenMsgCycleTime",
    "GenSigStartValue",
    "VFrameFormat",
    "Baudrate",
    "CANFD_BRS",
)
#: The receiver the format writes for a signal nobody receives.
NO_NODE = "Vector__XXX"


def kind_of(target: Target) -> str:
    return target[0] if target else "database"


def where(target: Target) -> str:
    """A target as the changes say it."""
    kind = kind_of(target)
    if kind == "database":
        return "Database"
    if kind == "node":
        return f"Node {target[1]}"
    return ".".join(target[1:])


@dataclass(frozen=True)
class AttributeRow:
    """One attribute as a dialog shows it: its definition, and its value on
    one target -- None for its default, an enumeration's by name."""

    name: str
    type_name: str
    choices: tuple[str, ...]
    minimum: float | None
    maximum: float | None
    default: Any
    value: Any
    #: Why it is not set here, or "".
    fixed: str = ""


VALUE_TABLE_CHANGES = (SetValueTable, AddValueTable, RemoveValueTable)


@dataclass(frozen=True)
class AddNode:
    name: str
    comment: str | None = None

    def described(self) -> str:
        return f"Node {self.name}: added"


@dataclass(frozen=True)
class RemoveNode:
    node: str

    def described(self) -> str:
        return f"Node {self.node}: removed"


@dataclass(frozen=True)
class SetNode:
    """A node's ``name`` or ``comment``."""

    node: str
    field: str
    value: Any

    def described(self) -> str:
        return f"Node {self.node}: {self.field} set to {_shown(self.value)}"


NODE_CHANGES = (AddNode, RemoveNode, SetNode)


@dataclass(frozen=True)
class AddSignalGroup:
    message: str
    name: str
    repetitions: int = 1
    signals: tuple[str, ...] = ()

    def described(self) -> str:
        return f"{self.message}: signal group {self.name} added"


@dataclass(frozen=True)
class RemoveSignalGroup:
    message: str
    group: str

    def described(self) -> str:
        return f"{self.message}: signal group {self.group} removed"


@dataclass(frozen=True)
class SetSignalGroup:
    """A signal group's ``name``, ``repetitions`` or ``signals``."""

    message: str
    group: str
    field: str
    value: Any

    def described(self) -> str:
        return (
            f"{self.message}: signal group {self.group}: {self.field} set to {_shown(self.value)}"
        )


GROUP_CHANGES = (AddSignalGroup, RemoveSignalGroup, SetSignalGroup)


@dataclass(frozen=True)
class SetAttribute:
    """An attribute's value on ``target``; a value of None takes it back to its
    default. See ``Target``."""

    target: tuple
    name: str
    value: Any

    def described(self) -> str:
        to = "its default" if self.value is None else _shown(self.value)
        return f"{where(self.target)}: attribute {self.name} set to {to}"


@dataclass(frozen=True)
class AttributeSpec:
    """An attribute's definition: what it applies to -- ``database``, ``node``,
    ``message`` or ``signal`` -- its type, its range or its choices, and its
    default."""

    name: str
    kind: str
    type_name: str
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[str, ...] = ()
    default: Any = None


@dataclass(frozen=True)
class AddAttributeDefinition:
    spec: AttributeSpec

    def described(self) -> str:
        return f"Attribute {self.spec.name}: defined"


@dataclass(frozen=True)
class SetAttributeDefinition:
    """``definition`` as ``spec`` says, renamed if its name differs."""

    definition: str
    spec: AttributeSpec

    def described(self) -> str:
        return f"Attribute {self.definition}: definition changed"


@dataclass(frozen=True)
class RemoveAttributeDefinition:
    definition: str

    def described(self) -> str:
        return f"Attribute {self.definition}: removed, with any values it had"


DEFINITION_CHANGES = (AddAttributeDefinition, SetAttributeDefinition, RemoveAttributeDefinition)

Change = (
    SetMessage
    | SetSignal
    | AddMessage
    | RemoveMessage
    | AddSignal
    | RemoveSignal
    | SetValueTable
    | AddValueTable
    | RemoveValueTable
    | AddNode
    | RemoveNode
    | SetNode
    | AddSignalGroup
    | RemoveSignalGroup
    | SetSignalGroup
    | SetAttribute
    | AddAttributeDefinition
    | SetAttributeDefinition
    | RemoveAttributeDefinition
)


def values_text(values: Iterable[int]) -> str:
    """Multiplexer values as they are typed: ``0, 2-4``."""
    ordered, runs = sorted(set(values)), []
    for value in ordered:
        if runs and value == runs[-1][1] + 1:
            runs[-1][1] = value
        else:
            runs.append([value, value])
    return ", ".join(str(a) if a == b else f"{a}-{b}" for a, b in runs)


def parse_values(text: str) -> list[int]:
    """``0, 2-4`` as [0, 2, 3, 4]; ValueError for anything that is not."""
    out: set[int] = set()
    for part in (p.strip() for p in text.split(",")):
        if not part:
            continue
        low, dash, high = part.partition("-")
        first, last = int(low, 0), int(high, 0) if dash else int(low, 0)
        if first < 0 or last < first:
            raise ValueError(part)
        out.update(range(first, last + 1))
    return sorted(out)


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
            name = _message_of(change)
            if name is None:
                continue  # the database's, not a message's
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

    def value_tables(self) -> dict[str, dict[int, str]]:
        """Each value table, in the file's order: {name: {number: name}}."""
        return {name: dict(values) for name, values in _value_tables(self.database).items()}

    def nodes(self) -> list:
        return list(self.database.nodes)

    def attributes(self, target: Target) -> list[AttributeRow]:
        """The attributes a target can have, from their definitions, with its
        values. A message's GenMsgCycleTime is left out: it is the message's
        cycle time, which its dialog has already.

        A target of its kind alone -- ``("message",)`` -- is one not made yet,
        with every attribute at its default."""
        kind = kind_of(target)
        named = kind == "database" or len(target) == (3 if kind == "signal" else 2)
        thing = _target(self.database, target) if named else None
        held = _held(self.database, target, thing) if named else {}
        rows = []
        for definition in _definitions(self.database).values():
            if definition.kind != KINDS[kind]:
                continue
            if kind == "message" and definition.name == "GenMsgCycleTime":
                continue
            if kind == "signal" and definition.name == "GenSigStartValue":
                value = thing.raw_initial if thing is not None else None
            else:
                attribute = held.get(definition.name)
                value = None if attribute is None else attribute.value
            if definition.type_name == "ENUM" and isinstance(value, int):
                choices = definition.choices or []
                value = choices[value] if 0 <= value < len(choices) else str(value)
            rows.append(
                AttributeRow(
                    name=definition.name,
                    type_name=definition.type_name or "STRING",
                    choices=tuple(definition.choices or ()),
                    minimum=definition.minimum,
                    maximum=definition.maximum,
                    default=definition.default_value,
                    value=value,
                    fixed=FOLLOWS.get(definition.name, ""),
                )
            )
        return rows

    def definitions(self) -> list[AttributeSpec]:
        """Every attribute's definition, as its dialog shows it."""
        kind_names = {v: k for k, v in KINDS.items()}
        return [
            AttributeSpec(
                name=d.name,
                kind=kind_names.get(d.kind, "database"),
                type_name=d.type_name or "STRING",
                minimum=d.minimum,
                maximum=d.maximum,
                choices=tuple(d.choices or ()),
                default=d.default_value,
            )
            for d in _definitions(self.database).values()
            if d.kind in kind_names  # not an environment variable's
        ]

    def uses(self, definition: str) -> int:
        """How many things an attribute is set on."""
        return sum(definition in held for held in _all_held(self.database))

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


def _message_of(change: Change) -> str | None:
    """The message a change is of, by the name it had then; None for one of
    the database's own -- a node, a value table, an attribute's definition."""
    if isinstance(change, AddMessage):
        return change.name
    if isinstance(change, SetAttribute):
        return change.target[1] if kind_of(change.target) in ("message", "signal") else None
    return getattr(change, "message", None)


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
        message = _message(database, change.message)
        before = _dbc_id(message)
        _set_message(database, message, change.field, change.value)
        if _dbc_id(message) != before:
            _rekey_message_relations(database, before, _dbc_id(message))
    elif isinstance(change, SetSignal):
        message = _message(database, change.message)
        _set_signal(message, _signal(message, change.signal), change.field, change.value)
        if change.field == "name" and change.value != change.signal:
            _rename_signal_relations(database, message, change.signal, change.value)
        message.refresh(strict=False)
    elif isinstance(change, AddMessage):
        _add_message(database, change)
    elif isinstance(change, RemoveMessage):
        message = _message(database, change.message)
        database.messages.remove(message)
        _drop_message_relations(database, _dbc_id(message))
    elif isinstance(change, NODE_CHANGES):
        _change_node(database, change)
    elif isinstance(change, GROUP_CHANGES):
        _change_group(_message(database, change.message), change)
    elif isinstance(change, SetAttribute):
        _set_attribute(database, change)
    elif isinstance(change, DEFINITION_CHANGES):
        _change_definition(database, change)
    elif isinstance(change, AddSignal):
        _add_signal(_message(database, change.message), change)
    elif isinstance(change, VALUE_TABLE_CHANGES):
        _change_value_table(database, change)
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
        _drop_signal_relations(database, message, signal.name)
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
    if name == "is_multiplexer":
        if not value and any(s.multiplexer_signal == signal.name for s in message.signals):
            raise EditError(f"{signal.name} chooses which of the others are sent: still needed.")
        signal.is_multiplexer = bool(value)
        return
    if name == "multiplexed_by":
        _multiplex(message, signal, *value)
        return
    if name == "choices":
        value = dict(value) or None
    setattr(signal, name, value)


def _multiplex(message, signal, mux: str | None, values) -> None:
    """Sent only when ``mux`` has one of ``values``; with no ``mux``, always."""
    if not mux:
        signal.multiplexer_signal, signal.multiplexer_ids = None, None
        return
    chooser = _signal(message, mux)
    if not chooser.is_multiplexer:
        raise EditError(f"{mux} does not choose which signals are sent.")
    above = chooser
    while above is not None:  # not under itself, however far up
        if above is signal:
            raise EditError(f"{signal.name} cannot be sent by {mux}, which it chooses itself.")
        above = next((s for s in message.signals if s.name == above.multiplexer_signal), None)
    wanted = sorted({int(v) for v in values})
    if not wanted or wanted[0] < 0:
        raise EditError(f"Which values of {mux} is {signal.name} sent for?")
    signal.multiplexer_signal, signal.multiplexer_ids = mux, wanted


def _value_tables(database):
    """The database's value tables, made if a file had none to start with."""
    if database.dbc is None:
        from cantools.database.can.formats.dbc_specifics import DbcSpecifics

        database.dbc = DbcSpecifics()
    return database.dbc.value_tables


def _change_value_table(database, change) -> None:
    tables = _value_tables(database)
    if isinstance(change, AddValueTable):
        if change.name in tables:
            raise EditError(f"There is a value table called {change.name} already.")
        tables[change.name] = OrderedDict(dict(change.values).items())
        return
    if change.table not in tables:
        raise EditError(f"There is no value table called {change.table}.")
    if isinstance(change, RemoveValueTable):
        del tables[change.table]
    elif change.field == "values":
        tables[change.table] = OrderedDict(dict(change.value).items())
    elif change.field == "name":
        if change.value != change.table and change.value in tables:
            raise EditError(f"There is a value table called {change.value} already.")
        renamed = [(change.value if k == change.table else k, v) for k, v in tables.items()]
        tables.clear()  # in place, keeping its place in the file
        tables.update(renamed)
    else:
        raise EditError(f"A value table has no {change.field} to set.")


# --- nodes ---------------------------------------------------------------------------------------
def _node(database, name: str):
    for node in database.nodes:
        if node.name == name:
            return node
    raise EditError(f"There is no node called {name}.")


def _node_uses(database, name: str) -> str:
    """What a node sends and receives, said briefly; "" for nothing."""
    sends = [m.name for m in database.messages if name in (m.senders or [])]
    receives = [
        f"{m.name}.{s.name}" for m in database.messages for s in m.signals if name in s.receivers
    ]
    said = []
    if sends:
        said.append("sends " + _few(sends))
    if receives:
        said.append("receives " + _few(receives))
    return " and ".join(said)


def _few(names: list[str]) -> str:
    return ", ".join(names[:3]) + (f" and {len(names) - 3} more" if len(names) > 3 else "")


def _change_node(database, change) -> None:
    from cantools.database.can import Node

    if isinstance(change, AddNode):
        if any(n.name == change.name for n in database.nodes):
            raise EditError(f"There is a node called {change.name} already.")
        database.nodes.append(
            Node(
                name=change.name,
                comment=change.comment or None,
                dbc_specifics=_new_specifics(database),
            )
        )
        return
    node = _node(database, change.node)
    if isinstance(change, RemoveNode):
        if uses := _node_uses(database, node.name):
            raise EditError(f"{node.name} {uses}: not removed. Take it off those first.")
        database.nodes.remove(node)
        _drop_node_relations(database, node.name)
    elif change.field == "comment":
        # Where the comment is read from. cantools' own setter puts it where
        # nothing reads it, and the file was written without it.
        node._comments = {None: change.value} if change.value else None
    elif change.field == "name":
        old, new = node.name, change.value
        if new == old:
            return
        if any(n.name == new for n in database.nodes):
            raise EditError(f"There is a node called {new} already.")
        node.name = new
        for message in database.messages:
            message.senders[:] = [new if n == old else n for n in message.senders]
            for signal in message.signals:
                signal.receivers[:] = [new if n == old else n for n in signal.receivers]
        _rename_node_relations(database, old, new)
    else:
        raise EditError(f"A node has no {change.field} to set.")


# --- signal groups --------------------------------------------------------------------------------
def _change_group(message, change) -> None:
    from cantools.database.can.signal_group import SignalGroup

    if message.signal_groups is None:
        message.signal_groups = []
    groups = message.signal_groups
    names = {s.name for s in message.signals}

    def known(signals) -> list[str]:
        missing = [n for n in signals if n not in names]
        if missing:
            raise EditError(f"{message.name} has no signal called {missing[0]}.")
        return list(signals)

    if isinstance(change, AddSignalGroup):
        if any(g.name == change.name for g in groups):
            raise EditError(f"{message.name} has a signal group called {change.name} already.")
        groups.append(SignalGroup(change.name, int(change.repetitions), known(change.signals)))
        return
    group = next((g for g in groups if g.name == change.group), None)
    if group is None:
        raise EditError(f"{message.name} has no signal group called {change.group}.")
    if isinstance(change, RemoveSignalGroup):
        groups.remove(group)
    elif change.field == "name":
        if change.value != group.name and any(g.name == change.value for g in groups):
            raise EditError(f"{message.name} has a signal group called {change.value} already.")
        group.name = change.value
    elif change.field == "repetitions":
        if int(change.value) < 1:
            raise EditError("A signal group is repeated at least once.")
        group.repetitions = int(change.value)
    elif change.field == "signals":
        group.signal_names = known(change.value)
    else:
        raise EditError(f"A signal group has no {change.field} to set.")


# --- attributes -----------------------------------------------------------------------------------
def _specifics(database):
    """The database's own DBC part, made for a file that had none."""
    if database.dbc is None:
        from cantools.database.can.formats.dbc_specifics import DbcSpecifics

        database.dbc = DbcSpecifics()
    return database.dbc


def _new_specifics(database):
    """A DBC part for a node, message or signal, sharing the database's definitions."""
    from cantools.database.can.formats.dbc_specifics import DbcSpecifics

    return DbcSpecifics(attribute_definitions=_definitions(database))


def _definitions(database):
    return _specifics(database).attribute_definitions


def _target(database, target: Target):
    """What an attribute is set on: the database, or its node, message or signal."""
    kind = kind_of(target)
    if kind == "database":
        return database
    if kind == "node":
        return _node(database, target[1])
    if kind == "message":
        return _message(database, target[1])
    if kind == "signal":
        message = _message(database, target[1])
        return _signal(message, target[2])
    raise EditError(f"Not something with attributes: {target!r}")


def _held(database, target: Target, thing) -> dict:
    """The attributes set on a target, as they stand; empty for one with none."""
    if kind_of(target) == "database":
        return _specifics(database).attributes
    return thing.dbc.attributes if thing.dbc is not None else {}


def _all_held(database) -> list[dict]:
    """Every set of attributes in the database."""
    held = [_specifics(database).attributes]
    held += [n.dbc.attributes for n in database.nodes if n.dbc is not None]
    for message in database.messages:
        if message.dbc is not None:
            held.append(message.dbc.attributes)
        held += [s.dbc.attributes for s in message.signals if s.dbc is not None]
    return held


def attribute_value(definition, value: Any) -> Any:
    """A value as its definition stores it -- an enumeration's choice by its
    index -- or EditError for one it cannot have."""
    kind = definition.type_name
    try:
        if kind in ("INT", "HEX"):
            if isinstance(value, float) and not value.is_integer():
                raise ValueError(value)
            return int(value)
        if kind == "FLOAT":
            return float(value)
        if kind == "ENUM":
            choices = list(definition.choices or [])
            if isinstance(value, str) and value in choices:
                return choices.index(value)
            if isinstance(value, int) and 0 <= value < len(choices):
                return value
            raise ValueError(value)
    except (TypeError, ValueError):
        allowed = ", ".join(definition.choices or []) if kind == "ENUM" else kind.lower()
        raise EditError(f"{definition.name} cannot be {value!r}: it is {allowed}.") from None
    return str(value)


def _set_attribute(database, change: SetAttribute) -> None:
    from cantools.database.can.attribute import Attribute

    definition = _definitions(database).get(change.name)
    if definition is None:
        raise EditError(f"There is no attribute called {change.name}.")
    kind = kind_of(change.target)
    if definition.kind != KINDS.get(kind, "?"):
        raise EditError(f"{change.name} is not an attribute of the {kind}.")
    if change.name in FOLLOWS:
        raise EditError(f"{change.name} {FOLLOWS[change.name]}, so it is not set here.")
    thing = _target(database, change.target)
    value = None if change.value is None else attribute_value(definition, change.value)
    if kind == "message" and change.name == "GenMsgCycleTime":
        thing.cycle_time = value or None  # written as the attribute, by the format
        return
    if kind == "signal" and change.name == "GenSigStartValue":
        thing.raw_initial = value
        return
    if kind != "database" and thing.dbc is None:
        thing.dbc = _new_specifics(database)
    held = _held(database, change.target, thing)
    if value is None:
        held.pop(change.name, None)
    else:
        held[change.name] = Attribute(value=value, definition=definition)
    if kind == "message" and change.name == "GenMsgSendType":
        # Read from the attribute when the file is, and kept by cantools apart
        # from it after, with no setter: so that the copy says what the file will.
        choices = definition.choices or []
        chosen = definition.default_value if value is None else choices[value]
        thing._send_type = chosen


def _definition_from(spec: AttributeSpec):
    """A cantools definition of a spec, checked."""
    from cantools.database.can.attribute_definition import AttributeDefinition

    if spec.kind not in KINDS:
        raise EditError(
            f"An attribute is of the database, a node, a message or a signal: not {spec.kind}."
        )
    if spec.type_name not in TYPES:
        raise EditError(f"An attribute's type is one of {', '.join(TYPES)}: not {spec.type_name}.")
    if not spec.name:
        raise EditError("An attribute needs a name.")
    numeric = spec.type_name in ("INT", "HEX", "FLOAT")
    whole = spec.type_name != "FLOAT"
    low = spec.minimum if spec.minimum is not None else 0
    high = spec.maximum if spec.maximum is not None else 0
    if numeric:
        low, high = (int(low), int(high)) if whole else (float(low), float(high))
    definition = AttributeDefinition(
        name=spec.name,
        kind=KINDS[spec.kind],
        type_name=spec.type_name,
        minimum=low if numeric else None,
        maximum=high if numeric else None,
        choices=list(spec.choices) if spec.type_name == "ENUM" else None,
    )
    if spec.type_name == "ENUM" and not spec.choices:
        raise EditError(f"{spec.name} is a choice of names, and has none.")
    if spec.default is not None:
        stored = attribute_value(definition, spec.default)
        definition.default_value = (
            definition.choices[stored] if spec.type_name == "ENUM" else stored
        )
    return definition


def _change_definition(database, change) -> None:
    definitions = _definitions(database)
    if isinstance(change, AddAttributeDefinition):
        if change.spec.name in definitions:
            raise EditError(f"There is an attribute called {change.spec.name} already.")
        definitions[change.spec.name] = _definition_from(change.spec)
        return
    name = change.definition
    definition = definitions.get(name)
    if definition is None:
        raise EditError(f"There is no attribute called {name}.")
    if isinstance(change, RemoveAttributeDefinition):
        if name in KEPT_BY_THE_FORMAT:
            raise EditError(f"{name} is kept by the format itself, so it is not removed here.")
        del definitions[name]
        for held in _all_held(database):
            held.pop(name, None)
        return
    new = _definition_from(change.spec)
    if new.name != name:
        if name in KEPT_BY_THE_FORMAT:
            raise EditError(f"{name} is kept by the format itself, so it is not renamed here.")
        if new.name in definitions:
            raise EditError(f"There is an attribute called {new.name} already.")
    uses = sum(name in held for held in _all_held(database))
    if uses and (new.kind != definition.kind or new.type_name != definition.type_name):
        raise EditError(
            f"{name} is set on {uses} thing(s): what it is of, and its type, cannot change "
            "while it is. Take it back to its default on those first."
        )
    # In place, so that every value set keeps pointing at it.
    definition._name = new.name
    definition._kind = new.kind
    definition._type_name = new.type_name
    definition._minimum = new.minimum
    definition._maximum = new.maximum
    definition._choices = new.choices
    definition._default_value = new.default_value
    if new.name != name:
        _rekey(definitions, name, new.name)
        for held in _all_held(database):
            _rekey(held, name, new.name)


# --- what names a node, a signal and a message, in the relation attributes ------------------------
def _dbc_id(message) -> int:
    """A message's identifier as the format writes it, the 29-bit flag in the top bit."""
    return message.frame_id | (0x80000000 if message.is_extended_frame else 0)


def _relations(database):
    return getattr(database.dbc, "relation_attributes", None)


def _rekey(mapping: dict, old, new) -> None:
    """One key renamed, in place and in its place."""
    if old not in mapping:
        return
    items = [(new if k == old else k, v) for k, v in mapping.items()]
    mapping.clear()
    mapping.update(items)


def _rename_node_relations(database, old: str, new: str) -> None:
    if (relations := _relations(database)) is None:
        return
    for by_signal in relations.node_signal_relations.values():
        for by_node in by_signal.values():
            _rekey(by_node, old, new)
    for by_node in relations.node_message_relations.values():
        _rekey(by_node, old, new)


def _drop_node_relations(database, name: str) -> None:
    if (relations := _relations(database)) is None:
        return
    for by_signal in relations.node_signal_relations.values():
        for by_node in by_signal.values():
            by_node.pop(name, None)
    for by_node in relations.node_message_relations.values():
        by_node.pop(name, None)


def _rename_signal_relations(database, message, old: str, new: str) -> None:
    if (relations := _relations(database)) is not None:
        _rekey(relations.node_signal_relations.get(_dbc_id(message), {}), old, new)


def _drop_signal_relations(database, message, name: str) -> None:
    if (relations := _relations(database)) is not None:
        relations.node_signal_relations.get(_dbc_id(message), {}).pop(name, None)


def _rekey_message_relations(database, old: int, new: int) -> None:
    if (relations := _relations(database)) is not None:
        _rekey(relations.node_signal_relations, old, new)
        _rekey(relations.node_message_relations, old, new)


def _drop_message_relations(database, dbc_id: int) -> None:
    if (relations := _relations(database)) is not None:
        relations.node_signal_relations.pop(dbc_id, None)
        relations.node_message_relations.pop(dbc_id, None)


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
        for signal in message.signals:
            chooser = by_name.get(signal.multiplexer_signal or "")
            if chooser is None or chooser.length < 1:
                continue
            past = [v for v in signal.multiplexer_ids or [] if v >= 1 << min(chooser.length, 64)]
            if past:
                found.append(
                    Problem(
                        WARNING,
                        f"{message.name}.{signal.name}",
                        f"sent when {chooser.name} is {values_text(past)}, "
                        f"which its {chooser.length} bits cannot hold",
                    )
                )
        for i, a in enumerate(message.signals):
            for b in message.signals[i + 1 :]:
                if _together(a, b, by_name) and bits(a) & bits(b):
                    found.append(
                        Problem(WARNING, f"{message.name}.{a.name}", f"shares bits with {b.name}")
                    )
    for name, count in names.items():
        if count > 1:
            found.append(Problem(ERROR, name, "two messages of this name"))
    tables = getattr(database.dbc, "value_tables", None) or {}
    for name in tables:
        if not NAME.match(name):
            found.append(Problem(ERROR, f"Value table {name}", "not a name the format allows"))
    found += _node_problems(database)
    found += _group_problems(database)
    found += _attribute_problems(database)
    return found


def _node_problems(database) -> list[Problem]:
    found: list[Problem] = []
    names = [n.name for n in database.nodes]
    for name in dict.fromkeys(names):
        if not NAME.match(name):
            found.append(Problem(ERROR, f"Node {name}", "not a name the format allows"))
        if names.count(name) > 1:
            found.append(Problem(ERROR, f"Node {name}", "two nodes of this name"))
    known = set(names) | {NO_NODE}
    for message in database.messages:
        for sender in message.senders or []:
            if sender not in known:
                found.append(
                    Problem(WARNING, message.name, f"sent by {sender}, which is not a node")
                )
        for signal in message.signals:
            for receiver in signal.receivers:
                if receiver not in known:
                    found.append(
                        Problem(
                            WARNING,
                            f"{message.name}.{signal.name}",
                            f"received by {receiver}, which is not a node",
                        )
                    )
    return found


def _group_problems(database) -> list[Problem]:
    found: list[Problem] = []
    for message in database.messages:
        seen: set[str] = set()
        for group in message.signal_groups or []:
            at = f"{message.name}: signal group {group.name}"
            if not NAME.match(group.name):
                found.append(Problem(ERROR, at, "not a name the format allows"))
            if group.name in seen:
                found.append(Problem(ERROR, at, "two groups of this name"))
            seen.add(group.name)
    return found


def _attribute_problems(database) -> list[Problem]:
    found: list[Problem] = []
    definitions = getattr(database.dbc, "attribute_definitions", None) or {}
    for definition in definitions.values():
        if not NAME.match(definition.name):
            found.append(
                Problem(ERROR, f"Attribute {definition.name}", "not a name the format allows")
            )
    places = [("Database", getattr(database.dbc, "attributes", None) or {})]
    places += [(f"Node {n.name}", n.dbc.attributes) for n in database.nodes if n.dbc is not None]
    for message in database.messages:
        if message.dbc is not None:
            places.append((message.name, message.dbc.attributes))
        places += [
            (f"{message.name}.{s.name}", s.dbc.attributes)
            for s in message.signals
            if s.dbc is not None
        ]
    for at, held in places:
        for name, attribute in held.items():
            definition = attribute.definition
            try:
                attribute_value(definition, attribute.value)
            except EditError:
                found.append(Problem(ERROR, at, f"attribute {name} is {attribute.value!r}"))
                continue
            low, high = definition.minimum, definition.maximum
            numeric = definition.type_name in ("INT", "HEX", "FLOAT")
            if numeric and low is not None and high is not None and low < high:
                if not low <= attribute.value <= high:
                    found.append(
                        Problem(
                            WARNING,
                            at,
                            f"attribute {name} is {attribute.value}, outside {low} to {high}",
                        )
                    )
    return found
