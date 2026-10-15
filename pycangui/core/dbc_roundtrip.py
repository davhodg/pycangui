# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""What a DBC file loses when cantools reads it and writes it back.

A database written by pycangui is written by cantools, and cantools keeps
what it understands: anything else in the file is gone from the copy, and
what it does keep may come back in another order or another shape. Before a
file of somebody's is edited and saved, it is worth knowing which.

So this reads a file, writes it, reads the result, and compares the two
three ways: what each statement of the format appears as and how often
(``BA_REL_`` four times before, none after); what the two databases hold,
object by object; and the order things are in.

Run on a file, it says what kinds of thing differ and how many, and names
nothing in the file unless asked to -- so that a report on a file that may not
leave its owner's computer can be read out without giving the file away::

    python -m pycangui.core.dbc_roundtrip some.dbc other.dbc
    python -m pycangui.core.dbc_roundtrip --names some.dbc
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: The format's statements, as each begins a line. CM_ and BA_ are split by
#: what they are about, since a comment kept on messages and lost on nodes is
#: two different findings.
KEYWORDS = (
    "VERSION",
    "NS_",
    "BS_",
    "BU_",
    "VAL_TABLE_",
    "BO_",
    "SG_",
    "BO_TX_BU_",
    "EV_",
    "ENVVAR_DATA_",
    "SGTYPE_",
    "SIG_TYPE_REF_",
    "CM_",
    "BA_DEF_",
    "BA_DEF_REL_",
    "BA_DEF_DEF_",
    "BA_DEF_DEF_REL_",
    "BA_",
    "BA_REL_",
    "VAL_",
    "SIG_VALTYPE_",
    "SIG_GROUP_",
    "SG_MUL_VAL_",
    "CAT_DEF_",
    "CAT_",
    "FILTER",
)
#: What a CM_ or BA_ is about, by the word after it.
TARGETS = ("BU_", "BO_", "SG_", "EV_")
_STATEMENT = re.compile(r"^\s*([A-Z_]+)\b(?:\s+(?:\"[^\"]*\"\s+)?([A-Z_]+)\b)?", re.MULTILINE)


@dataclass(frozen=True)
class Finding:
    """One thing that is not the same after the round trip."""

    #: What kind: "statement", "message", "signal", "node", "attribute",
    #: "value table", "signal group", "environment variable", "order".
    kind: str
    #: Where, by name; left out of a report unless names are asked for.
    where: str
    #: What differs, in words that name nothing in the file.
    what: str


def statements(text: str) -> Counter:
    """How many of each statement there are: "CM_ BO_" for a comment on a
    message, "BA_ SG_" for an attribute on a signal, "BA_" for one on the
    network, and so on."""
    found: Counter = Counter()
    in_ns = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            in_ns = False
            continue
        if in_ns and line[:1] in (" ", "\t"):
            continue  # the list under NS_ names statements; it is not them
        match = _STATEMENT.match(line)
        if match is None or match.group(1) not in KEYWORDS:
            continue
        keyword = match.group(1)
        in_ns = keyword == "NS_"
        target = match.group(2)
        if keyword in ("CM_", "BA_") and target in TARGETS:
            keyword = f"{keyword} {target}"
        found[keyword] += 1
    return found


def _order(text: str, keyword: str) -> list[str]:
    pattern = re.compile(rf"^\s*{keyword}\s+(\S+)", re.MULTILINE)
    return [m.group(1).rstrip(":") for m in pattern.finditer(text)]


def _signal_fields(signal: Any) -> dict[str, Any]:
    choices = {int(k): str(v) for k, v in (signal.choices or {}).items()}
    return {
        "start": signal.start,
        "length": signal.length,
        "byte order": signal.byte_order,
        "signedness": signal.is_signed,
        "float": signal.is_float,
        "scale": signal.scale,
        "offset": signal.offset,
        "minimum": signal.minimum,
        "maximum": signal.maximum,
        "unit": signal.unit or "",
        "named values": choices,
        "comment": signal.comment or "",
        "receivers": sorted(signal.receivers or []),
        "multiplexer": signal.is_multiplexer,
        "multiplexer values": sorted(signal.multiplexer_ids or []),
        "multiplexed by": signal.multiplexer_signal or "",
        "initial value": signal.raw_initial,
        "invalid value": signal.raw_invalid,
    }


def _message_fields(message: Any) -> dict[str, Any]:
    return {
        "name": message.name,
        "length": message.length,
        "extended": message.is_extended_frame,
        "CAN FD": message.is_fd,
        "cycle time": message.cycle_time,
        "send type": message.send_type,
        "senders": sorted(message.senders or []),
        "comment": message.comment or "",
        "signal groups": sorted(
            (g.name, tuple(g.signal_names)) for g in (message.signal_groups or [])
        ),
    }


def _attributes(owner: Any) -> dict[str, Any]:
    dbc = getattr(owner, "dbc", None)
    attributes = getattr(dbc, "attributes", None) or {}
    return {name: getattr(a, "value", a) for name, a in attributes.items()}


def _definitions(database: Any) -> dict[str, tuple]:
    dbc = getattr(database, "dbc", None)
    out = {}
    for name, d in (getattr(dbc, "attribute_definitions", None) or {}).items():
        out[name] = (
            d.kind,
            d.type_name,
            d.minimum,
            d.maximum,
            tuple(d.choices or ()),
            d.default_value,
        )
    return out


