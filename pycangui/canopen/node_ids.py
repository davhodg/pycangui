# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Which node ids pycangui listens for heartbeats from, written as people write them.

``1-20``, or ``1-10, 32``: single ids and ranges, separated by commas, the
way a DBC writes a multiplexer's values. By default every id CANopen has,
1 to 127; fewer lets another protocol use the identifiers CANopen reserves
for heartbeats on the same bus without its frames being taken for nodes.
"""

from __future__ import annotations

from collections.abc import Iterable

#: Every node id CiA 301 allows.
ALL = frozenset(range(1, 128))


def parse(text: str) -> frozenset[int]:
    """The ids ``text`` names. ValueError, saying what is wrong, if it names none
    or something that is not a node id."""
    ids: set[int] = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        first, dash, last = part.partition("-")
        try:
            low = int(first)
            high = int(last) if dash else low
        except ValueError:
            raise ValueError(f"{part!r} is not a node id or a range of them") from None
        if low > high:
            raise ValueError(f"{part!r} runs backwards: write the lower id first")
        if low < min(ALL) or high > max(ALL):
            raise ValueError(f"{part!r}: node ids are 1 to 127")
        ids.update(range(low, high + 1))
    if not ids:
        raise ValueError("no node ids: 1-127 is every one")
    return frozenset(ids)


def text(ids: Iterable[int]) -> str:
    """The ids as ``parse`` reads them, the shortest way: ``1-10, 32``."""
    runs: list[list[int]] = []
    for node_id in sorted(set(ids)):
        if runs and node_id == runs[-1][1] + 1:
            runs[-1][1] = node_id
        else:
            runs.append([node_id, node_id])
    return ", ".join(str(low) if low == high else f"{low}-{high}" for low, high in runs)
