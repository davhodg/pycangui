# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Two configurations, side by side, and what is different about them.

"What is different about the unit that fails" is the question a commissioning
tool exists to answer and the one pycangui could not answer at all: it could
save a DCF and it could apply one, and there was nothing in between.

A comparison is between two **readings**, and a reading is deliberately a
small thing -- a label, the values it holds, and what each object is called.
Where it came from is not part of it. That is what lets a file be compared
against a file, a file against a live node, and a node against a node without
three implementations: reading a DCF and reading a controller both end up
here, and everything below this line is the same afterwards.

Three things this refuses to do, each because the alternative is a confident
answer that is wrong:

**It does not treat "absent" as "different".**  A DCF carries a value only for
the objects that had one, and a node answers only what it implements, so an
object on one side and not the other is extremely common and almost never a
configuration difference. It gets a state of its own and is not counted among
the differences.

**It does not decide what to read on its own.**  Something has to say which
objects are being compared -- a file lists them, a loaded EDS lists them --
and when nothing does, that is a refusal rather than a guess at a range of
indices.

**It does not compare across node-IDs quietly.**  Half the communication
objects in a CANopen device are COB-IDs derived from the node-ID, so a DCF
from node 5 against one from node 6 differs in every one of them, correctly
and uselessly. The node-ID of each side is carried on the reading so that
whatever shows the result can say so.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

#: What a row of a comparison can be. ``SAME`` is kept rather than dropped
#: because "these two agree about 412 objects" is worth being able to show,
#: and because a comparison that only ever displays differences cannot be
#: checked against anything.
SAME = "same"
DIFFERENT = "different"
LEFT_ONLY = "left only"
RIGHT_ONLY = "right only"

COMMISSIONING = "DeviceComissioning"  # spelled as CiA 306 spells it

#: Below this are the communication objects a device does not configure. The
#: same floor ``save_dcf`` uses, so a DCF written by pycangui and a node read
#: by it cover the same ground.
FIRST_INDEX = 0x1000


#: The access types a device will take a write to, as CiA 306 spells them.
#: The two left out are ``ro`` and ``const``: measurements and nameplate,
#: which differ between two readings because the machine was doing something
#: at the time, not because anybody configured it differently.
WRITABLE = ("rw", "wo", "rww", "rwr")


def writable(access: str) -> bool:
    """Whether an object is one somebody could have changed.

    An unknown access type -- neither side had a file saying -- counts as
    writable, deliberately. A filter that hides what it cannot classify
    hides the thing being looked for.
    """
    return not access or access.lower() in WRITABLE


@dataclass(frozen=True)
class Reading:
    """One side of a comparison: what was found, and what it was called.

    ``values`` is keyed by ``(index, sub)`` and holds raw values -- what a DCF
    stores and what an SDO read gives back -- because comparing what two tools
    made of a number is comparing the tools.
    """

    label: str
    values: dict[tuple[int, int], object] = field(default_factory=dict)
    names: dict[tuple[int, int], str] = field(default_factory=dict)
    #: ``ro``, ``rw``, ``const`` and the rest, where a file said. A node read
    #: over SDO says nothing about access, so this is empty for one without
    #: an EDS loaded -- and empty is "not known", not "read-only".
    access: dict[tuple[int, int], str] = field(default_factory=dict)
    #: Where the values came from, when that is a node. Two readings taken at
    #: different node-IDs are comparable in principle and misleading in
    #: practice, and nothing here can tell which without being told.
    node_id: int | None = None

    def name_of(self, where: tuple[int, int]) -> str:
        return self.names.get(where, "")

    def access_of(self, where: tuple[int, int]) -> str:
        return self.access.get(where, "")


@dataclass(frozen=True)
class Row:
    """One object, as the two sides have it."""

    index: int
    sub: int
    name: str
    left: object
    right: object
    state: str
    #: What a file says this object's access type is, or "" where neither
    #: side had one to say.
    access: str = ""

    @property
    def where(self) -> tuple[int, int]:
        return (self.index, self.sub)

    @property
    def differs(self) -> bool:
        """Whether this is a disagreement, as opposed to a gap in one side."""
        return self.state == DIFFERENT

    @property
    def writable(self) -> bool:
        """Whether this is an object somebody could have configured."""
        return writable(self.access)


def same_value(left: object, right: object) -> bool:
    """Whether two raw values are the same number, however they were written.

    A DCF holds ``0x1F80`` as text a parser turned into an integer and a node
    hands back an integer; a value that survived that round trip must not be
    reported as a change. Booleans are compared as the integers CANopen
    stores, since a node returning 1 and a file saying True are the same bit.
    """
    if isinstance(left, bool) or isinstance(right, bool):
        left = int(left) if isinstance(left, bool) else left
        right = int(right) if isinstance(right, bool) else right
    if isinstance(left, int | float) and isinstance(right, int | float):
        return float(left) == float(right)
    if isinstance(left, bytes | bytearray) or isinstance(right, bytes | bytearray):
        return bytes(left or b"") == bytes(right or b"")
    return left == right


