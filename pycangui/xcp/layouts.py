# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Record layouts: where each part of a characteristic lies in memory.

A ``RECORD_LAYOUT`` lists the parts of a characteristic in the order of their
*position* numbers: the counts of axis points, the axis points themselves,
the values, and anything else a generator stores there (addresses, shifts,
an identification). Each part is laid down after the one before it, moved on
to its type's alignment, so where the values start depends on everything
before them::

    /begin RECORD_LAYOUT RL_CURVE
        NO_AXIS_PTS_X 1 UBYTE                    the number of points, 1 byte
        AXIS_PTS_X    2 SWORD INDEX_INCR DIRECT  the points, from offset 2
        FNC_VALUES    3 UWORD COLUMN_DIR DIRECT  then the values
    /end RECORD_LAYOUT

When the number of axis points is stored in the slave, the parts after it
move with it: a curve of five points takes less room than one of eight,
unless the layout says ``STATIC_RECORD_LAYOUT``, in which case room is kept
for the most there can be. Either way the count has to be read before the
values can be found, which is what :meth:`Layout.counted` says.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pycangui.xcp import DATATYPES

#: The axes a layout can name, as the suffix on its keywords.
AXES = ("X", "Y", "Z", "4", "5")

#: ALIGNMENT_* keywords, and the data types each one is for.
ALIGNMENTS = {
    "ALIGNMENT_BYTE": ("UBYTE", "SBYTE"),
    "ALIGNMENT_WORD": ("UWORD", "SWORD"),
    "ALIGNMENT_LONG": ("ULONG", "SLONG"),
    "ALIGNMENT_INT64": ("A_UINT64", "A_INT64"),
    "ALIGNMENT_FLOAT16_IEEE": ("FLOAT16_IEEE",),
    "ALIGNMENT_FLOAT32_IEEE": ("FLOAT32_IEEE",),
    "ALIGNMENT_FLOAT64_IEEE": ("FLOAT64_IEEE",),
}

#: RESERVED gives a size by name rather than a data type.
RESERVED_SIZES = {"BYTE": "UBYTE", "WORD": "UWORD", "LONG": "ULONG"}

#: How the values of a map are stored. The ALTERNATE ones interleave rows
#: and columns, and are not read.
VALUE_ORDERS = ("ROW_DIR", "COLUMN_DIR")

#: Parts that take room, by keyword without its axis, and how many fields follow.
_SIZED = {
    "NO_AXIS_PTS": 2,
    "NO_RESCALE": 2,
    "SRC_ADDR": 2,
    "RIP_ADDR": 2,
    "SHIFT_OP": 2,
    "OFFSET": 2,
    "DIST_OP": 2,
    "AXIS_PTS": 4,
    "AXIS_RESCALE": 5,
}


@dataclass(frozen=True)
class Part:
    """One thing a layout stores: what it is, for which axis, and its type."""

    kind: str  # FNC_VALUES, AXIS_PTS, NO_AXIS_PTS, AXIS_RESCALE, or another keyword
    axis: str  # X, Y ... or "" for one that belongs to no axis
    position: int
    datatype: str
    #: ROW_DIR or COLUMN_DIR for the values; INDEX_INCR or INDEX_DECR for points.
    order: str = ""
    #: DIRECT, or one of the pointer kinds, which are not read.
    addressing: str = "DIRECT"
    #: For AXIS_RESCALE: the most pairs it holds.
    most: int = 0


@dataclass
class Layout:
    name: str
    parts: list[Part] = field(default_factory=list)
    #: FIX_NO_AXIS_PTS_X and the like: a number of points that is not stored.
    fixed_counts: dict[str, int] = field(default_factory=dict)
    #: STATIC_RECORD_LAYOUT: room is kept for the most points, however many there are.
    static: bool = False
    #: The layout's own ALIGNMENT_* settings, by data type.
    alignment: dict[str, int] = field(default_factory=dict)
    #: Why it cannot be used, or "" when it can.
    problem: str = ""

    def part(self, kind: str, axis: str = "") -> Part | None:
        return next((p for p in self.parts if p.kind == kind and p.axis == axis), None)

    @property
    def values(self) -> Part | None:
        return self.part("FNC_VALUES")

    @property
    def counted(self) -> set[str]:
        """The axes whose number of points is stored in the slave."""
        return {p.axis for p in self.parts if p.kind == "NO_AXIS_PTS"}

    def place(
        self,
        start: int,
        counts: dict[str, int],
        most: dict[str, int],
        alignment: dict[str, int],
    ) -> dict[tuple[str, str], int]:
        """Where each part starts, as ``{(kind, axis): address}``.

        ``counts`` are the numbers of points each axis has, ``most`` the
        numbers it may have; a static layout makes room for ``most``.
        ``alignment`` is the module's, which the layout's own overrides.
        """
        sizes = most if self.static else counts
        where: dict[tuple[str, str], int] = {}
        at = start
        for part in sorted(self.parts, key=lambda p: p.position):
            size = DATATYPES[part.datatype][1]
            align = self.alignment.get(part.datatype) or alignment.get(part.datatype) or size
            at += -at % align
            where[(part.kind, part.axis)] = at
            at += size * self._how_many(part, sizes)
        return where

    @staticmethod
    def _how_many(part: Part, sizes: dict[str, int]) -> int:
        if part.kind == "AXIS_PTS":
            return sizes.get(part.axis, 0)
        if part.kind == "FNC_VALUES":
            values = 1
            for axis in AXES:
                if axis in sizes:
                    values *= sizes[axis]
            return values
        if part.kind == "AXIS_RESCALE":
            return 2 * part.most
        return 1


