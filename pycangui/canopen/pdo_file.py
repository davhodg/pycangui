# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""A file's PDO configuration: read out of its objects, and put back into them.

A node's PDOs are read and written over SDO by the ``canopen`` package. A DCF
or EDS open with no node has the same four records -- communication at 0x1400
(receive) and 0x1800 (transmit), mapping at 0x1600 and 0x1A00 -- as values in
a file, so the same ``PdoConfig`` is decoded from them here, and an edited one
is put back as values. They are held like any other edit to the file, and go
to disk when the file is saved.

Only what changed is put back. An EDS usually gives a COB-ID as
``$NODEID+0x180``, which is not a number until there is a node ID; writing the
same PDO back untouched must leave that text alone, and does.
"""

from __future__ import annotations

import re
from pathlib import Path

from pycangui.canopen import PdoConfig, PdoEntry

#: Direction, first communication record, first mapping record.
RECORDS = (("TPDO", 0x1800, 0x1A00), ("RPDO", 0x1400, 0x1600))
HOW_MANY = 0x200

#: COB-ID bits (CiA 301): 31 set means the PDO is not used, 30 no RTR, 29 a
#: 29-bit identifier. 30 and 29 are kept as the file has them.
NOT_USED = 0x8000_0000
KEPT = 0x6000_0000
IDENTIFIER = 0x1FFF_FFFF

_SECTION = re.compile(r"^\[([0-9A-Fa-f]{4})sub([0-9A-Fa-f]+)\]$")
_VALUE = re.compile(r"^(ParameterValue|DefaultValue)\s*=\s*(.*)$", re.IGNORECASE)
_NODE_ID = re.compile(r"\+?\$NODEID\+?", re.IGNORECASE)


def relative_cob_ids(path: Path) -> dict[int, str]:
    """Communication records whose COB-ID the file gives relative to the node
    ID, as the text it gives: ``{0x1800: "$NODEID+0x180"}``."""
    try:
        text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return {}
    found: dict[int, dict[str, str]] = {}
    where: int | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            match = _SECTION.match(stripped)
            where = None
            if match is not None and int(match.group(2), 16) == 1:
                index = int(match.group(1), 16)
                if any(base <= index < base + HOW_MANY for _d, base, _m in RECORDS):
                    where = index
        elif where is not None and (value := _VALUE.match(stripped)) is not None:
            found.setdefault(where, {})[value.group(1).lower()] = value.group(2).strip()
    out = {}
    for index, values in found.items():
        said = values.get("parametervalue") or values.get("defaultvalue", "")
        if "$NODEID" in said.upper():
            out[index] = said
    return out


def _base(text: str) -> int | None:
    """``$NODEID+0x180`` without the node ID: 0x180."""
    try:
        return int(_NODE_ID.sub("", text.replace(" ", "")) or "0", 0)
    except ValueError:
        return None


def _value(source, index: int, sub: int) -> int | None:
    raw, _error = source.current(index, sub)
    return raw if isinstance(raw, int) else None


def _cob_raw(source, index: int, relative: dict[int, str]) -> int | None:
    """The COB-ID entry as a number: the file's, or its base where it is relative."""
    raw = _value(source, index, 1)
    if raw is None and index in relative:
        raw = _base(relative[index])
    return raw


def _name(dictionary, index: int, sub: int) -> str:
    try:
        return dictionary.get_variable(index, sub).name
    except Exception:
        return ""


def configs(source) -> list[PdoConfig]:
    """Every PDO the file has a communication record for."""
    dictionary = source.object_dictionary
    if dictionary is None:
        return []
    relative = relative_cob_ids(source.path)
    out: list[PdoConfig] = []
    for direction, comm_base, map_base in RECORDS:
        for index in sorted(i for i in dictionary if comm_base <= i < comm_base + HOW_MANY):
            raw = _cob_raw(source, index, relative)
            if raw is None:
                continue
            number = index - comm_base + 1
            mapping = map_base + number - 1
            entries = []
            for sub in range(1, (_value(source, mapping, 0) or 0) + 1):
                word = _value(source, mapping, sub)
                if word:
                    at, at_sub, bits = word >> 16, (word >> 8) & 0xFF, word & 0xFF
                    entries.append(PdoEntry(at, at_sub, bits, _name(dictionary, at, at_sub)))
            inhibit = _value(source, index, 3)
            out.append(
                PdoConfig(
                    node_id=0,
                    direction=direction,
                    number=number,
                    name=getattr(dictionary[index], "name", ""),
                    cob_id=raw & IDENTIFIER,
                    enabled=not raw & NOT_USED,
                    transmission_type=_value(source, index, 2),
                    inhibit_time_us=None if inhibit is None else inhibit * 100,
                    event_timer_ms=_value(source, index, 5),
                    entries=entries,
                    cob_id_text=relative.get(index, "") if _value(source, index, 1) is None else "",
                )
            )
    return out


def apply(source, config: PdoConfig) -> str:
    """Put one PDO into the file's values. Returns why not, or "" when done."""
    dictionary = source.object_dictionary
    comm_base, map_base = next((c, m) for d, c, m in RECORDS if d == config.direction)
    index = comm_base + config.number - 1
    mapping = map_base + config.number - 1
    if dictionary is None or index not in dictionary:
        return f"{config.direction}{config.number} is not in this file"
    room = [sub for sub in dictionary[mapping] if sub] if mapping in dictionary else []
    if len(config.entries) > len(room):
        return (
            f"{config.direction}{config.number} has room for {len(room)} mapped "
            f"object(s) in this file, and {len(config.entries)} are mapped"
        )

    wanted: dict[tuple[int, int], int] = {}
    was = _cob_raw(source, index, relative_cob_ids(source.path)) or 0
    wanted[(index, 1)] = (
        (was & KEPT) | (config.cob_id & IDENTIFIER) | (0 if config.enabled else NOT_USED)
    )
    if config.transmission_type is not None:
        wanted[(index, 2)] = config.transmission_type
    if config.inhibit_time_us is not None:
        wanted[(index, 3)] = config.inhibit_time_us // 100
    if config.event_timer_ms is not None:
        wanted[(index, 5)] = config.event_timer_ms
    for sub, entry in zip(room, config.entries, strict=False):
        wanted[(mapping, sub)] = (entry.index << 16) | (entry.subindex << 8) | entry.bits
    if mapping in dictionary:
        wanted[(mapping, 0)] = len(config.entries)

    for (at, sub), value in wanted.items():
        if sub not in dictionary[at]:
            continue  # many devices have only sub-indices 1 and 2 of the record
        now = was if (at, sub) == (index, 1) else _value(source, at, sub)
        if now != value:
            source.write(at, sub, value)
    return ""