def compare(left: Reading, right: Reading, first_index: int = FIRST_INDEX) -> list[Row]:
    """Every object either side holds, in index order, with what it is.

    Everything is returned, differences included and excluded alike; deciding
    what to show is the caller's, and a function that returned only the
    differences could not be asked how many objects agreed.
    """
    rows = []
    for where in sorted(set(left.values) | set(right.values)):
        if where[0] < first_index:
            continue
        in_left, in_right = where in left.values, where in right.values
        if in_left and in_right:
            state = SAME if same_value(left.values[where], right.values[where]) else DIFFERENT
        else:
            state = LEFT_ONLY if in_left else RIGHT_ONLY
        rows.append(
            Row(
                index=where[0],
                sub=where[1],
                name=left.name_of(where) or right.name_of(where),
                access=left.access_of(where) or right.access_of(where),
                left=left.values.get(where),
                right=right.values.get(where),
                state=state,
            )
        )
    return rows


def summarise(rows: list[Row]) -> str:
    """One line saying how the two compare, for putting above the table."""
    counted = {
        state: sum(1 for row in rows if row.state == state)
        for state in (DIFFERENT, LEFT_ONLY, RIGHT_ONLY, SAME)
    }
    if not rows:
        return "Nothing to compare."
    if not any(counted[state] for state in (DIFFERENT, LEFT_ONLY, RIGHT_ONLY)):
        return f"Identical: {counted[SAME]} objects, all agreeing."
    parts = [f"{counted[DIFFERENT]} different"]
    if counted[LEFT_ONLY]:
        parts.append(f"{counted[LEFT_ONLY]} only on the left")
    if counted[RIGHT_ONLY]:
        parts.append(f"{counted[RIGHT_ONLY]} only on the right")
    parts.append(f"{counted[SAME]} the same")
    return ", ".join(parts) + "."


def node_id_warning(left: Reading, right: Reading) -> str:
    """Said when the two sides were taken at different node-IDs, or nothing.

    Not a refusal. Comparing a controller against the file from the one
    beside it is a thing people do on purpose, and the node-ID difference is
    then noise they need to be told about rather than protected from.
    """
    if left.node_id is None or right.node_id is None or left.node_id == right.node_id:
        return ""
    return (
        f"Left is node {left.node_id} and right is node {right.node_id}. Every COB-ID "
        "derived from the node-ID will differ, correctly -- that is what a different "
        "node-ID means, not a difference in configuration."
    )


def as_text(rows: list[Row], left: str, right: str, differences_only: bool = True) -> str:
    """The comparison as text, for pasting into a note or a build record."""
    wanted = [row for row in rows if row.state != SAME] if differences_only else rows
    lines = [f"Index    Sub  {'Object':<32} {left:<20} {right:<20} "]
    for row in wanted:
        lines.append(
            f"0x{row.index:04X}  {row.sub:3d}  {row.name[:32]:<32} "
            f"{shown(row.left):<20} {shown(row.right):<20} {row.state}"
        )
    return "\n".join(lines)


def shown(value: object) -> str:
    """A raw value as it should read on screen: absent is absent, not None."""
    if value is None:
        return "--"
    if isinstance(value, bool):
        return str(int(value))
    if isinstance(value, int):
        return f"{value} (0x{value:X})" if value > 9 or value < -9 else str(value)
    if isinstance(value, bytes | bytearray):
        return value.hex(" ").upper()
    return str(value)


# --- where a reading comes from ----------------------------------------------------------
def read_file(path: str | Path, node_id: int = 0) -> Reading:
    """A DCF or EDS as one side of a comparison.

    An EDS is allowed as well as a DCF, and is worth having: comparing a
    controller against the EDS it was built from is the question "what has
    anybody changed on this device", which is a different question from "does
    it match the file I was given" and just as often the one being asked.

    ``node_id`` resolves the ``$NODEID`` expressions a CANopen file uses for
    its COB-IDs. A DCF carries its own in the commissioning section and that
    one wins, because the file knows better than the caller which node it was
    taken from.
    """
    import canopen

    from pycangui.canopen.manager import _all_variables

    path = Path(path)
    stored = _node_id_in(path)
    od = canopen.import_od(str(path), stored if stored is not None else node_id)
    values: dict[tuple[int, int], object] = {}
    names: dict[tuple[int, int], str] = {}
    access: dict[tuple[int, int], str] = {}
    for var in _all_variables(od):
        where = (var.index, var.subindex)
        names[where] = var.name or ""
        access[where] = getattr(var, "access_type", "") or ""
        if var.value is not None:
            values[where] = var.value
    return Reading(label=path.name, values=values, names=names, access=access, node_id=stored)


def _node_id_in(path: Path) -> int | None:
    """The node-ID a DCF was taken from, or None for a plain EDS.

    Read out of the text rather than off the parsed dictionary, because the
    parser wants the node-ID in order to do the parsing -- which is the wrong
    way round when the file is the only thing that knows it.
    """
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return None
    inside = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            inside = stripped.strip("[]").lower() == COMMISSIONING.lower()
        elif inside and stripped.lower().startswith("nodeid"):
            _name, _, value = stripped.partition("=")
            try:
                return int(value.strip(), 0)
            except ValueError:
                return None
    return None


def objects_to_read(*readings: Reading) -> list[tuple[int, int]]:
    """Which objects a node should be asked for, taken from the file beside it.

    Reading only what the other side names is faster than reading everything,
    needs no EDS on the node, and asks the question actually being asked --
    does this device agree with this file. Reading the whole dictionary and
    then discarding most of it would be minutes of SDO traffic to answer the
    same thing.
    """
    wanted: list[tuple[int, int]] = []
    for reading in readings:
        for where in sorted(reading.values):
            if where[0] >= FIRST_INDEX and where not in wanted:
                wanted.append(where)
    return wanted