def parse(name: str, words: list[str]) -> Layout:
    """A layout from the words after its name, as the file has them."""
    layout = Layout(name)
    at = 0
    while at < len(words):
        word = str(words[at])
        keyword, axis = _split(word)
        if word == "STATIC_RECORD_LAYOUT":
            layout.static = True
            at += 1
            continue
        if word == "STATIC_ADDRESS_OFFSETS":
            at += 1
            continue
        if word in ALIGNMENTS and at + 1 < len(words):
            for datatype in ALIGNMENTS[word]:
                layout.alignment[datatype] = _int(words[at + 1]) or 1
            at += 2
            continue
        if keyword == "FIX_NO_AXIS_PTS" and at + 1 < len(words):
            layout.fixed_counts[axis] = _int(words[at + 1]) or 0
            at += 2
            continue
        if word == "FNC_VALUES" and at + 4 < len(words):
            position, datatype, order, addressing = (str(w) for w in words[at + 1 : at + 5])
            if order not in VALUE_ORDERS:
                layout.problem = f"its values are stored {order}, which is not read"
            layout.parts.append(
                Part("FNC_VALUES", "", _int(position) or 0, datatype, order, addressing)
            )
            at += 5
            continue
        if word in ("IDENTIFICATION", "RESERVED") and at + 2 < len(words):
            datatype = str(words[at + 2])
            datatype = RESERVED_SIZES.get(datatype, datatype)
            layout.parts.append(Part(word, "", _int(words[at + 1]) or 0, datatype))
            at += 3
            continue
        if keyword in _SIZED and axis:
            count = _SIZED[keyword]
            fields = [str(w) for w in words[at + 1 : at + 1 + count]]
            if len(fields) < count:
                layout.problem = f"{word} in it is cut short"
                break
            position, datatype = _int(fields[0]) or 0, fields[1]
            if keyword == "AXIS_PTS":
                part = Part(keyword, axis, position, datatype, fields[2], fields[3])
            elif keyword == "AXIS_RESCALE":
                part = Part(
                    keyword, axis, position, datatype, fields[3], fields[4], _int(fields[2]) or 0
                )
            else:
                part = Part(keyword, axis, position, datatype)
            layout.parts.append(part)
            at += 1 + count
            continue
        layout.problem = f"{word} in it is not something pycangui reads"
        break
    for part in layout.parts:
        if part.datatype not in DATATYPES:
            layout.problem = layout.problem or f"its type {part.datatype} is not one pycangui reads"
        if part.addressing != "DIRECT":
            layout.problem = layout.problem or (
                f"its {part.kind} are reached through a pointer ({part.addressing}), "
                "which is not followed"
            )
    return layout


def module_alignment(words: list[str]) -> dict[str, int]:
    """MOD_COMMON's ALIGNMENT_* settings, by data type."""
    found: dict[str, int] = {}
    for at, word in enumerate(words[:-1]):
        if str(word) in ALIGNMENTS:
            for datatype in ALIGNMENTS[str(word)]:
                found[datatype] = _int(words[at + 1]) or 1
    return found


def _split(word: str) -> tuple[str, str]:
    """``NO_AXIS_PTS_X`` into ``("NO_AXIS_PTS", "X")``; ``RIP_ADDR_W`` too."""
    keyword, _, axis = word.rpartition("_")
    if axis in AXES or (keyword == "RIP_ADDR" and axis == "W"):
        return keyword, axis
    return word, ""


def _int(token) -> int | None:
    try:
        return int(str(token), 0)
    except ValueError:
        return None