def compare(before: Any, after: Any, text_before: str, text_after: str) -> list[Finding]:
    """What differs between a database and its copy, and between their texts."""
    found: list[Finding] = []
    counts_before, counts_after = statements(text_before), statements(text_after)
    for keyword in sorted(set(counts_before) | set(counts_after)):
        was, now = counts_before[keyword], counts_after[keyword]
        if was != now:
            found.append(Finding("statement", keyword, f"{keyword}: {was} before, {now} after"))

    theirs = {(m.frame_id, m.is_extended_frame): m for m in after.messages}
    for mine in before.messages:
        other = theirs.get((mine.frame_id, mine.is_extended_frame))
        if other is None:
            found.append(Finding("message", mine.name, "a message is missing"))
            continue
        a, b = _message_fields(mine), _message_fields(other)
        for field, value in a.items():
            if b[field] != value:
                found.append(Finding("message", mine.name, f"a message's {field} differs"))
        if _attributes(mine) != _attributes(other):
            found.append(Finding("attribute", mine.name, "a message's attributes differ"))
        signals = {s.name: s for s in other.signals}
        for signal in mine.signals:
            copy = signals.get(signal.name)
            where = f"{mine.name}.{signal.name}"
            if copy is None:
                found.append(Finding("signal", where, "a signal is missing"))
                continue
            x, y = _signal_fields(signal), _signal_fields(copy)
            for field, value in x.items():
                if y[field] != value:
                    found.append(Finding("signal", where, f"a signal's {field} differs"))
            if _attributes(signal) != _attributes(copy):
                found.append(Finding("attribute", where, "a signal's attributes differ"))
        order_a = [s.name for s in mine.signals]
        order_b = [s.name for s in other.signals if s.name in set(order_a)]
        if order_a != order_b:
            found.append(Finding("order", mine.name, "a message's signals are in another order"))

    nodes = {n.name: n for n in after.nodes}
    for node in before.nodes:
        copy = nodes.get(node.name)
        if copy is None:
            found.append(Finding("node", node.name, "a node is missing"))
        elif (node.comment or "") != (copy.comment or ""):
            found.append(Finding("node", node.name, "a node's comment differs"))
        elif _attributes(node) != _attributes(copy):
            found.append(Finding("attribute", node.name, "a node's attributes differ"))

    if _attributes(before) != _attributes(after):
        found.append(Finding("attribute", "network", "the network's attributes differ"))
    if _definitions(before) != _definitions(after):
        found.append(Finding("attribute", "definitions", "attribute definitions differ"))
    tables_a = getattr(getattr(before, "dbc", None), "value_tables", None) or {}
    tables_b = getattr(getattr(after, "dbc", None), "value_tables", None) or {}
    if {k: dict(v) for k, v in tables_a.items()} != {k: dict(v) for k, v in tables_b.items()}:
        found.append(Finding("value table", "value tables", "value tables differ"))
    envs_a = getattr(getattr(before, "dbc", None), "environment_variables", None) or {}
    envs_b = getattr(getattr(after, "dbc", None), "environment_variables", None) or {}
    if set(envs_a) != set(envs_b):
        found.append(Finding("environment variable", "environment", "environment variables differ"))
    if _order(text_before, "BO_") != _order(text_after, "BO_"):
        found.append(Finding("order", "messages", "messages are in another order"))
    return found


def round_trip(path: str | Path) -> list[Finding]:
    """Read a DBC, write it with cantools, read that, and say what differs.

    A file cantools cannot read at all is one finding of its own.
    """
    import cantools.database

    text = Path(path).read_text(encoding="utf-8", errors="replace")
    try:
        # In the file's own order: by default cantools sorts signals by start
        # bit, which is a change made on reading before any on writing.
        before = cantools.database.load_string(
            text, database_format="dbc", strict=False, sort_signals=None
        )
    except Exception as exc:  # cantools raises several kinds
        return [Finding("unreadable", str(path), f"cantools cannot read it: {_first_line(exc)}")]
    written = before.as_dbc_string()
    after = cantools.database.load_string(
        written, database_format="dbc", strict=False, sort_signals=None
    )
    return compare(before, after, text, written)


def _first_line(exc: Exception) -> str:
    """The parser's complaint without the text it quotes from the file."""
    message = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
    return re.sub(r'".*"', '"..."', message)


def report(path: str | Path, findings: list[Finding], names: bool = False) -> str:
    """The findings for one file, as kinds and counts -- or one a line, named."""
    lines = [f"{Path(path).name}: " + ("nothing lost" if not findings else f"{len(findings)}")]
    if names:
        lines += [f"  {f.kind}: {f.where}: {f.what}" for f in findings]
    else:
        for what, count in sorted(Counter(f.what for f in findings).items()):
            lines.append(f"  {what}" + (f" (x{count})" if count > 1 else ""))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    names = "--names" in argv
    files = [a for a in argv if a != "--names"]
    if not files:
        print(__doc__)
        return 2
    for path in files:
        print(report(path, round_trip(path), names))
    return 0


if __name__ == "__main__":
    sys.exit(main())
