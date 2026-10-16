# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""A CAN database made from a CANopen network: its PDOs, heartbeats and emergencies.

Everything a DBC says about a CANopen network is already known once its
nodes' configuration is: which identifier each PDO goes out on, what is
mapped into it and in what order, what type each object is, and -- where
the EDS or a hook says -- its unit, scaling and named values. Written out
as a DBC, the same frames are named and decoded by any tool that reads one,
and by pycangui on a machine that has no EDS for the device.

Made from nothing, so nothing of anybody's is overwritten, and the result
can be checked exactly: what is written is read back and compared.

Left out, and said so in the file's own comment: SDO and LSS. Their bytes
are protocol rather than signals -- what bytes 4 to 7 of an SDO mean
depends on the command and the index in bytes 0 to 3 -- and a DBC has no
way to say that usefully.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from canopen.objectdictionary import datatypes

from pycangui.canopen.display import Display

#: The DBC node that sends the RPDOs and the network's own messages: the
#: master, which on a bench is the tester.
TESTER = "Tester"
#: A classic frame; a PDO mapping past it is a fault in the configuration.
MOST_BITS = 64
#: Mapping entries that stand for nothing, to leave a gap: the data type
#: objects, 0x0001 to 0x0007, mapped in place of a real one.
DUMMIES = range(0x0001, 0x0008)
#: What a heartbeat's one byte says, by value (CiA 301).
NMT_STATES = {0: "Boot-up", 4: "Stopped", 5: "Operational", 127: "Pre-operational"}
#: The NMT command byte, by value.
NMT_COMMANDS = {
    1: "Start",
    2: "Stop",
    128: "Enter pre-operational",
    129: "Reset node",
    130: "Reset communication",
}
#: A transmission type, said in the PDO's comment where it has no cycle.
SYNC_TYPES = range(1, 241)


@dataclass
class Device:
    """One node of the network, as the database will describe it."""

    #: Its name in the database: a node of the DBC, and the start of each of
    #: its messages' names.
    name: str
    node_id: int
    #: canopen's PdoMap objects, as configured: the live node's, or a file's.
    tpdos: list[Any] = field(default_factory=list)
    rpdos: list[Any] = field(default_factory=list)
    #: (index, sub) -> how to show that object: name, unit, scaling, named values.
    display: Callable[[int, int], Display] = lambda _index, _sub: Display()
    #: Whether it sends a heartbeat. The identifier is fixed by CiA 301, 0x700
    #: plus the node id; whether there is one is the node's configuration.
    heartbeat: bool = False
    #: Where its emergencies go, from 0x1014, which can be anything; None
    #: where that is not known, or emergencies are switched off.
    emcy_cob_id: int | None = None


def from_dictionary(object_dictionary: Any, node_id: int) -> tuple[list[Any], list[Any]]:
    """The TPDOs and RPDOs a DCF configures: those it has a value for.

    Only those. A default is the EDS's, and an EDS's COB-IDs are the
    predefined connection set's suggestion and its mapping what the device
    ships with -- not how any node on any network is set up.

    Read into a node of its own, on a network with no bus, so that nothing
    of a live node's is touched: its maps are what it is decoded and sent
    by, and a file of what it shipped with is not that.
    """
    import canopen

    node = canopen.RemoteNode(node_id, object_dictionary)
    canopen.Network().add_node(node)
    node.tpdo.read(from_od=True)
    node.rpdo.read(from_od=True)

    def configured(pdo_map) -> bool:
        return (
            pdo_map.com_record[1].od.value is not None and pdo_map.map_array[0].od.value is not None
        )

    return (
        [m for m in node.tpdo.map.values() if configured(m)],
        [m for m in node.rpdo.map.values() if configured(m)],
    )


def configured_value(object_dictionary: Any, index: int) -> int | None:
    """An object's value in a DCF, as a number; None where it has none."""
    try:
        value = object_dictionary[index].value
    except (KeyError, TypeError, AttributeError):
        return None
    return value if isinstance(value, int) else None


#: Bit 31 of an EMCY COB-ID: set, the node sends no emergencies.
NOT_VALID = 0x80000000


def emcy_id(cob_id: int | None) -> int | None:
    """Where emergencies go, from 0x1014's value; None if they are switched off."""
    if cob_id is None or cob_id & NOT_VALID:
        return None
    return cob_id & 0x1FFFFFFF


def dbc_name(text: str, fallback: str = "Signal") -> str:
    """A name a DBC will take: letters, digits and underscores, not starting
    with a digit. "Motor speed (rpm)" is Motor_speed_rpm."""
    name = re.sub(r"[^A-Za-z0-9_]+", "_", text).strip("_")
    if not name:
        return fallback
    return f"_{name}" if name[0].isdigit() else name


def _unique(name: str, taken: set[str]) -> str:
    if name not in taken:
        taken.add(name)
        return name
    number = 2
    while f"{name}_{number}" in taken:
        number += 1
    taken.add(f"{name}_{number}")
    return f"{name}_{number}"


