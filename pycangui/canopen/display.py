# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""How an object's value is shown, and how a typed one is read back.

Three layers, deliberately kept apart.  **Structure** -- type, access,
limits -- comes from the EDS and is what the ``canopen`` package parses.
**Meaning** -- what the number is called, what it is measured in, how to
scale it, what its values are named -- comes from the EDS *where the file
carries it*, and from ``hooks/canopen.py::object_display`` where it does not.
**The value** comes from the node, or a DCF, or the EDS default.

The middle layer is the one that needed inventing, because most EDS files do
not carry it.  CiA 306 defines no key for a unit or for scaling: a vendor who
wants one invents a key, and a vendor who does not simply leaves the tool
guessing.  A real 654 kB EDS for a motor controller was the case that settled
the design -- 1357 variables, of which **none** declared a unit, a factor, a
description or an enumeration, and 1330 declared limits.  So:

* limits are worth using, because they are nearly always there;
* units and scaling have to be able to come from a hook, because for a file
  like that there is nowhere else they could come from;
* and the raw value must stay reachable -- it is on each object's tooltip --
  since a scaled one is a claim somebody made in a hook rather than something
  the wire said.

Raw is what goes on the wire and what a DCF stores.  Physical is raw times
the factor plus the offset, and is what a person reads and types.  Every
conversion in pycangui goes through this module so the two cannot drift.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Display:
    """What is known about how to present one object.

    Everything is optional: with nothing filled in this is the behaviour
    pycangui had before, which is the raw number and nothing else.
    """

    name: str = ""
    #: A sentence about what the object is for, where the file has one.
    description: str = ""
    unit: str = ""
    factor: float = 1.0
    offset: float = 0.0
    #: Decimal places for the physical value.  None means "as many as it
    #: takes", which is right when nobody has said otherwise.
    decimals: int | None = None
    #: Raw value -> what that value means.  From the EDS where it names them,
    #: from the hook otherwise.
    choices: dict[int, str] = field(default_factory=dict)
    #: In raw units, as the EDS states them.
    low: float | None = None
    high: float | None = None

    @property
    def scaled(self) -> bool:
        return self.factor != 1.0 or self.offset != 0.0

    def physical(self, raw: float) -> float:
        return raw * self.factor + self.offset

    def raw(self, physical: float) -> float:
        return (physical - self.offset) / self.factor


def from_variable(var: Any | None) -> Display:
    """What the EDS itself said, for the files that say anything.

    ``canopen`` already parses Unit, Factor, Description, LowLimit and
    HighLimit where a file has them -- the gap was never the parser for those,
    it was pycangui not reading them back out.
    """
    if var is None:
        return Display()
    return Display(
        name=getattr(var, "name", "") or "",
        description=getattr(var, "description", "") or "",
        unit=getattr(var, "unit", "") or "",
        factor=float(getattr(var, "factor", 1.0) or 1.0),
        choices=dict(getattr(var, "value_descriptions", None) or {}),
        low=getattr(var, "min", None),
        high=getattr(var, "max", None),
    )


def with_overrides(base: Display, overrides: dict | None) -> Display:
    """Apply a hook's answer over what the file said.

    The hook wins: it is written by somebody holding the product
    documentation, and the file demonstrably may say nothing at all.  Keys it
    leaves out keep whatever the EDS had, so naming a unit does not silently
    discard the limits.
    """
    if not overrides:
        return base
    fields = {
        "name": str,
        "description": str,
        "unit": str,
        "factor": float,
        "offset": float,
        "decimals": lambda v: None if v is None else int(v),
        "choices": dict,
        "low": float,
        "high": float,
    }
    changes = {}
    for key, cast in fields.items():
        if key in overrides and overrides[key] is not None:
            try:
                changes[key] = cast(overrides[key])
            except (TypeError, ValueError):
                continue  # a hook typo must not take the object with it
    return Display(**{**vars(base), **changes})


def format_number(value: float, decimals: int | None) -> str:
    """A number as somebody would write it, not as a float prints."""
    if decimals is not None:
        return f"{value:.{decimals}f}"
    if isinstance(value, int) or float(value).is_integer():
        return str(int(value))
    return f"{value:.10g}"


def text(display: Display, raw: Any) -> str:
    """One value, said in the terms the object is understood in.

    Only the converted value: a cell showing "20 ms [20000]" spends half its
    width on a number nobody came for.  The raw one is on the tooltip, which
    is where it belongs -- worth having, because a scaled reading is a claim
    somebody made in a hook rather than something the wire said, but not worth
    a column.
    """
    if raw is None:
        return ""
    if isinstance(raw, bytes | bytearray):
        return raw.hex(" ").upper()
    if not isinstance(raw, int | float) or isinstance(raw, bool):
        return str(raw)

    if isinstance(raw, int) and display.choices.get(raw):
        return f"{raw} ({display.choices[raw]})"

    if display.scaled:
        shown = format_number(display.physical(raw), display.decimals)
        return f"{shown} {display.unit}".rstrip()

    shown = format_number(raw, display.decimals)
    if display.unit:
        return f"{shown} {display.unit}"
    # Hex alongside for anything that is likely a code rather than a quantity.
    return f"{shown} (0x{raw:X})" if isinstance(raw, int) and raw > 9 else shown


def as_number(text: str) -> float | None:
    """A typed value as a number, or None if it is not one.

    Accepts the bases a person actually types -- 0x10, 0b1010 -- because the
    tree shows hex beside anything that looks like a code, and what is shown
    is what gets typed back.
    """
    text = str(text).strip()
    try:
        if text.lower().startswith(("0x", "0b", "0o", "-0x", "-0b", "-0o")):
            return float(int(text, 0))
        return float(text)
    except ValueError:
        return None


def out_of_range(display: Display, raw: float) -> str:
    """Why this value would be refused, or "" if it would not be.

    Checked against the limits the EDS declares, which for a real file is the
    one piece of meaning it reliably carries.  A node is free to clamp
    silently, so catching it here is the difference between a parameter that
    did not take and a parameter nobody knew had not taken.
    """
    if display.low is not None and raw < display.low:
        return f"below the minimum of {format_number(display.low, None)}"
    if display.high is not None and raw > display.high:
        return f"above the maximum of {format_number(display.high, None)}"
    return ""


def limits_text(display: Display) -> str:
    """ "0 to 1000 (0.0 to 100.0 A)", or "" where the file did not say."""
    if display.low is None and display.high is None:
        return ""
    low = "?" if display.low is None else format_number(display.low, None)
    high = "?" if display.high is None else format_number(display.high, None)
    raw = f"{low} to {high}"
    if not display.scaled:
        return f"{raw} {display.unit}".strip()
    lo = (
        "?"
        if display.low is None
        else format_number(display.physical(display.low), display.decimals)
    )
    hi = (
        "?"
        if display.high is None
        else format_number(display.physical(display.high), display.decimals)
    )
    unit = f" {display.unit}" if display.unit else ""
    return f"{raw} ({lo} to {hi}{unit})"
