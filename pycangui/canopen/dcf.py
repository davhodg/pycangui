# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""A DCF written as what it is: the EDS, with the values filled in.

CiA 306 says a device configuration file *is* an EDS with a ``ParameterValue``
added to each object and a ``[DeviceComissioning]`` section on the end. The
obvious way to produce one, and the way pycangui used to, is to hand the
parsed object dictionary back to the library's writer -- which loses
everything the parser did not keep. For a real motor controller EDS that is
eleven thousand comment lines, carrying the units, the scaling and the
description of nearly every object: a round trip through the tool quietly
turned a documented file into an undocumented one.

So the values are written into the file rather than the file being written
from the values. The original text goes through unchanged, comments and
ordering and unknown sections included, and the only edits are the
``ParameterValue`` lines and the commissioning section. A DCF then differs
from the EDS it came from by exactly what it is supposed to differ by, which
is also the thing that makes the two readable side by side in a diff.
"""

from __future__ import annotations

import re

#: ``[1018]`` or ``[1018sub2]``.
_OBJECT_SECTION = re.compile(r"^\[([0-9A-Fa-f]{4})(?:sub([0-9A-Fa-f]+))?\]$")
_SECTION = re.compile(r"^\[(.+)\]$")
_PARAMETER_VALUE = re.compile(r"^ParameterValue\s*=", re.IGNORECASE)
_NODE_ID = re.compile(r"^NodeID\s*=", re.IGNORECASE)

COMMISSIONING = "DeviceComissioning"  # spelled as CiA 306 spells it


def _line_ending(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def write_dcf(eds_text: str, values: dict[tuple[int, int], str], node_id: int | None = None) -> str:
    """The EDS text with values added, as a DCF.

    `values` is keyed by (index, sub-index) and holds the raw value already
    formatted as it should appear -- raw, because that is what a DCF stores
    and what the next tool to read it will expect.

    An object with no value in `values` is left exactly as it was: a parameter
    the node would not give up is better absent than guessed at.
    """
    ending = _line_ending(eds_text)
    out: list[str] = []
    where: tuple[int, int] | None = None
    last_key: int | None = None
    seen_commissioning = False

    def flush() -> None:
        """Add the value for the section that is ending, if it had none."""
        nonlocal where, last_key
        if where is not None and where in values:
            at = last_key + 1 if last_key is not None else len(out)
            out.insert(at, f"ParameterValue={values[where]}")
        where, last_key = None, None

    for line in eds_text.splitlines():
        stripped = line.strip()
        section = _SECTION.match(stripped)
        if section is not None:
            flush()
            match = _OBJECT_SECTION.match(stripped)
            if match is not None:
                sub = int(match.group(2), 16) if match.group(2) else 0
                where = (int(match.group(1), 16), sub)
            elif section.group(1).lower() == COMMISSIONING.lower():
                seen_commissioning = True
            out.append(line)
            continue

        if where is not None and _PARAMETER_VALUE.match(stripped):
            # Re-saving a DCF: replace what is there rather than writing a
            # second one, which would leave the file with two answers.
            if where in values:
                out.append(f"ParameterValue={values[where]}")
                where, last_key = None, len(out) - 1  # already written
            continue
        if seen_commissioning and node_id is not None and _NODE_ID.match(stripped):
            out.append(f"NodeID={node_id}")
            continue

        if where is not None and stripped and not stripped.startswith(";") and "=" in stripped:
            last_key = len(out)
        out.append(line)
    flush()

    if node_id is not None and not seen_commissioning:
        out += ["", f"[{COMMISSIONING}]", f"NodeID={node_id}"]
    return ending.join(out) + ending


def values_from(pairs: dict[tuple[int, int], object]) -> dict[tuple[int, int], str]:
    """Raw values as a DCF writes them.

    Plain decimal for numbers. The library's own writer formats a negative
    number as "0x-4D2", which its own reader then refuses, so a DCF written
    that way does not survive being read back.
    """
    out: dict[tuple[int, int], str] = {}
    for key, value in pairs.items():
        if value is None:
            continue
        if isinstance(value, bytes | bytearray):
            out[key] = value.hex().upper()
        elif isinstance(value, bool):
            out[key] = str(int(value))
        else:
            out[key] = str(value)
    return out
