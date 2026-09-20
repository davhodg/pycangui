# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Which identifiers a channel accepts, applied by the driver rather than here.

Everything else in pycangui that narrows what you see -- the trace's filter
box, its Filter menu, Pause -- hides rows and keeps the frames. This does
not. A filter here is given to python-can, which hands it to the adapter's
driver where the adapter can do it and drops the frames as it reads them
where it cannot. Either way the frames never reach pycangui: they are not
classified, not decoded, not counted, not recorded, not exported, and no
protocol stack sees them. On a bus busy enough to need this that is the
whole point, and it is also why it is dangerous.

The rules are an accept list, because that is what python-can takes. No
rules means accept everything, which is the normal state; one rule means
that identifier and nothing else, SDO replies to a node pycangui is talking
to included. Nothing here tries to protect the user from that -- a tool that
quietly kept some frames back from a filter it was asked for would be worse
-- so the job of the pane above is to make an applied filter impossible to
miss.

A rule is an identifier and a mask, the way CAN acceptance filtering has
always been written: a frame is accepted when ``id & mask == rule.can_id &
mask``. A mask of all ones is one identifier exactly, which is the default
and what most people want; clearing bits widens it to a range.
"""

from __future__ import annotations

from dataclasses import dataclass

#: All eleven or twenty-nine bits: this rule is one identifier exactly.
STANDARD_MASK = 0x7FF
EXTENDED_MASK = 0x1FFFFFFF


def full_mask(extended: bool) -> int:
    return EXTENDED_MASK if extended else STANDARD_MASK


@dataclass(frozen=True, slots=True)
class Rule:
    """One acceptance rule: an identifier, a mask, and which format it is."""

    can_id: int
    mask: int = STANDARD_MASK
    extended: bool = False

    def matches(self, can_id: int, extended: bool) -> bool:
        """Whether a frame gets through this rule.

        pycangui never filters with this -- the driver does -- but a user
        about to hide traffic is owed an answer to "would this let my node
        through", and the dialog asks it here.
        """
        if extended != self.extended:
            return False
        return can_id & self.mask == self.can_id & self.mask

    def as_can_filter(self) -> dict:
        return {"can_id": self.can_id, "can_mask": self.mask, "extended": self.extended}

    @property
    def one_id(self) -> bool:
        return self.mask == full_mask(self.extended)

    def text(self) -> str:
        width = 8 if self.extended else 3
        out = f"0x{self.can_id:0{width}X}"
        if not self.one_id:
            out += f"/0x{self.mask:0{width}X}"
        return out + (" ext" if self.extended else "")


def as_can_filters(rules: list[Rule]) -> list[dict] | None:
    """What python-can's ``set_filters`` wants, or None for accept everything."""
    return [r.as_can_filter() for r in rules] or None


def accepts(rules: list[Rule], can_id: int, extended: bool) -> bool:
    """Whether a frame would get through. No rules accepts everything."""
    return not rules or any(r.matches(can_id, extended) for r in rules)


def describe(rules: list[Rule]) -> str:
    """A short phrase for a status bar, in the singular where it is one."""
    if not rules:
        return ""
    if len(rules) == 1:
        return f"only {rules[0].text()}"
    return f"only {len(rules)} ids"


def to_saved(rules: list[Rule]) -> list[dict]:
    """As the workspace keeps them. Hex, because a CAN id is spoken in hex."""
    return [
        {"id": f"0x{r.can_id:X}", "mask": f"0x{r.mask:X}", "extended": r.extended} for r in rules
    ]


def from_saved(saved: object) -> list[Rule]:
    """Back from the workspace, skipping anything that does not parse.

    A settings file is editable by hand and older ones will not have this at
    all, so a rule that makes no sense is dropped rather than raised: the
    cost is a filter that lets more through than was meant, which is the
    safe direction for something that hides traffic.
    """
    out: list[Rule] = []
    if not isinstance(saved, list):
        return out
    for entry in saved:
        if not isinstance(entry, dict):
            continue
        extended = bool(entry.get("extended", False))
        try:
            can_id = int(str(entry.get("id", "")), 0)
            mask = int(str(entry.get("mask", full_mask(extended))), 0)
        except (TypeError, ValueError):
            continue
        limit = full_mask(extended)
        if not 0 <= can_id <= limit:
            continue
        out.append(Rule(can_id, min(max(mask, 0), limit), extended))
    return out
