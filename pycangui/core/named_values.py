# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""A value that has a name, written one way everywhere: ``Run (1)``.

A DBC's ``VAL_`` table, an EDS's or a hook's choices for an object: each says
that 1 means *Run*. The name is what somebody reads and the number is what is
on the wire, so both are shown, name first, and the same way in every pane --
it was ``1 (Run)`` in one and ``Run`` in another.

Typed back in, any of ``1``, ``Run`` and ``Run (1)`` means the same value.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

#: ``Run (1)``, as ``named`` writes it: the number is what is sent.
_NAMED = re.compile(r"^.*\((-?\d+)\)\s*$")


def named(number, name) -> str:
    """A named value as it is shown: the name, and the number it stands for."""
    return f"{name} ({number})"


def _pairs(choices) -> list[tuple[int, str]]:
    """Choices as (number, name), from a table of them or a list of pairs."""
    if not choices:
        return []
    items: Iterable = choices.items() if isinstance(choices, Mapping) else choices
    return [(number, str(name)) for number, name in items]


def number_in(text: str) -> int | None:
    """The number in ``Run (1)``; None for text that is not written that way."""
    found = _NAMED.match(text.strip())
    return int(found.group(1)) if found is not None else None


def shown(value, choices) -> str | None:
    """A number as its name and number, where the table names it; else None."""
    if not choices or not isinstance(value, int | float) or isinstance(value, bool):
        return None
    if float(value) != int(value):
        return None
    for number, name in _pairs(choices):
        if number == int(value):
            return named(number, name)
    return None


def with_its_name(text: str, choices) -> str:
    """What was typed, as name and number together where the table has it.

    ``1``, ``Run`` and ``Run (1)`` all come out as ``Run (1)``. Anything the
    table does not name is left exactly as typed: a number outside it is
    sent as that number, and a name outside it is refused when it is used.
    """
    text = text.strip()
    pairs = _pairs(choices)
    if not pairs:
        return text
    if (number := number_in(text)) is not None:
        text = str(number)
    for number, name in pairs:
        if text == name:
            return named(number, name)
    try:
        typed = float(text)
    except ValueError:
        return text
    return shown(typed, pairs) or text


def plain(text: str, choices) -> str:
    """What was typed, as the bare number where it names or carries one.

    For whatever goes on to parse a number: ``Run (1)`` and ``Run`` become
    ``1``, and anything else is handed on unchanged.
    """
    text = text.strip()
    if (number := number_in(text)) is not None:
        return str(number)
    for number, name in _pairs(choices):
        if text == name:
            return str(number)
    return text
