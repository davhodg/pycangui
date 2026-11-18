# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""What a custom pane is, and the file it is kept in.

A custom pane is a title and a list of fields; a field is one object dictionary
entry with a label and a way of being shown. The seven ways are the whole
vocabulary, and they are enough to express every configuration screen in a
manufacturer's tool: a range-checked number, a hex code, a named choice, a
word of flags, a field packed into some bits of a larger object, an XY map,
and a value that is only read.

Kept as indented JSON in the workspace, beside the hooks that say what the
objects mean. That is deliberate: a custom pane is knowledge about a product, the
same as the hooks and the EDS, and all three should travel together. It is
also why the index is written as ``"0x2001"`` rather than as 8193 -- nobody
speaks about a CANopen object in decimal, and the file is meant to be opened
in a text editor.
"""

from __future__ import annotations

import json
import re
from collections.abc import Hashable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from pycangui.canopen.display import Display, with_overrides
from pycangui.core import workspaces

#: How a field is shown, and therefore how it is edited.
#:
#: ``value``  read-only, whatever the object says
#: ``number`` a number in its own units, refused if the EDS says it is out of range
#: ``hex``    the same, written and read in hex, for codes rather than quantities
#: ``enum``   a dropdown of the values that have names
#: ``flags``  one named tick per bit of the object
#: ``bits``   a field packed into some of the bits of a larger object
#: ``map``    an array object as an editable XY table beside its graph
#: ``table``  an A2L curve or map, opened in the XCP pane's table window
#: ``text``   text, typed and written as it is: a UDS identifier, a string
KINDS = ("value", "number", "hex", "enum", "flags", "bits", "map", "table", "text")

#: What a pane is for. One protocol to a pane: its fields are addressed the
#: way that protocol addresses things, and only its sources are offered.
CANOPEN = "canopen"
XCP = "xcp"
UDS = "uds"
PROTOCOLS = (CANOPEN, XCP, UDS)
#: The kinds that only make sense on CANopen: a map is an array object, one
#: sub-index a point.
CANOPEN_ONLY = ("map",)
#: And on XCP: a curve or a map is the A2L's, by name, with its axes.
XCP_ONLY = ("table",)
MAX_DID = 0xFFFF

MAX_INDEX = 0xFFFF
MAX_SUB = 0xFF
#: A pane name is a file name, so it has to survive being one.
_ALLOWED_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]*$")
MAX_NAME = 64
SUFFIX = ".json"


@dataclass(frozen=True)
class Field:
    """One object on a pane, and how it is shown.

    Addressed one of three ways, by the pane's protocol: ``index`` and ``sub``
    on CANopen, ``name`` (the A2L's) on XCP and CCP, ``did`` on UDS. ``key``
    is whichever it is, and it is all the pane, the widgets and the poller ever
    see; only the source looks inside it.
    """

    index: int = 0
    sub: int = 0
    kind: str = "number"
    #: What to call it here. Empty means whatever the source says it is
    #: called, which is usually right and occasionally unreadable.
    label: str = ""

    # --- meaning, where the pane knows better than the file ---------------------
    #: These sit on top of whatever the EDS and the display hook produced, for
    #: the case the hook cannot cover: one object that means something
    #: particular *on this pane*. Left unset, the source's answer stands.
    unit: str = ""
    factor: float | None = None
    offset: float | None = None
    decimals: int | None = None
    low: float | None = None
    high: float | None = None
    #: Raw value -> what it means, for ``enum`` and ``bits``.
    choices: dict[int, str] = field(default_factory=dict)

    # --- flags and bits ----------------------------------------------------------
    #: ``flags``: bit number -> what that bit means. Only the named ones are
    #: shown; a word with three meaningful bits should not display 32 ticks.
    bits: dict[int, str] = field(default_factory=dict)
    #: ``bits``: the least significant bit of the field, and how wide it is.
    first: int = 0
    width: int = 0

    # --- map ----------------------------------------------------------------------
    #: ``map``: the array object holding the X values, where there is one.
    #: Without it the sub-index number is the X, which is what an array of
    #: breakpoints usually means.
    x_index: int | None = None
    x_sub: int = 0
    x_label: str = ""
    y_label: str = ""

    # --- other protocols ------------------------------------------------------------
    #: XCP and CCP: the measurement or characteristic, by its A2L name.
    name: str = ""
    #: UDS: the data identifier.
    did: int | None = None

    @property
    def where(self) -> tuple[int, int]:
        """CANopen's address, whatever the field is: see ``key``."""
        return (self.index, self.sub)

    @property
    def key(self) -> Hashable:
        """What the source is asked for: the name, the DID, or (index, sub)."""
        if self.name:
            return self.name
        if self.did is not None:
            return self.did
        return (self.index, self.sub)

    @property
    def mask(self) -> int:
        """Which bits of the object this field occupies. ``bits`` only."""
        return ((1 << self.width) - 1) << self.first if self.width else 0

    def extract(self, raw: int) -> int:
        """This field's value, out of the object it is packed into."""
        return (int(raw) & self.mask) >> self.first if self.width else int(raw)

    def insert(self, raw: int, value: int) -> int:
        """The object with this field replaced.

        Read-modify-write, and there is no other way: three bits of a 32 bit
        word cannot be written without the other twenty-nine, so a pane that
        did not read first would zero everything it was not showing.
        """
        if not self.width:
            return int(value)
        return (int(raw) & ~self.mask) | ((int(value) << self.first) & self.mask)


@dataclass
class CustomPane:
    """A named group of objects, laid out as a form."""

    title: str = ""
    description: str = ""
    fields: list[Field] = field(default_factory=list)
    #: The node this pane opens against, where it has a usual one. A pane is
    #: bound to a *source* rather than to a node -- the same pane serves a
    #: live node, a DCF and an EDS's defaults -- so this is only a default.
    node: int | None = None
    #: Which protocol's sources it is for. CANopen unless the file says.
    protocol: str = CANOPEN

    def with_field(self, new: Field) -> CustomPane:
        return replace(self, fields=[*self.fields, new])


# --- the file --------------------------------------------------------------------------
def _number(value: Any, default: int = 0) -> int:
    """8193, "0x2001" or "2001h" -- a file people edit gets all three."""
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    text = str(value).strip().lower()
    if text.endswith("h"):
        text = "0x" + text[:-1]
    try:
        return int(text, 0)
    except ValueError:
        return default


def _int_keys(value: Any) -> dict[int, str]:
    """JSON has no integer keys, so they come back as strings."""
    if not isinstance(value, dict):
        return {}
    return {_number(k): str(v) for k, v in value.items()}


def field_from_dict(data: dict) -> Field:
    """One field, forgiving of a hand-edited file.

    Anything unreadable falls back to the default rather than raising: a typo
    in one field of one pane should cost that field, not the pane and not
    the window that was opening it.
    """
    kind = str(data.get("kind", "number"))
    return Field(
        index=_number(data.get("index")),
        sub=_number(data.get("sub"), 0),
        kind=kind if kind in KINDS else "value",
        label=str(data.get("label", "")),
        unit=str(data.get("unit", "")),
        factor=_optional_float(data.get("factor")),
        offset=_optional_float(data.get("offset")),
        decimals=None if data.get("decimals") is None else _number(data.get("decimals")),
        low=_optional_float(data.get("low")),
        high=_optional_float(data.get("high")),
        choices=_int_keys(data.get("choices")),
        bits=_int_keys(data.get("bits")),
        first=_number(data.get("first"), 0),
        width=_number(data.get("width"), 0),
        x_index=None if data.get("x_index") is None else _number(data.get("x_index")),
        x_sub=_number(data.get("x_sub"), 0),
        x_label=str(data.get("x_label", "")),
        y_label=str(data.get("y_label", "")),
        name=str(data.get("name", "")),
        did=None if data.get("did") is None else _number(data.get("did")),
    )


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def field_to_dict(item: Field) -> dict:
    """Only what was actually said, so the file stays readable.

    A field written out with all twenty keys, eighteen of them at their
    defaults, is a file nobody will edit twice.
    """
    out: dict[str, Any]
    if item.name:
        out = {"name": item.name, "kind": item.kind}
    elif item.did is not None:
        out = {"did": f"0x{item.did:04X}", "kind": item.kind}
    else:
        out = {"index": f"0x{item.index:04X}", "kind": item.kind}
        if item.sub:
            out["sub"] = item.sub
    if item.label:
        out["label"] = item.label
    for name in ("unit", "x_label", "y_label"):
        if getattr(item, name):
            out[name] = getattr(item, name)
    for name in ("factor", "offset", "decimals", "low", "high"):
        if getattr(item, name) is not None:
            out[name] = getattr(item, name)
    for name in ("choices", "bits"):
        if getattr(item, name):
            out[name] = {str(k): v for k, v in sorted(getattr(item, name).items())}
    if item.width:
        out["first"], out["width"] = item.first, item.width
    if item.x_index is not None:
        out["x_index"] = f"0x{item.x_index:04X}"
        if item.x_sub:
            out["x_sub"] = item.x_sub
    return out


def pane_from_dict(data: dict) -> CustomPane:
    raw_fields = data.get("fields")
    return CustomPane(
        title=str(data.get("title", "")),
        description=str(data.get("description", "")),
        node=None if data.get("node") is None else _number(data.get("node")),
        # Kept as written even when unknown, so problems() can say so rather
        # than the pane quietly turning into a CANopen one.
        protocol=str(data.get("protocol", CANOPEN)).strip().lower() or CANOPEN,
        fields=[field_from_dict(f) for f in raw_fields if isinstance(f, dict)]
        if isinstance(raw_fields, list)
        else [],
    )


def pane_to_dict(pane: CustomPane) -> dict:
    out: dict[str, Any] = {"title": pane.title}
    if pane.protocol != CANOPEN:
        out["protocol"] = pane.protocol  # so a CANopen pane's file is as it always was
    if pane.description:
        out["description"] = pane.description
    if pane.node is not None:
        out["node"] = pane.node
    out["fields"] = [field_to_dict(f) for f in pane.fields]
    return out


def display_for(item: Field, base: Display) -> Display:
    """What the source said about the object, with the pane's word on top.

    The pane wins where it speaks, because it is the more specific statement:
    the hook says what an object means on this product, and the pane says what
    it means *on this screen*, which is occasionally narrower. Everything the
    pane leaves unset keeps whatever the EDS and the hook produced, so naming
    a field does not silently discard the limits the file declared.
    """
    overrides: dict[str, object] = {}
    if item.label:
        overrides["name"] = item.label
    for key in ("unit", "factor", "offset", "decimals", "low", "high"):
        value = getattr(item, key)
        if value not in (None, ""):
            overrides[key] = value
    if item.choices:
        overrides["choices"] = dict(item.choices)
    return with_overrides(base, overrides)


# --- where they are kept ------------------------------------------------------------------
def directory() -> Path:
    return workspaces.custom_panes_dir()


def path_for(name: str) -> Path:
    return directory() / f"{name}{SUFFIX}"


def names(protocol: str | None = None) -> list[str]:
    """Every pane in the workspace, by name; only those for ``protocol`` if given."""
    every = sorted(p.stem for p in directory().glob(f"*{SUFFIX}") if p.is_file())
    if protocol is None:
        return every
    return [
        name for name in every if (pane := load(name)) is not None and pane.protocol == protocol
    ]


def why_not(name: str) -> str:
    """Why this pane cannot be called that, or "" if it can."""
    name = name.strip()
    if not name:
        return "A pane needs a name."
    if len(name) > MAX_NAME:
        return f"Names are at most {MAX_NAME} characters."
    if not _ALLOWED_NAME.match(name) or name.endswith((" ", ".")):
        return (
            "A pane name is also a file name: letters, digits, spaces, dots, "
            "dashes and underscores, starting with a letter or a digit."
        )
    if path_for(name).exists():
        return f"There is already a pane called {name}."
    return ""


def load(name: str) -> CustomPane | None:
    """A pane by name, or None if there is no readable file for it."""
    try:
        data = json.loads(path_for(name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return pane_from_dict(data) if isinstance(data, dict) else None


def save(name: str, pane: CustomPane) -> Path:
    path = path_for(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(pane_to_dict(pane), indent=2), encoding="utf-8")
    return path


def delete(name: str) -> None:
    path_for(name).unlink(missing_ok=True)


# --- is it usable -----------------------------------------------------------------------------
def problems(pane: CustomPane) -> list[str]:
    """What is wrong with this pane, said in the terms it was written in.

    Reported rather than raised. A pane with one bad field is still a pane,
    and refusing to open it would leave somebody with no way to see which
    field was the problem.
    """
    found: list[str] = []
    if not pane.title.strip():
        found.append("The pane has no title.")
    if pane.protocol not in PROTOCOLS:
        found.append(f"{pane.protocol!r} is not one of {', '.join(PROTOCOLS)}.")
    seen: set[tuple[Hashable, int, int]] = set()
    for item in pane.fields:
        where = key_text(item.key)
        found.extend(f"{where}: {why}" for why in _address_problems(pane.protocol, item))
        if item.kind in CANOPEN_ONLY and pane.protocol != CANOPEN:
            found.append(f"{where}: {item.kind!r} is for CANopen panes.")
        if item.kind in XCP_ONLY and pane.protocol != XCP:
            found.append(f"{where}: {item.kind!r} is for XCP panes.")
        if item.kind not in KINDS:
            found.append(f"{where}: {item.kind!r} is not one of {', '.join(KINDS)}.")
        if item.kind == "bits":
            if not 1 <= item.width <= 64:
                found.append(f"{where}: a bit field is 1 to 64 bits wide.")
            if not 0 <= item.first <= 63:
                found.append(f"{where}: the first bit is 0 to 63.")
        if item.kind == "flags" and not item.bits:
            found.append(f"{where}: flags with no named bits would show nothing.")
        if item.kind == "enum" and not item.choices:
            found.append(f"{where}: a dropdown needs the choices naming, here or in the EDS.")
        # The same object twice is allowed -- a word of flags beside one of its
        # bits is a real layout -- but the same *field* twice is a copy/paste.
        same = (item.key, item.first, item.width)
        if same in seen and item.kind not in ("value",):
            found.append(f"{where}: appears twice with the same bits.")
        seen.add(same)
    return found


def _address_problems(protocol: str, item: Field) -> list[str]:
    """Whether a field is addressed the way its pane's protocol addresses things."""
    if protocol == XCP:
        return [] if item.name else ["a field on an XCP pane is named, as in the A2L."]
    if protocol == UDS:
        if item.name or item.did is None:
            return ["a field on a UDS pane has a DID."]
        return [] if 0 <= item.did <= MAX_DID else ["a DID is 0 to 0xFFFF."]
    if protocol != CANOPEN:
        return []  # said once for the pane already
    if item.name or item.did is not None:
        return ["a field on a CANopen pane has an index and a sub-index."]
    out = []
    if not 0 <= item.index <= MAX_INDEX:
        out.append("an index is 0 to 0xFFFF.")
    if not 0 <= item.sub <= MAX_SUB:
        out.append("a sub-index is 0 to 0xFF.")
    return out


def key_text(key: Hashable) -> str:
    """A key as it is written: 0x2001:00, a name, or a DID in hex."""
    if isinstance(key, tuple) and len(key) == 2:
        return f"0x{int(key[0]):04X}:{int(key[1]):02X}"
    if isinstance(key, int) and not isinstance(key, bool):
        return f"DID 0x{key:04X}"
    return str(key)