def _signal(start: int, var: Any, display: Display, name: str, receivers: list[str]):
    from cantools.database import Signal
    from cantools.database.conversion import BaseConversion

    data_type = getattr(var.od, "data_type", None)
    is_float = data_type in datatypes.FLOAT_TYPES and var.length in (32, 64)
    choices = dict(display.choices) or None

    def physical(raw: float | None) -> float | None:
        return None if raw is None else raw * display.factor + display.offset

    low, high = physical(display.low), physical(display.high)
    if low is not None and high is not None and low > high:  # a negative factor
        low, high = high, low
    return Signal(
        name=name,
        start=start,
        length=var.length,
        byte_order="little_endian",  # CANopen is little-endian throughout
        is_signed=data_type in datatypes.SIGNED_TYPES,
        conversion=BaseConversion.factory(
            scale=display.factor, offset=display.offset, choices=choices, is_float=is_float
        ),
        minimum=low,
        maximum=high,
        unit=display.unit or None,
        comment=f"{var.od.index:04X}:{var.od.subindex:02X} {var.name}"
        + (f". {display.description}" if display.description else ""),
        receivers=receivers,
    )


def _cycle(pdo: Any) -> tuple[int | None, str]:
    """The cycle time a DBC can give, and what to say about one it cannot."""
    kind = pdo.trans_type
    if kind is not None and kind >= 254:
        if pdo.event_timer:
            return int(pdo.event_timer), f"event-driven, every {pdo.event_timer} ms at most"
        return None, "event-driven"
    if kind == 0:
        return None, "on SYNC, when it has changed"
    if kind in SYNC_TYPES:
        return None, "every SYNC" if kind == 1 else f"every {kind} SYNCs"
    if kind in (252, 253):
        return None, "on request (RTR)"
    return None, ""


@dataclass
class Made:
    """What came of it: the database, and what was left out of it and why."""

    database: Any
    notes: list[str] = field(default_factory=list)


