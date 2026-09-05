"""What a panel is, and the file it is kept in.

A panel is a title and a list of fields; a field is one object dictionary
entry with a label and a way of being shown.  The seven ways are the whole
vocabulary, and they are enough to express every configuration screen in a
manufacturer's tool: a range-checked number, a hex code, a named choice, a
word of flags, a field packed into some bits of a larger object, an XY map,
and a value that is only read.

Kept as indented JSON in the workspace, beside the hooks that say what the
objects mean.  That is deliberate: a panel is knowledge about a product, the
same as the hooks and the EDS, and all three should travel together.  It is
also why the index is written as ``"0x2001"`` rather than as 8193 -- nobody
speaks about a CANopen object in decimal, and the file is meant to be opened
in a text editor.
"""

from __future__ import annotations

import json
import re
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
KINDS = ("value", "number", "hex", "enum", "flags", "bits", "map")

MAX_INDEX = 0xFFFF
MAX_SUB = 0xFF
#: A panel name is a file name, so it has to survive being one.
_ALLOWED_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]*$")
MAX_NAME = 64
SUFFIX = ".json"


@dataclass(frozen=True)
class Field:
    """One object on a panel, and how it is shown."""

    index: int
    sub: int = 0
    kind: str = "number"
    #: What to call it here.  Empty means whatever the source says it is
    #: called, which is usually right and occasionally unreadable.
    label: str = ""

    # --- meaning, where the panel knows better than the file ---------------------
    #: These sit on top of whatever the EDS and the display hook produced, for
    #: the case the hook cannot cover: one object that means something
    #: particular *on this panel*.  Left unset, the source's answer stands.
    unit: str = ""
    factor: float | None = None
    offset: float | None = None
    decimals: int | None = None
    low: float | None = None
    high: float | None = None
    #: Raw value -> what it means, for ``enum`` and ``bits``.
    choices: dict[int, str] = field(default_factory=dict)

    # --- flags and bits ----------------------------------------------------------
    #: ``flags``: bit number -> what that bit means.  Only the named ones are
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

    @property
    def where(self) -> tuple[int, int]:
        return (self.index, self.sub)

    @property
    def mask(self) -> int:
        """Which bits of the object this field occupies.  ``bits`` only."""
        return ((1 << self.width) - 1) << self.first if self.width else 0

    def extract(self, raw: int) -> int:
        """This field's value, out of the object it is packed into."""
        return (int(raw) & self.mask) >> self.first if self.width else int(raw)

    def insert(self, raw: int, value: int) -> int:
        """The object with this field replaced.

        Read-modify-write, and there is no other way: three bits of a 32 bit
        word cannot be written without the other twenty-nine, so a panel that
        did not read first would zero everything it was not showing.
        """
        if not self.width:
            return int(value)
        return (int(raw) & ~self.mask) | ((int(value) << self.first) & self.mask)


@dataclass
class Panel:
    """A named group of objects, laid out as a form."""

    title: str = ""
    description: str = ""
    fields: list[Field] = field(default_factory=list)
    #: The node this panel opens against, where it has a usual one.  A panel is
    #: bound to a *source* rather than to a node -- the same panel serves a
    #: live node, a DCF and an EDS's defaults -- so this is only a default.
    node: int | None = None

    def with_field(self, new: Field) -> Panel:
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
    in one field of one panel should cost that field, not the panel and not
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
    out: dict[str, Any] = {"index": f"0x{item.index:04X}", "kind": item.kind}
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


def panel_from_dict(data: dict) -> Panel:
    raw_fields = data.get("fields")
    return Panel(
        title=str(data.get("title", "")),
        description=str(data.get("description", "")),
        node=None if data.get("node") is None else _number(data.get("node")),
        fields=[field_from_dict(f) for f in raw_fields if isinstance(f, dict)]
        if isinstance(raw_fields, list)
        else [],
    )


def panel_to_dict(panel: Panel) -> dict:
    out: dict[str, Any] = {"title": panel.title}
    if panel.description:
        out["description"] = panel.description
    if panel.node is not None:
        out["node"] = panel.node
    out["fields"] = [field_to_dict(f) for f in panel.fields]
    return out


def display_for(item: Field, base: Display) -> Display:
    """What the source said about the object, with the panel's word on top.

    The panel wins where it speaks, because it is the more specific statement:
    the hook says what an object means on this product, and the panel says what
    it means *on this screen*, which is occasionally narrower.  Everything the
    panel leaves unset keeps whatever the EDS and the hook produced, so naming
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
    return workspaces.panels_dir()


def path_for(name: str) -> Path:
    return directory() / f"{name}{SUFFIX}"


def names() -> list[str]:
    """Every panel in the workspace, by name."""
    return sorted(p.stem for p in directory().glob(f"*{SUFFIX}") if p.is_file())


def why_not(name: str) -> str:
    """Why this panel cannot be called that, or "" if it can."""
    name = name.strip()
    if not name:
        return "A panel needs a name."
    if len(name) > MAX_NAME:
        return f"Names are at most {MAX_NAME} characters."
    if not _ALLOWED_NAME.match(name) or name.endswith((" ", ".")):
        return (
            "A panel name is also a file name: letters, digits, spaces, dots, "
            "dashes and underscores, starting with a letter or a digit."
        )
    if path_for(name).exists():
        return f"There is already a panel called {name}."
    return ""


def load(name: str) -> Panel | None:
    """A panel by name, or None if there is no readable file for it."""
    try:
        data = json.loads(path_for(name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return panel_from_dict(data) if isinstance(data, dict) else None


def save(name: str, panel: Panel) -> Path:
    path = path_for(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(panel_to_dict(panel), indent=2), encoding="utf-8")
    return path


def delete(name: str) -> None:
    path_for(name).unlink(missing_ok=True)


# --- is it usable -----------------------------------------------------------------------------
def problems(panel: Panel) -> list[str]:
    """What is wrong with this panel, said in the terms it was written in.

    Reported rather than raised.  A panel with one bad field is still a panel,
    and refusing to open it would leave somebody with no way to see which
    field was the problem.
    """
    found: list[str] = []
    if not panel.title.strip():
        found.append("The panel has no title.")
    seen: set[tuple[int, int, int, int]] = set()
    for item in panel.fields:
        where = f"0x{item.index:04X}:{item.sub:02X}"
        if not 0 <= item.index <= MAX_INDEX:
            found.append(f"{where}: an index is 0 to 0xFFFF.")
        if not 0 <= item.sub <= MAX_SUB:
            found.append(f"{where}: a sub-index is 0 to 0xFF.")
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
        key = (item.index, item.sub, item.first, item.width)
        if key in seen and item.kind not in ("value",):
            found.append(f"{where}: appears twice with the same bits.")
        seen.add(key)
    return found