def build(devices: Iterable[Device], network: bool = True) -> Made:
    """The database for these devices. ``network`` adds NMT, SYNC and TIME,
    which belong to the network rather than to any node of it."""
    from cantools.database import Database, Message, Signal
    from cantools.database.can.node import Node

    devices = list(devices)
    notes: list[str] = []
    messages: dict[int, Any] = {}  # frame id -> message
    sent_by: dict[int, str] = {}
    names: set[str] = set()

    def add(message, sender: str) -> bool:
        if message.frame_id in messages:
            notes.append(
                f"{message.name}: left out, since {messages[message.frame_id].name} is on "
                f"the same identifier, 0x{message.frame_id:X}"
            )
            return False
        messages[message.frame_id] = message
        sent_by[message.frame_id] = sender
        return True

    def pdo_message(device: Device, pdo: Any, sender: str, receivers: list[str]):
        if pdo.cob_id is None or not pdo.enabled:
            return None
        signals, taken, at = [], set(), 0
        for var in pdo.map:
            if var.od.index in DUMMIES:
                at += var.length  # a gap, kept as one
                continue
            shown = device.display(var.od.index, var.od.subindex)
            # The entry's own name, not "Record.Entry": the record is said
            # in the comment, with the index.
            name = _unique(dbc_name(shown.name or var.od.name or var.name), taken)
            signals.append(_signal(at, var, shown, name, receivers))
            at += var.length
        if not signals:
            return None
        # canopen calls them TxPDO1_node5; the node is in the name already.
        name = f"{device.name}_{re.sub(r'_node\d+$', '', pdo.name)}"
        if at > MOST_BITS:
            notes.append(f"{name}: left out, its mapping is {at} bits, more than a frame holds")
            return None
        cycle, how = _cycle(pdo)
        return Message(
            frame_id=pdo.cob_id,
            name=_unique(dbc_name(name, "PDO"), names),
            length=(at + 7) // 8,
            signals=signals,
            senders=[sender],
            cycle_time=cycle,
            comment=f"{re.sub(r'_node\d+$', '', pdo.name)} of node {device.node_id}"
            + (f", {how}" if how else ""),
            is_extended_frame=pdo.cob_id > 0x7FF,
            strict=False,
        )

    for device in devices:
        for pdo in device.tpdos:
            message = pdo_message(device, pdo, device.name, [TESTER])
            if message is not None:
                add(message, device.name)
    for device in devices:
        for pdo in device.rpdos:
            existing = messages.get(pdo.cob_id) if pdo.enabled else None
            if existing is not None and sent_by[pdo.cob_id] != TESTER:
                # Another node's TPDO is this node's RPDO: one frame, sent by
                # one and received by both. Said as a receiver, not twice.
                for signal in existing.signals:
                    if device.name not in signal.receivers:
                        signal.receivers.append(device.name)
                continue
            message = pdo_message(device, pdo, TESTER, [device.name])
            if message is not None:
                add(message, TESTER)
    for device in devices:
        if not 1 <= device.node_id <= 127 or not device.heartbeat:
            continue
        add(
            Message(
                frame_id=0x700 + device.node_id,
                name=_unique(f"{device.name}_Heartbeat", names),
                length=1,
                signals=[
                    Signal(
                        "NmtState",
                        0,
                        8,
                        conversion=_choices(NMT_STATES),
                        receivers=[TESTER],
                    )
                ],
                senders=[device.name],
                comment=f"Heartbeat and boot-up of node {device.node_id}",
                strict=False,
            ),
            device.name,
        )
    for device in devices:
        if device.emcy_cob_id is None:
            continue
        add(
            Message(
                frame_id=device.emcy_cob_id,
                name=_unique(f"{device.name}_EMCY", names),
                length=8,
                signals=[
                    Signal("ErrorCode", 0, 16, receivers=[TESTER]),
                    Signal("ErrorRegister", 16, 8, receivers=[TESTER]),
                    Signal("ManufacturerError", 24, 40, receivers=[TESTER]),
                ],
                senders=[device.name],
                comment=f"Emergency from node {device.node_id}",
                is_extended_frame=device.emcy_cob_id > 0x7FF,
                strict=False,
            ),
            device.name,
        )
    if network:
        everyone = [d.name for d in devices]
        add(
            Message(
                frame_id=0x000,
                name=_unique("NMT", names),
                length=2,
                signals=[
                    Signal("Command", 0, 8, conversion=_choices(NMT_COMMANDS), receivers=everyone),
                    Signal("NodeId", 8, 8, comment="0 for every node", receivers=everyone),
                ],
                senders=[TESTER],
                comment="Network management",
                strict=False,
            ),
            TESTER,
        )
        add(
            Message(
                frame_id=0x080,
                name=_unique("SYNC", names),
                length=1,
                signals=[
                    Signal(
                        "Counter",
                        0,
                        8,
                        comment="Only where a SYNC counter is used",
                        receivers=everyone,
                    )
                ],
                senders=[TESTER],
                comment="SYNC. Without a counter it has no data at all.",
                strict=False,
            ),
            TESTER,
        )
        add(
            Message(
                frame_id=0x100,
                name=_unique("TIME", names),
                length=6,
                signals=[
                    Signal(
                        "Milliseconds",
                        0,
                        28,
                        unit="ms",
                        comment="since midnight",
                        receivers=everyone,
                    ),
                    Signal(
                        "Days",
                        32,
                        16,
                        unit="days",
                        comment="since 1 January 1984",
                        receivers=everyone,
                    ),
                ],
                senders=[TESTER],
                comment="TIME stamp",
                strict=False,
            ),
            TESTER,
        )
    nodes = [Node(TESTER)] + [Node(d.name) for d in devices]
    database = Database(
        messages=sorted(messages.values(), key=lambda m: m.frame_id),
        nodes=nodes,
        strict=False,
    )
    return Made(database, notes)


def _choices(table: dict[int, str]):
    from cantools.database.conversion import BaseConversion

    return BaseConversion.factory(choices=dict(table))


def as_dbc(made: Made) -> str:
    return made.database.as_dbc_string()


def differences(made: Made, text: str) -> list[str]:
    """What reading ``text`` back gives that the database did not have: the
    check that the file written says what was meant. Empty when it does."""
    import cantools.database

    back = cantools.database.load_string(text, database_format="dbc", strict=False)
    found = []
    theirs = {m.frame_id: m for m in back.messages}
    for mine in made.database.messages:
        other = theirs.get(mine.frame_id)
        if other is None:
            found.append(f"{mine.name}: not in the file")
            continue
        if (other.name, other.length) != (mine.name, mine.length):
            found.append(f"{mine.name}: read back as {other.name}, {other.length} bytes")
        signals = {s.name: s for s in other.signals}
        for signal in mine.signals:
            got = signals.get(signal.name)
            wanted = _described(signal)
            if got is None:
                found.append(f"{mine.name}.{signal.name}: not in the file")
            elif _described(got) != wanted:
                found.append(f"{mine.name}.{signal.name}: read back differently")
    return found


def _described(signal) -> tuple:
    choices = {int(k): str(v) for k, v in (signal.choices or {}).items()}
    return (
        signal.start,
        signal.length,
        signal.byte_order,
        signal.is_signed,
        signal.is_float,
        round(signal.scale, 12),
        round(signal.offset, 12),
        signal.unit or "",
        choices,
    )


def write(made: Made, path: str | Path) -> list[str]:
    """Write the database, then read it back: what differs, empty if nothing."""
    text = as_dbc(made)
    # As cantools wrote it: its lines already end CR LF, and written as text
    # on Windows each gained a second CR.
    Path(path).write_text(text, encoding="utf-8", newline="")
    return differences(made, Path(path).read_text(encoding="utf-8"))
