# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""An A2L reader: what the XCP pane needs out of ASAM MCD-2 MC, and no more.

It lists a file's MEASUREMENTs and CHARACTERISTICs with where each one is, what
type it is, and how its raw value becomes a number somebody recognises. Single
values are read in full, and so is a row of them: an array measurement or a
block of values, element by element. Curves and maps are read with their axes,
wherever the record layout puts them (see :mod:`pycangui.xcp.layouts`); text
and three-dimensional maps are listed, so that the file is seen whole, and
marked as not readable here.

A2L is nested blocks of positional fields and optional keywords::

    /begin MEASUREMENT EngineSpeed "Engine speed"
        UWORD rpm_conv 0 0 0 8000
        ECU_ADDRESS 0x1000
    /end MEASUREMENT

    /begin CHARACTERISTIC SpeedLimit "Maximum engine speed"
        VALUE 0x2000 RL_UWORD 0 rpm_conv 0 8000
    /end CHARACTERISTIC

    /begin RECORD_LAYOUT RL_UWORD
        FNC_VALUES 1 UWORD COLUMN_DIR DIRECT
    /end RECORD_LAYOUT

Two things about it are easy to get wrong. A characteristic does not say what
type it is: it names a record layout, and the type is there. And the fields
come in a fixed order which differs between the two, so each block is read by
position -- looking for a word that happens to be a data type finds the
measurement's and never the characteristic's.

A file is read in three steps: into tokens (comments dropped, quoted strings
kept whole), into a tree of blocks, and then the blocks this reader knows are
picked out of the tree. Anything else in the file is skipped without complaint;
an A2L carries a great deal that a calibration pane has no use for.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from itertools import pairwise, product
from pathlib import Path

from pycangui.xcp import DATATYPES, layouts
from pycangui.xcp.formula import FormulaError, compile_formula

#: Comments, quoted strings, and everything else up to the next space. A string
#: may hold a doubled or a backslashed quote.
_TOKEN = re.compile(
    r"""/\*.*?\*/ | //[^\n]*            # a comment, dropped
      | "(?:[^"\\]|\\.|"")*"           # a quoted string, kept whole
      | [^\s"]+                         # a word
    """,
    re.S | re.X,
)

#: A characteristic that is one value, and one that is a row of them.
VALUE = "VALUE"
VAL_BLK = "VAL_BLK"
#: Values over one axis and over two, and an AXIS_PTS: axis points kept apart,
#: which several curves and maps share. Each is read with what its axes hold.
CURVE = "CURVE"
MAP = "MAP"
AXIS_PTS = "AXIS_PTS"
TABLES = (CURVE, MAP, AXIS_PTS)
#: Text: a row of bytes read and written as a string.
ASCII = "ASCII"
#: What a GROUP and a FUNCTION list, by the block each list is in.
GROUP_LISTS = ("REF_CHARACTERISTIC", "REF_MEASUREMENT")
FUNCTION_LISTS = (
    "DEF_CHARACTERISTIC",
    "REF_CHARACTERISTIC",
    "IN_MEASUREMENT",
    "OUT_MEASUREMENT",
    "LOC_MEASUREMENT",
)
#: ``%6.1``, ``%.2f``: how many places after the point a value is shown with.
_FORMAT = re.compile(r"%\s*\d*\.(\d+)")
#: The axes of a curve, then of a map, by the letter the file gives each.
AXIS_LETTERS = ("X", "Y")
#: Axes whose points are somewhere this reader goes: in the record, in an
#: AXIS_PTS of their own, or worked out from numbers in the file.
READ_AXES = ("STD_AXIS", "COM_AXIS", "FIX_AXIS")

#: What ``BYTE_ORDER`` can say, as whether the first byte is the most
#: significant. MSB_FIRST and BIG_ENDIAN are Motorola order; the standard
#: spent some years with these the wrong way round in its own text, and this
#: is the meaning it settled on.
BYTE_ORDERS = {
    "MSB_FIRST": True,
    "BIG_ENDIAN": True,
    "MSB_LAST": False,
    "LITTLE_ENDIAN": False,
}

#: The most values of one array read in one go. Each is a read of its own, so
#: a table of thousands is minutes on a CAN bus and not something to start by
#: double-clicking a row. A bigger array is still opened, and its values read
#: one at a time.
MOST_AT_ONCE = 1024

#: The most values an array may have and still be opened into a row for each.
MOST_ELEMENTS = 4096

#: A record layout with one of these in it holds more than the values: axis
#: points, counts, addresses. Where the values then start is worked out from
#: the rest, which this reader does not do.
_MORE_THAN_VALUES = (
    "AXIS_",
    "NO_AXIS",
    "FIX_NO_AXIS",
    "NO_RESCALE",
    "OFFSET_",
    "SHIFT_OP",
    "DIST_OP",
    "SRC_ADDR",
    "RIP_ADDR",
    "RESERVED",
    "IDENTIFICATION",
)

#: The indices on the end of ``name[3]`` or ``name[1][3]``: one value of an array.
_INDICES = re.compile(r"(?:\[\d+\])+$")

#: How many files an ``/include`` may go down, one inside another. A file that
#: includes itself is a mistake, not a reason to run out of stack.
INCLUDE_DEPTH = 8


class _Text(str):
    """A token that was in quotes: text, and never a keyword."""


@dataclass
class _Block:
    """``/begin KIND ... /end KIND``: its words, strings and blocks, in order."""

    kind: str
    items: list = field(default_factory=list)

    def fields(self) -> list[str]:
        """The words and strings, without the blocks inside."""
        return [item for item in self.items if not isinstance(item, _Block)]

    def blocks(self, kind: str) -> list[_Block]:
        return [item for item in self.items if isinstance(item, _Block) and item.kind == kind]

    def after(self, keyword: str, count: int = 1) -> list[str] | None:
        """The ``count`` fields following an optional keyword, or None without it.

        A keyword is a bare word, so a description that happens to say
        BIT_MASK is not one.
        """
        fields = self.fields()
        for at, item in enumerate(fields):
            if item == keyword and not isinstance(item, _Text):
                found = fields[at + 1 : at + 1 + count]
                return found if len(found) == count else None
        return None

    def has(self, keyword: str) -> bool:
        return any(i == keyword and not isinstance(i, _Text) for i in self.fields())


# --- what a raw value means ----------------------------------------------------------------
@dataclass
class Conversion:
    """A COMPU_METHOD: how a raw value becomes the number, or the name, shown.

    Linear ones are worked both ways, and so is one over a straight line,
    which is how a period is stored for something shown as a frequency, and
    a rational function with a square in it, whose quadratic is solved. A
    table of numbers (COMPU_TAB) is looked up, with or without interpolating
    between its rows, and a formula (FORM) is worked out; it is written only
    where the file gives the formula back the other way, FORMULA_INV. A
    table of names (``choices``) leaves the number alone and says what it is
    called. Anything else is ``exact = False``: the raw value is shown as it
    is, and said to be raw.
    """

    name: str
    factor: float = 1.0
    offset: float = 0.0
    unit: str = ""
    #: Raw value -> its name, from a COMPU_VTAB.
    choices: dict[int, str] = field(default_factory=dict)
    #: Whether pycangui works this conversion out, or shows the raw value.
    exact: bool = True
    #: What kind it is in the file: RAT_FUNC, LINEAR, TAB_VERB, IDENTICAL, FORM...
    kind: str = ""
    #: The places after the point its FORMAT shows, or None to show as many as there are.
    decimals: int | None = None
    #: ``(b, c, e, f)`` of ``raw = (b*phys + c) / (e*phys + f)``, for a rational
    #: function that is not a straight line; None for one that is.
    fraction: tuple[float, float, float, float] | None = None
    #: All six coefficients of a RAT_FUNC with a square in it.
    quadratic: tuple[float, float, float, float, float, float] | None = None
    #: COMPU_TAB: ``(raw, physical)`` rows, by raw value; whether to interpolate
    #: between them; and what a raw value not in a table that does not is.
    table: tuple[tuple[float, float], ...] = ()
    interpolate: bool = False
    default: float | None = None
    #: FORM: the formula, and the one the other way where the file gives it.
    formula: Callable[[float], float] | None = field(default=None, repr=False, compare=False)
    inverse: Callable[[float], float] | None = field(default=None, repr=False, compare=False)

    @property
    def invertible(self) -> bool:
        """Whether a physical value can be turned back into the raw one to write."""
        return self.formula is None or self.inverse is not None

    def to_phys(
        self, raw: float, within: tuple[float | None, float | None] = (None, None)
    ) -> float:
        """The physical value. ``within`` is the parameter's limits, which
        choose between the two answers a quadratic can have."""
        if self.formula is not None:
            return self.formula(raw)
        if self.table:
            return _look_up(self.table, raw, self.interpolate, self.default)
        if self.quadratic is not None:
            return _solve(self.quadratic, raw, within)
        if self.fraction is not None:
            b, c, e, f = self.fraction
            below = e * raw - b
            # The one raw value with no physical one: a period of zero.
            return (c - f * raw) / below if below else math.inf
        return raw * self.factor + self.offset

    def to_raw(self, phys: float) -> float:
        if self.formula is not None:
            if self.inverse is None:
                raise ValueError(f"{self.name} has no FORMULA_INV, so it cannot be written")
            return self.inverse(phys)
        if self.table:
            return _look_back(self.table, phys, self.interpolate)
        if self.quadratic is not None:
            a, b, c, d, e, f = self.quadratic
            below = d * phys * phys + e * phys + f
            return (a * phys * phys + b * phys + c) / below if below else 0.0
        if self.fraction is not None:
            b, c, e, f = self.fraction
            below = e * phys + f
            return (b * phys + c) / below if below else 0.0
        return (phys - self.offset) / self.factor if self.factor else 0.0


def _look_up(
    table: tuple[tuple[float, float], ...], raw: float, interpolate: bool, default: float | None
) -> float:
    """A COMPU_TAB's physical value for a raw one.

    Interpolated, a value between two rows is in proportion between them, and
    one beyond the ends is the end's. Not interpolated, it is the row with
    that raw value, the file's default where there is none, and the nearest
    row where the file has no default either.
    """
    if not interpolate:
        for row_raw, row_phys in table:
            if math.isclose(row_raw, raw):
                return row_phys
        if default is not None:
            return default
        return min(table, key=lambda row: abs(row[0] - raw))[1]
    if raw <= table[0][0]:
        return table[0][1]
    for (raw_a, phys_a), (raw_b, phys_b) in pairwise(table):
        if raw <= raw_b:
            return phys_a + (phys_b - phys_a) * (raw - raw_a) / (raw_b - raw_a)
    return table[-1][1]


def _look_back(table: tuple[tuple[float, float], ...], phys: float, interpolate: bool) -> float:
    """The raw value to write for a physical one: the other way through the table."""
    if interpolate:
        for (raw_a, phys_a), (raw_b, phys_b) in pairwise(table):
            if min(phys_a, phys_b) <= phys <= max(phys_a, phys_b) and phys_a != phys_b:
                return raw_a + (raw_b - raw_a) * (phys - phys_a) / (phys_b - phys_a)
    return min(table, key=lambda row: abs(row[1] - phys))[0]


def _solve(
    coefficients: tuple[float, ...], raw: float, within: tuple[float | None, float | None]
) -> float:
    """The physical value of a RAT_FUNC with a square in it.

    ``raw = (a*p*p + b*p + c) / (d*p*p + e*p + f)`` is a quadratic in p for a
    given raw value. Of its two answers, the one within the limits is taken,
    and where both are, or neither, the larger: the file's own limits are
    all there is to choose by.
    """
    a, b, c, d, e, f = coefficients
    big, middle, small = a - raw * d, b - raw * e, c - raw * f
    if math.isclose(big, 0.0, abs_tol=1e-15):
        return -small / middle if middle else math.nan
    under = middle * middle - 4 * big * small
    if under < 0:
        return math.nan
    root = math.sqrt(under)
    answers = sorted(((-middle - root) / (2 * big), (-middle + root) / (2 * big)))
    lower, upper = within
    inside = [p for p in answers if (lower is None or p >= lower) and (upper is None or p <= upper)]
    return (inside or answers)[-1]


def _unit(conversion: Conversion | None, own: str) -> str:
    """A value's unit: raw where it is shown raw, else its own PHYS_UNIT, else its conversion's."""
    if conversion is not None and not conversion.exact:
        return "raw"
    return own or (conversion.unit if conversion else "")


def _places(conversion: Conversion | None, own: int | None) -> int | None:
    if conversion is not None and not conversion.exact:
        return None  # a raw number is shown as it is
    if own is not None:
        return own
    return conversion.decimals if conversion is not None else None


@dataclass
class Group:
    """A GROUP or a FUNCTION: names of parameters, and of the groups inside it."""

    name: str
    description: str
    kind: str  # GROUP or FUNCTION
    root: bool = False
    members: list[str] = field(default_factory=list)
    inside: list[str] = field(default_factory=list)


@dataclass
class Axis:
    """One axis of a curve or a map, from its AXIS_DESCR."""

    letter: str  # X, then Y
    attribute: str  # STD_AXIS, COM_AXIS, FIX_AXIS ...
    input: str  # the measurement the axis is of
    conversion: Conversion | None
    most: int  # MaxAxisPoints
    lower: float | None = None
    upper: float | None = None
    #: How many points it has where that is known without asking the slave.
    count: int | None = None
    #: The parameter its points are read from: ``Map:x`` for points kept in
    #: the record, an AXIS_PTS's name for a shared axis; "" for a fixed one.
    points: str = ""
    #: A FIX_AXIS's raw points, worked out from the file.
    fixed: tuple[float, ...] = ()
    read_only: bool = False
    #: Its own FORMAT and PHYS_UNIT, where it gives them.
    own_decimals: int | None = None
    phys_unit: str = ""

    @property
    def unit(self) -> str:
        return _unit(self.conversion, self.phys_unit)

    @property
    def decimals(self) -> int | None:
        return _places(self.conversion, self.own_decimals)


@dataclass
class Parameter:
    name: str
    kind: str  # "MEASUREMENT" or "CHARACTERISTIC"
    datatype: str
    address: int
    description: str = ""
    conversion: Conversion | None = None
    lower: float | None = None
    upper: float | None = None
    #: For a characteristic: VALUE, CURVE, MAP, CUBOID, VAL_BLK, ASCII...
    shape: str = VALUE
    #: The bits of the stored value that are this parameter, or None for all.
    bit_mask: int | None = None
    #: Its own byte order where it states one: True for most significant first.
    big_endian: bool | None = None
    #: Which of the slave's address spaces it is in: ECU_ADDRESS_EXTENSION.
    extension: int = 0
    #: Marked READ_ONLY in the file: a characteristic not meant to be written.
    read_only: bool = False
    #: Why pycangui cannot read it, or "" when it can.
    unreadable: str = ""
    #: How many values it is: more than one for an array or a block of values.
    count: int = 1
    #: An array's size along each of its dimensions, in the order its values
    #: are stored: the last one is the one that changes fastest, as in C. So
    #: ``(2, 24)`` is two rows of twenty-four, and ``name[1][0]`` is the
    #: twenty-fifth value.
    sizes: tuple[int, ...] = ()
    #: The same sizes as the file wrote them, and which way round it stores
    #: them, for saying how ``sizes`` was arrived at.
    written: tuple[int, ...] = ()
    column_dir: bool = False
    #: For one value of an array, ``name[3]``: the array's name, and which.
    element_of: str = ""
    index: int = 0
    #: A curve's or a map's axes, in the order the file gives them.
    axes: tuple[Axis, ...] = ()
    #: How far apart, in values, the next one along each index is. Empty
    #: for an array, which is stored with the last index changing fastest;
    #: a map's values are named ``[x][y]`` whichever way they are stored.
    strides: tuple[int, ...] = ()
    #: The record layout it names, and where its record starts: ``address``
    #: is where its values do, which the layout decides.
    layout: str = ""
    start: int = 0
    #: Axes whose number of points is kept in the slave: until they are read,
    #: where the values are is not known.
    counted: tuple[str, ...] = ()
    #: Its own FORMAT and PHYS_UNIT, which win over its conversion's.
    own_decimals: int | None = None
    phys_unit: str = ""
    #: For text (ASCII): how many bytes it has.
    length: int = 0

    @property
    def readable(self) -> bool:
        return not self.unreadable

    @property
    def is_array(self) -> bool:
        """A row of values, read and written through its elements."""
        return self.count > 1 and self.readable

    @property
    def is_table(self) -> bool:
        """A curve, a map or an AXIS_PTS, as a whole: read with its axes."""
        return self.shape in TABLES and not self.element_of

    @property
    def writable(self) -> bool:
        if self.is_array or self.is_table:
            return False  # one of its values is; the row as a whole is not typed in
        if self.conversion is not None and not self.conversion.invertible:
            return False
        return self.kind == "CHARACTERISTIC" and self.readable and not self.read_only

    @property
    def unit(self) -> str:
        return _unit(self.conversion, self.phys_unit)

    @property
    def decimals(self) -> int | None:
        """The places after the point it is shown with: its own FORMAT, then its
        conversion's; None for a value shown raw, or a file that does not say."""
        return _places(self.conversion, self.own_decimals)

    @property
    def is_text(self) -> bool:
        return self.shape == ASCII and self.readable

    @property
    def choices(self) -> dict[int, str]:
        return self.conversion.choices if self.conversion else {}


@dataclass(frozen=True)
class Area:
    """A stretch of the slave's memory that is erased and programmed as one.

    From an A2L, an XCP programming SECTOR or a MOD_PAR MEMORY_SEGMENT; or
    typed into the Transfer tab, or made from a file's own segments, which
    is how the A2L is overridden.
    """

    name: str
    address: int
    size: int
    #: The order areas are erased in, then the order they are programmed in.
    #: A SECTOR gives both; a MEMORY_SEGMENT gives neither, and is taken in
    #: address order.
    clear_order: int = 0
    program_order: int = 0
    #: FLASH, RAM, EEPROM and the rest, for a MEMORY_SEGMENT; empty otherwise.
    memory: str = ""
    #: Where it came from: "SECTOR", "MEMORY_SEGMENT", "typed" or "file".
    source: str = ""
    extension: int = 0

    @property
    def end(self) -> int:
        return self.address + self.size


@dataclass(frozen=True)
class ChecksumInfo:
    """What an A2L says about BUILD_CHECKSUM, for one protocol.

    XCP's IF_DATA has a ``CHECKSUM`` block, ``XCP_CRC_32 MAX_BLOCK_SIZE ...``;
    CCP's has ``CHECKSUM_PARAM`` in its TP_BLOB, a procedure number and the
    largest block one command may sum. The procedure numbers are XCP's
    checksum types; a number above them is the maker's own, and kept as
    ``code`` so it can be shown even though it cannot be worked out here.
    """

    kind: int | None
    code: int
    max_block: int | None = None


#: XCP's checksum types by the names its A2L blocks use.
CHECKSUM_NAMES = {
    "XCP_ADD_11": 0x01,
    "XCP_ADD_12": 0x02,
    "XCP_ADD_14": 0x03,
    "XCP_ADD_22": 0x04,
    "XCP_ADD_24": 0x05,
    "XCP_ADD_44": 0x06,
    "XCP_CRC_16": 0x07,
    "XCP_CRC_16_CITT": 0x08,
    "XCP_CRC_32": 0x09,
    "XCP_USER_DEFINED": 0xFF,
}

#: Memory segments that are flashed, by their memory type.
FLASHED = {"FLASH", "EEPROM", "EPROM"}
#: Programming types a segment is never flashed for.
NOT_FLASHED = {"RESERVED", "EXCLUDE_FROM_FLASH", "VARIABLES"}


@dataclass
class XcpOnCan:
    """The identifiers an A2L gives for XCP on CAN, where it gives them."""

    command_id: int
    response_id: int
    extended: bool = False
    bitrate: int | None = None
    #: MAX_DLC_REQUIRED: the slave ignores a command shorter than 8 bytes.
    padded: bool = False


#: Told how far a read has got, from 0 to 1. A big file is seconds of work,
#: and whoever asked for it can show that it is going on. It may raise, to
#: stop the read: nothing here catches it, and nothing has been kept by then.
Progress = Callable[[float], None]

#: How often it is told: every so many words of the file, and every so many
#: of its measurements and characteristics.
WORDS_A_REPORT = 20_000
BLOCKS_A_REPORT = 1_000

#: How much of the time each part takes, as measured on an 18 MB file:
#: splitting the text into words, and nesting them, against reading the blocks.
WORDS_SHARE, NESTED_SHARE = 0.5, 0.6


class A2l:
    def __init__(self) -> None:
        self.parameters: dict[str, Parameter] = {}
        self.conversions: dict[str, Conversion] = {}
        #: Record layout name -> the data type of the values it lays out.
        self.layouts: dict[str, str] = {}
        #: The layouts that hold the values and nothing before them.
        self._only_values: set[str] = set()
        #: The layouts that store their values column by column.
        self._by_column: set[str] = set()
        #: Every record layout, read in full: where a curve's or a map's parts are.
        self.record_layouts: dict[str, layouts.Layout] = {}
        #: MOD_COMMON's ALIGNMENT_* settings, by data type.
        self.alignment: dict[str, int] = {}
        #: GROUPs and FUNCTIONs by name: how the file sorts its parameters.
        self.groups: dict[str, Group] = {}
        #: XCP's timeouts T1 to T7 from its PROTOCOL_LAYER, in seconds, by name.
        self.timeouts: dict[str, float] = {}
        #: The axis points a curve or a map keeps in its own record, as
        #: parameters of their own: ``Map:x``. Not in ``parameters``, which is
        #: what the file lists, but found by name like them.
        self._axes: dict[str, Parameter] = {}
        #: Elements already asked for, by name: made when wanted, since a big
        #: file has a hundred thousand of them and uses a handful.
        self._elements: dict[str, Parameter] = {}
        #: The file's own byte order, True for most significant first; None
        #: where it does not say, and the slave's own answer stands.
        self.big_endian: bool | None = None
        #: XCP on CAN identifiers from the file's IF_DATA, if it has them.
        self.xcp_on_can: XcpOnCan | None = None
        #: MOD_PAR's MEMORY_SEGMENTs, every one of them, in the file's order.
        self.memory_segments: list[Area] = []
        #: The SECTORs of an XCP IF_DATA's PGM block: what is flashed, and in
        #: which order, where the file says.
        self.sectors: list[Area] = []
        #: The checksum each protocol's IF_DATA names, by "XCP" or "CCP".
        self.checksums: dict[str, ChecksumInfo] = {}
        #: MOD_PAR's EPK: the software version this file was made for, and
        #: where in the slave's memory the slave keeps its own, to compare.
        self.epk: str | None = None
        self.epk_address: int | None = None
        #: Where it was read from, so the pane can say which file these names
        #: came from. Empty for one parsed from text, as the tests do.
        self.path = ""

    @classmethod
    def parse(cls, text: str, folder: Path | None = None, progress: Progress | None = None) -> A2l:
        a2l = cls()
        tree = _tree(_tokens(text, folder, progress=progress))
        modules = _all(tree, "MODULE") or [tree]  # a fragment with no MODULE round it
        for number, module in enumerate(modules):

            def of_this_module(part: float, number: int = number) -> None:
                share = (number + part) / len(modules)
                progress(NESTED_SHARE + (1 - NESTED_SHARE) * share)

            a2l._read_module(module, of_this_module if progress else None)
        return a2l

    @classmethod
    def load(cls, path: str, progress: Progress | None = None) -> A2l:
        a2l = cls.parse(_read(Path(path)), Path(path).parent, progress)
        a2l.path = str(path)
        return a2l

    def measurements(self) -> list[Parameter]:
        return [p for p in self.parameters.values() if p.kind == "MEASUREMENT"]

    def characteristics(self) -> list[Parameter]:
        return [p for p in self.parameters.values() if p.kind == "CHARACTERISTIC"]

    def find(self, name: str) -> Parameter | None:
        """A parameter by name, or one value of an array as ``name[3]`` or
        ``name[1][3]``.

        A parameter the file itself calls ``table[3]`` is found first, as
        itself: files generated from C structures are full of those.
        """
        found = self.parameters.get(name) or self._axes.get(name) or self._elements.get(name)
        if found is not None:
            return found
        match = _INDICES.search(name)
        if match is None:
            return None
        indices = [int(digits) for digits in re.findall(r"\d+", match.group(0))]
        array = None
        # The array may have brackets in its own name, so the name is tried
        # with each number of indices taken off the end: ``a[2].b[1][3]`` is
        # ``a[2].b`` with two, before it is ``a[2].b[1]`` with one.
        for taken in range(len(indices), 0, -1):
            base = name[: match.start()] + "".join(f"[{i}]" for i in indices[:-taken])
            candidate = self.parameters.get(base) or self._axes.get(base)
            if (
                candidate is not None
                and candidate.is_array
                and len(candidate.sizes) == taken
                and all(i < size for i, size in zip(indices[-taken:], candidate.sizes, strict=True))
            ):
                array, indices = candidate, indices[-taken:]
                break
        if array is None:
            return None
        strides = array.strides or _strides(array.sizes)
        index = sum(position * stride for position, stride in zip(indices, strides, strict=True))
        element = replace(
            array,
            name=name,
            address=array.address + index * DATATYPES[array.datatype][1],
            count=1,
            sizes=(),
            strides=(),
            axes=(),
            counted=(),
            element_of=array.name,
            index=index,
        )
        self._elements[name] = element
        return element

    # --- curves and maps ----------------------------------------------------------------
    def counts_to_read(self, table: Parameter) -> list[tuple[str, str, int, str, Parameter]]:
        """The numbers of axis points to read from the slave before ``table``
        can be found: ``(holder, axis, address, datatype, holder parameter)``.

        A curve's own, and those of any shared axis (AXIS_PTS) it uses.
        """
        wanted = []
        holders = [table] + [
            self.parameters[a.points]
            for a in table.axes
            if a.attribute == "COM_AXIS" and a.points in self.parameters
        ]
        for holder in holders:
            layout = self.record_layouts.get(holder.layout)
            if layout is None:
                continue
            most = {a.letter: a.most for a in holder.axes}
            where = layout.place(holder.start, {}, most, self.alignment)
            for letter in holder.counted:
                part = layout.part("NO_AXIS_PTS", letter)
                if part is not None:
                    at = where[("NO_AXIS_PTS", letter)]
                    wanted.append((holder.name, letter, at, part.datatype, holder))
        return wanted

    def resolve(self, name: str, counts: dict[tuple[str, str], int] | None = None) -> Parameter:
        """Work out where a curve's, a map's or an AXIS_PTS's values and axis
        points are, with ``counts`` the numbers of points read from the slave,
        by ``(parameter, axis)``. The parameter is replaced by the one worked
        out, which is returned; so are its axes, and the values found before.
        """
        counts = counts or {}
        table = self.parameters[name]
        # A shared axis first: its number of points is this one's too.
        for axis in table.axes:
            if axis.attribute == "COM_AXIS" and axis.points in self.parameters:
                shared = self.parameters[axis.points]
                if shared.counted or not shared.sizes:
                    self.resolve(shared.name, counts)
        table = self.parameters[name]
        layout = self.record_layouts[table.layout]
        how_many: dict[str, int] = {}
        for axis in table.axes:
            if (table.name, axis.letter) in counts:
                how_many[axis.letter] = counts[(table.name, axis.letter)]
            elif axis.attribute == "COM_AXIS":
                shared = self.parameters.get(axis.points)
                how_many[axis.letter] = shared.count if shared is not None else axis.most
            else:
                how_many[axis.letter] = axis.count if axis.count is not None else axis.most
        most = {axis.letter: axis.most for axis in table.axes}
        if any(how_many[letter] > most[letter] or how_many[letter] < 1 for letter in how_many):
            raise ValueError(
                f"{name}: the slave says {how_many}, beyond the most points the A2L allows, {most}"
            )
        where = layout.place(table.start, how_many, most, self.alignment)
        for old in [n for n in self._elements if n.startswith((name + "[", name + ":"))]:
            del self._elements[old]
        axes = []
        for axis in table.axes:
            n = how_many[axis.letter]
            if axis.attribute == "STD_AXIS" or table.shape == AXIS_PTS:
                points_name = name if table.shape == AXIS_PTS else f"{name}:{axis.letter.lower()}"
                part = layout.part("AXIS_PTS", axis.letter)
                points = Parameter(
                    name=points_name,
                    kind="CHARACTERISTIC",
                    datatype=part.datatype,
                    address=where[("AXIS_PTS", axis.letter)],
                    description=f"{axis.letter} axis of {name}: {axis.input}",
                    conversion=axis.conversion,
                    lower=axis.lower,
                    upper=axis.upper,
                    shape=AXIS_PTS,
                    big_endian=table.big_endian,
                    extension=table.extension,
                    read_only=table.read_only or axis.read_only,
                    count=n,
                    sizes=(n,),
                    written=(n,),
                    own_decimals=axis.own_decimals,
                    phys_unit=axis.phys_unit,
                )
                if table.shape != AXIS_PTS:
                    self._axes[points_name] = points
                axis = replace(axis, points=points_name, count=n)
            else:
                axis = replace(axis, count=n)
            axes.append(axis)
        if table.shape == AXIS_PTS:
            resolved = replace(
                points,
                name=name,
                description=table.description,
                axes=tuple(axes),
                layout=table.layout,
                start=table.start,
                counted=(),
            )
        else:
            sizes = tuple(how_many[axis.letter] for axis in table.axes)
            room = most if layout.static else how_many
            if table.shape == MAP and layout.values.order == "ROW_DIR":
                strides = (1, room["X"])  # a row is the values along x
            elif table.shape == MAP:
                strides = (room["Y"], 1)
            else:
                strides = (1,)
            resolved = replace(
                table,
                address=where[("FNC_VALUES", "")],
                datatype=layout.values.datatype,
                count=math.prod(sizes),
                sizes=sizes,
                written=sizes,
                strides=strides,
                column_dir=layout.values.order == "COLUMN_DIR",
                axes=tuple(axes),
                counted=(),
            )
            if resolved.count > MOST_ELEMENTS:
                resolved.unreadable = (
                    f"it is {resolved.count} values, and up to {MOST_ELEMENTS} are opened"
                )
        self.parameters[name] = resolved
        return resolved

    def elements(self, array: Parameter) -> list[Parameter]:
        """Each value of an array, in the order they are stored; nothing for
        what is not one."""
        if not array.is_array:
            return []
        return [
            self.find(array.name + "".join(f"[{i}]" for i in indices))
            for indices in product(*(range(size) for size in array.sizes))
        ]

    def flash_areas(self) -> list[Area]:
        """What the Transfer tab programs, as the file has it.

        The programming SECTORs where there are any, since they are written
        for exactly this and give the order. Otherwise the memory segments
        that are flash, erased and programmed in address order.
        """
        if self.sectors:
            return list(self.sectors)
        # One marked RESERVED or EXCLUDE_FROM_FLASH says so in its memory
        # type, "FLASH (RESERVED)", which is then not one of these.
        flashed = [a for a in self.memory_segments if a.memory in FLASHED and a.size > 0]
        flashed.sort(key=lambda a: a.address)
        return [replace(a, clear_order=n, program_order=n) for n, a in enumerate(flashed)]

    def members(self, name: str) -> set[str]:
        """Every parameter in a group or a function, with those in the groups
        inside it, however deep. A group the file puts inside itself is
        gone into once."""
        found: set[str] = set()
        seen: set[str] = set()
        waiting = [name]
        while waiting:
            group = self.groups.get(waiting.pop())
            if group is None or group.name in seen:
                continue
            seen.add(group.name)
            found.update(group.members)
            waiting.extend(group.inside)
        return found

    def group_tree(self, kind: str) -> list[tuple[int, Group]]:
        """The groups, or the functions, in the order to list them, each with how
        deep it is: the top ones (marked ROOT, or inside no other) in the file's
        order, each followed by those inside it."""
        of_kind = {n: g for n, g in self.groups.items() if g.kind == kind}
        inside = {child for g in of_kind.values() for child in g.inside}
        tops = [g for g in of_kind.values() if g.root or g.name not in inside]
        listed: list[tuple[int, Group]] = []
        seen: set[str] = set()

        def add(group: Group, depth: int) -> None:
            if group.name in seen:
                return
            seen.add(group.name)
            listed.append((depth, group))
            for child in group.inside:
                if child in of_kind:
                    add(of_kind[child], depth + 1)

        for group in tops:
            add(group, 0)
        return listed

    def unreadable(self) -> list[Parameter]:
        """The ones listed and not read: curves, maps, types not known."""
        return [p for p in self.parameters.values() if not p.readable]

    # --- one module ---------------------------------------------------------------------
    def _read_module(self, module: _Block, progress: Progress | None = None) -> None:
        for common in module.blocks("MOD_COMMON"):
            if (order := common.after("BYTE_ORDER")) and order[0] in BYTE_ORDERS:
                self.big_endian = BYTE_ORDERS[order[0]]
            self.alignment.update(layouts.module_alignment(common.fields()))
        tables = {}
        for kind in ("COMPU_VTAB", "COMPU_VTAB_RANGE", "COMPU_TAB"):
            for block in module.blocks(kind):
                if (table := _table(block)) is not None:
                    tables[table[0]] = table[1]
        numbers = {}
        for block in module.blocks("COMPU_TAB"):
            if (rows := _numbers(block)) is not None:
                numbers[rows[0]] = rows[1:]
        for block in module.blocks("COMPU_METHOD"):
            if (conversion := _conversion(block, tables, numbers)) is not None:
                self.conversions[conversion.name] = conversion
        for block in module.blocks("RECORD_LAYOUT"):
            fields = block.fields()
            if fields:
                self.record_layouts[str(fields[0])] = layouts.parse(str(fields[0]), fields[1:])
            if fields and (values := block.after("FNC_VALUES", 2)):
                self.layouts[fields[0]] = values[1]
                more = any(
                    not isinstance(word, _Text) and str(word).startswith(_MORE_THAN_VALUES)
                    for word in fields[1:]
                )
                # Its position is not asked: it is the order among the parts,
                # and with one part it says nothing. Tools number it 0 or 1.
                if not more:
                    self._only_values.add(fields[0])
                if (how := block.after("FNC_VALUES", 3)) and how[2] == "COLUMN_DIR":
                    self._by_column.add(fields[0])
        measurements = module.blocks("MEASUREMENT")
        characteristics = module.blocks("CHARACTERISTIC")
        # The shared axes before the curves and maps that use them.
        readers = [(self._axis_points, block) for block in module.blocks("AXIS_PTS")]
        readers += [(self._measurement, block) for block in measurements]
        readers += [(self._characteristic, block) for block in characteristics]
        for done, (read, block) in enumerate(readers):
            if progress is not None and not done % BLOCKS_A_REPORT:
                progress(done / len(readers))
            if (parameter := read(block)) is not None:
                self.parameters[parameter.name] = parameter
        if self.xcp_on_can is None:
            self.xcp_on_can = _xcp_on_can(module)
        if not self.timeouts:
            self.timeouts = _timeouts(module)
        for protocol, info in _checksums(module).items():
            self.checksums.setdefault(protocol, info)
        self.memory_segments += _memory_segments(module)
        self.sectors += _sectors(module)
        self._read_groups(module, measurements + characteristics)
        for parameters in _all(module, "MOD_PAR"):
            if self.epk is None and (epk := parameters.after("EPK")):
                self.epk = str(epk[0])
            if self.epk_address is None and (at := parameters.after("ADDR_EPK")):
                self.epk_address = _integer(at[0])

    def _read_groups(self, module: _Block, blocks: list[_Block]) -> None:
        """GROUPs and FUNCTIONs, and the FUNCTION_LIST an older file puts in a
        parameter to say which functions it belongs to."""
        for kind, lists, inner in (
            ("GROUP", GROUP_LISTS, "SUB_GROUP"),
            ("FUNCTION", FUNCTION_LISTS, "SUB_FUNCTION"),
        ):
            for block in module.blocks(kind):
                f = block.fields()
                if not f:
                    continue
                group = Group(
                    str(f[0]),
                    _plain(f[1]) if len(f) > 1 else "",
                    kind,
                    root=block.has("ROOT"),
                )
                for listed in lists:
                    for part in block.blocks(listed):
                        group.members += [str(name) for name in part.fields()]
                for part in block.blocks(inner):
                    group.inside += [str(name) for name in part.fields()]
                self.groups.setdefault(group.name, group)
        for block in blocks:
            for listed in block.blocks("FUNCTION_LIST"):
                name = str(block.fields()[0]) if block.fields() else ""
                for function in listed.fields():
                    group = self.groups.setdefault(
                        str(function), Group(str(function), "", "FUNCTION")
                    )
                    if name and name not in group.members:
                        group.members.append(name)

    def _measurement(self, block: _Block) -> Parameter | None:
        """Name LongIdentifier Datatype Conversion Resolution Accuracy Lower Upper."""
        f = block.fields()
        if len(f) < 8:
            return None
        address = _integer(found[0]) if (found := block.after("ECU_ADDRESS")) else None
        if address is None:
            address = _older_address(block)
        parameter = Parameter(
            name=f[0],
            kind="MEASUREMENT",
            datatype=f[2],
            address=address or 0,
            description=_plain(f[1]),
            conversion=self.conversions.get(f[3]),
            lower=_number(f[6]),
            upper=_number(f[7]),
        )
        if address is None:
            parameter.unreadable = "the file gives it no address"
        else:
            by_column = bool((layout := block.after("LAYOUT")) and layout[0] == "COLUMN_DIR")
            _sized(parameter, _dimensions(block, "MATRIX_DIM", "ARRAY_SIZE"), by_column)
        self._common(block, parameter)
        return parameter

    def _characteristic(self, block: _Block) -> Parameter | None:
        """Name LongIdentifier Type Address Deposit MaxDiff Conversion Lower Upper."""
        f = block.fields()
        if len(f) < 9:
            return None
        parameter = Parameter(
            name=f[0],
            kind="CHARACTERISTIC",
            datatype=self.layouts.get(f[4], ""),
            address=_integer(f[3]) or 0,
            description=_plain(f[1]),
            conversion=self.conversions.get(f[6]),
            lower=_number(f[7]),
            upper=_number(f[8]),
            shape=f[2],
            read_only=block.has("READ_ONLY"),
        )
        dimensions = _dimensions(block, "MATRIX_DIM", "NUMBER")
        if parameter.shape in (CURVE, MAP):
            self._table(block, parameter, f[4])
        elif parameter.shape == ASCII:
            if not parameter.datatype:
                parameter.unreadable = f"its record layout, {f[4]}, is not in the file"
            elif DATATYPES.get(parameter.datatype, ("", 0))[1] != 1:
                parameter.unreadable = f"its characters are {parameter.datatype}, not bytes"
            elif not dimensions:
                parameter.unreadable = "the file does not say how long it is"
            else:
                parameter.length = _values(dimensions)
        elif parameter.shape not in (VALUE, VAL_BLK):
            what = {
                "CUBOID": "a three-dimensional map",
                "CUBE_4": "a four-dimensional map",
                "CUBE_5": "a five-dimensional map",
            }.get(parameter.shape, parameter.shape)
            parameter.unreadable = (
                f"it is {what}, and values, rows of them, text, curves and maps are what is read"
            )
        elif not parameter.datatype:
            parameter.unreadable = f"its record layout, {f[4]}, is not in the file"
        elif parameter.shape == VAL_BLK and not dimensions:
            parameter.unreadable = "the file does not say how many values it holds"
        elif _values(dimensions) > 1 and f[4] not in self._only_values:
            parameter.unreadable = (
                f"its record layout, {f[4]}, holds more than the values, and where they "
                "start is not worked out"
            )
        else:
            _sized(parameter, dimensions, f[4] in self._by_column)
        self._common(block, parameter)
        # A curve or a map worked out already is in the list as a new object,
        # which the keywords above were not read into: do it again for that.
        if (resolved := self.parameters.get(parameter.name)) is not None and (
            resolved is not parameter
        ):
            self._common(block, resolved)
            return resolved
        return parameter

    def _table(self, block: _Block, parameter: Parameter, layout_name: str) -> None:
        """A curve or a map: its axes, and where its parts are where that is
        known without asking the slave."""
        parameter.layout, parameter.start = layout_name, parameter.address
        layout = self.record_layouts.get(layout_name)
        descriptions = block.blocks("AXIS_DESCR")
        wanted = 1 if parameter.shape == CURVE else 2
        if layout is None:
            parameter.unreadable = f"its record layout, {layout_name}, is not in the file"
            return
        if len(descriptions) != wanted:
            parameter.unreadable = f"it has {len(descriptions)} axes where it should have {wanted}"
            return
        if layout.problem or layout.values is None:
            parameter.unreadable = (
                f"its record layout, {layout_name}: {layout.problem or 'it holds no values'}"
            )
            return
        axes = []
        for letter, description in zip(AXIS_LETTERS, descriptions, strict=False):
            axis, why = self._axis(letter, description, layout)
            if why:
                parameter.unreadable = why
                return
            axes.append(axis)
        parameter.axes = tuple(axes)
        parameter.datatype = layout.values.datatype
        self._place(parameter, layout)

    def _axis(self, letter: str, block: _Block, layout: layouts.Layout) -> tuple[Axis, str]:
        """``AXIS_DESCR Attribute InputQuantity Conversion MaxAxisPoints Lower Upper``."""
        f = block.fields()
        if len(f) < 6:
            return Axis(letter, "", "", None, 0), f"its {letter} axis is cut short"
        axis = Axis(
            letter,
            attribute=str(f[0]),
            input=str(f[1]),
            conversion=self.conversions.get(f[2]),
            most=_integer(f[3]) or 0,
            lower=_number(f[4]),
            upper=_number(f[5]),
            read_only=block.has("READ_ONLY"),
        )
        if shown := block.after("FORMAT"):
            axis.own_decimals = _decimals(shown[0])
        if unit := block.after("PHYS_UNIT"):
            axis.phys_unit = _plain(unit[0])
        if axis.conversion is not None and not axis.conversion.exact:
            axis.conversion = Conversion(axis.conversion.name, unit="raw", exact=False)
        if axis.attribute not in READ_AXES:
            return axis, f"its {letter} axis is a {axis.attribute}, which is not read"
        if (deposit := block.after("DEPOSIT")) and deposit[0] == "DIFFERENCE":
            return axis, f"its {letter} axis is stored as differences, which are not read"
        if axis.attribute == "STD_AXIS":
            if layout.part("AXIS_PTS", letter) is None:
                return axis, f"its record layout has no axis points for its {letter} axis"
            if letter in layout.fixed_counts:
                axis.count = layout.fixed_counts[letter]
        elif axis.attribute == "COM_AXIS":
            reference = block.after("AXIS_PTS_REF")
            shared = self.parameters.get(reference[0]) if reference else None
            if shared is None or shared.shape != AXIS_PTS:
                return axis, f"its {letter} axis names an AXIS_PTS that is not in the file"
            if shared.unreadable:
                return axis, f"its {letter} axis, {shared.name}, is not read: {shared.unreadable}"
            axis.points = shared.name
            axis.most = shared.axes[0].most
        else:
            axis.fixed = _fixed_points(block)
            if not axis.fixed:
                return axis, f"its {letter} axis is fixed, but the file gives no points"
            axis.count = len(axis.fixed)
        return axis, ""

    def _axis_points(self, block: _Block) -> Parameter | None:
        """``AXIS_PTS Name LongIdentifier Address InputQuantity Deposit MaxDiff
        Conversion MaxAxisPoints Lower Upper``: axis points that curves and maps share."""
        f = block.fields()
        if len(f) < 10:
            return None
        address = _integer(f[2]) or 0
        conversion = self.conversions.get(f[6])
        parameter = Parameter(
            name=f[0],
            kind="CHARACTERISTIC",
            datatype="",
            address=address,
            description=_plain(f[1]),
            conversion=conversion,
            lower=_number(f[8]),
            upper=_number(f[9]),
            shape=AXIS_PTS,
            read_only=block.has("READ_ONLY"),
            layout=str(f[4]),
            start=address,
        )
        self._common(block, parameter)
        layout = self.record_layouts.get(str(f[4]))
        axis = Axis(
            "X",
            "STD_AXIS",
            str(f[3]),
            parameter.conversion,
            _integer(f[7]) or 0,
            parameter.lower,
            parameter.upper,
            read_only=parameter.read_only,
        )
        axis.own_decimals, axis.phys_unit = parameter.own_decimals, parameter.phys_unit
        parameter.axes = (axis,)
        if layout is None:
            parameter.unreadable = f"its record layout, {f[4]}, is not in the file"
        elif layout.problem:
            parameter.unreadable = f"its record layout, {f[4]}: {layout.problem}"
        elif (part := layout.part("AXIS_PTS", "X")) is None:
            parameter.unreadable = f"its record layout, {f[4]}, holds no axis points"
        elif (deposit := block.after("DEPOSIT")) and deposit[0] == "DIFFERENCE":
            parameter.unreadable = "it is stored as differences, which are not read"
        else:
            parameter.datatype = part.datatype
            if "X" in layout.fixed_counts:
                parameter.axes = (replace(axis, count=layout.fixed_counts["X"]),)
            self._place(parameter, layout)
        return parameter

    def _place(self, parameter: Parameter, layout: layouts.Layout) -> None:
        """Work out where its parts are now, or say what has to be read first.

        Where the number of points is kept in the slave, it is read when the
        parameter is, and only then are the parts found -- unless something
        that moves with it comes before the count, which would leave the count
        itself nowhere to be found.
        """
        counted = tuple(
            a.letter for a in parameter.axes if a.letter in layout.counted and a.count is None
        )
        shared = [
            self.parameters[a.points]
            for a in parameter.axes
            if a.attribute == "COM_AXIS" and a.points in self.parameters
        ]
        if counted and not layout.static:
            for letter in counted:
                count_at = layout.part("NO_AXIS_PTS", letter).position
                moving = [
                    p
                    for p in layout.parts
                    if p.kind in ("AXIS_PTS", "FNC_VALUES") and p.position < count_at
                ]
                if moving:
                    parameter.unreadable = (
                        f"its record layout keeps the number of {letter} points after parts "
                        "that move with it"
                    )
                    return
        parameter.counted = counted
        if counted or any(s.counted for s in shared):
            return  # read when it is: the slave says how many points there are
        self.parameters[parameter.name] = parameter
        self.resolve(parameter.name)

    def _common(self, block: _Block, parameter: Parameter) -> None:
        """The optional keywords a measurement and a characteristic share."""
        if mask := block.after("BIT_MASK"):
            parameter.bit_mask = _integer(mask[0])
        if (order := block.after("BYTE_ORDER")) and order[0] in BYTE_ORDERS:
            parameter.big_endian = BYTE_ORDERS[order[0]]
        if parameter.readable and parameter.datatype and parameter.datatype not in DATATYPES:
            parameter.unreadable = f"its data type, {parameter.datatype}, is not one pycangui reads"
        if extension := block.after("ECU_ADDRESS_EXTENSION"):
            parameter.extension = _integer(extension[0]) or 0
        if parameter.conversion is not None and not parameter.conversion.exact:
            # Read, and shown raw: better a true number than a wrong one.
            parameter.conversion = Conversion(
                parameter.conversion.name, unit="raw", exact=False, kind=parameter.conversion.kind
            )
        if shown := block.after("FORMAT"):
            parameter.own_decimals = _decimals(shown[0])
        if unit := block.after("PHYS_UNIT"):
            parameter.phys_unit = _plain(unit[0])


# --- the blocks this reader knows ------------------------------------------------------------
def _conversion(
    block: _Block,
    tables: dict[str, dict[int, str] | None],
    numbers: dict | None = None,
) -> Conversion | None:
    """Name LongIdentifier ConversionType Format Unit, then how.

    RAT_FUNC's six coefficients give the raw value from the physical one,
    ``raw = (a*p*p + b*p + c) / (d*p*p + e*p + f)``. With a, d and e zero that
    is a straight line, ``raw = (b*p + c) / f``, which turns round to
    ``p = (f/b)*raw - c/b``. With only a and d zero it is one straight line
    over another, which turns round as well: ``p = (c - f*raw) / (e*raw - b)``.
    LINEAR's two go the other way already.
    """
    f = block.fields()
    if len(f) < 5:
        return None
    conversion = Conversion(name=f[0], unit=_plain(f[4]), kind=f[2], decimals=_decimals(f[3]))
    if conversion.kind == "IDENTICAL":
        return conversion
    if linear := block.after("COEFFS_LINEAR", 2):
        a, b = (_number(x) for x in linear)
        if a is not None and b is not None:
            conversion.factor, conversion.offset = a, b
            return conversion
    if rational := block.after("COEFFS", 6):
        a, b, c, d, e, g = (_number(x) for x in rational)
        if None not in (a, b, c, d, e, g) and a == 0 and d == 0:
            if e == 0 and b and g:
                conversion.factor, conversion.offset = g / b, -c / b
                return conversion
            if b * g != c * e:  # equal, and raw does not depend on the value at all
                conversion.fraction = (b, c, e, g)
                return conversion
        elif None not in (a, b, c, d, e, g):
            conversion.quadratic = (a, b, c, d, e, g)
            return conversion
    reference = block.after("COMPU_TAB_REF")
    if reference and conversion.kind in ("TAB_INTP", "TAB_NOINTP"):
        if numbers and (rows := numbers.get(reference[0])) is not None:
            table, interpolate, default = rows
            conversion.table, conversion.default = table, default
            conversion.interpolate = conversion.kind == "TAB_INTP" and interpolate
            return conversion
    if reference and tables.get(reference[0]):
        conversion.choices = tables[reference[0]]
        return conversion
    if conversion.kind == "FORM" and (formulas := block.blocks("FORMULA")):
        words = formulas[0].fields()
        backwards = formulas[0].after("FORMULA_INV")
        try:
            conversion.formula = compile_formula(str(words[0]))
            if backwards:
                conversion.inverse = compile_formula(str(backwards[0]))
            return conversion
        except (FormulaError, IndexError):
            conversion.formula = conversion.inverse = None
    conversion.exact = False
    return conversion


def _numbers(block: _Block) -> tuple | None:
    """A COMPU_TAB of numbers: ``Name LongIdentifier ConversionType Count (in out)...``
    and an optional ``DEFAULT_VALUE_NUMERIC``. As ``(name, rows by raw value,
    whether to interpolate, default)``."""
    f = block.fields()
    if len(f) < 4:
        return None
    count = _integer(f[3]) or 0
    rows = []
    for raw, phys in zip(f[4 : 4 + 2 * count : 2], f[5 : 5 + 2 * count : 2], strict=False):
        a, b = _number(raw), _number(phys)
        if a is None or b is None:
            return None
        rows.append((a, b))
    if not rows:
        return None
    default = block.after("DEFAULT_VALUE_NUMERIC")
    return (
        str(f[0]),
        tuple(sorted(rows)),
        str(f[2]) == "TAB_INTP",
        _number(default[0]) if default else None,
    )


def _fixed_points(block: _Block) -> tuple[float, ...]:
    """A FIX_AXIS's raw points: ``FIX_AXIS_PAR offset shift count`` (each one
    2 to the shift further on), ``FIX_AXIS_PAR_DIST offset distance count``, or
    a ``FIX_AXIS_PAR_LIST`` of them."""
    if par := block.after("FIX_AXIS_PAR", 3):
        offset, shift, count = (_number(x) for x in par)
        if None not in (offset, shift, count):
            return tuple(offset + i * 2**shift for i in range(int(count)))
    if par := block.after("FIX_AXIS_PAR_DIST", 3):
        offset, distance, count = (_number(x) for x in par)
        if None not in (offset, distance, count):
            return tuple(offset + i * distance for i in range(int(count)))
    for listed in block.blocks("FIX_AXIS_PAR_LIST"):
        points = [_number(x) for x in listed.fields()]
        if points and None not in points:
            return tuple(points)
    return ()


def _decimals(token) -> int | None:
    """The places after the point a FORMAT asks for: ``%6.1`` is one."""
    match = _FORMAT.match(str(token).strip())
    return int(match.group(1)) if match else None


def _strides(sizes: tuple[int, ...]) -> tuple[int, ...]:
    """How far apart the values along each index are, the last changing fastest."""
    strides, step = [], 1
    for size in reversed(sizes):
        strides.append(step)
        step *= size
    return tuple(reversed(strides))


def _table(block: _Block) -> tuple[str, dict[int, str] | None] | None:
    """A table of names by value, or None for one this reader does not use.

    COMPU_VTAB is ``Name LongIdentifier Type Count (value "name")...`` and
    COMPU_VTAB_RANGE is ``Name LongIdentifier Count (low high "name")...``; a
    range names every whole value in it, which is how they are nearly always
    used -- one state per number, written as a range of one. A range too wide
    to list, and COMPU_TAB, a table of numbers to interpolate, are left out.
    """
    f = block.fields()
    if block.kind == "COMPU_VTAB" and len(f) >= 4:
        names: dict[int, str] = {}
        pairs = f[4:]
        for value, name in zip(pairs[0::2], pairs[1::2], strict=False):
            if value == "DEFAULT_VALUE" and not isinstance(value, _Text):
                break
            if (number := _number(value)) is not None and isinstance(name, _Text):
                names[int(number)] = _plain(name)
        return f[0], names or None
    if block.kind == "COMPU_VTAB_RANGE" and len(f) >= 3:
        names = {}
        triples = f[3:]
        for low, high, name in zip(triples[0::3], triples[1::3], triples[2::3], strict=False):
            first, last = _number(low), _number(high)
            if first is None or last is None or not isinstance(name, _Text):
                break
            if last - first > 255:
                return f[0], None
            for value in range(int(first), int(last) + 1):
                names[value] = _plain(name)
        return f[0], names or None
    return (f[0], None) if f else None


def _dimensions(block: _Block, *keywords: str) -> list[int]:
    """How many values a block says it is, along each dimension; [] if it does not say.

    From the first of the keywords it has: ``MATRIX_DIM`` with up to three
    sizes, or ``ARRAY_SIZE`` or ``NUMBER`` with one.
    """
    fields = block.fields()
    for keyword in keywords:
        for at, item in enumerate(fields):
            if item != keyword or isinstance(item, _Text):
                continue
            sizes = []
            for size in fields[at + 1 : at + 4]:
                if isinstance(size, _Text) or not str(size).isdigit():
                    break
                sizes.append(int(size))
            if sizes:
                return sizes
    return []


def _values(dimensions: list[int]) -> int:
    return math.prod(dimensions) if dimensions else 1


def _sized(parameter: Parameter, dimensions: list[int], by_column: bool) -> None:
    """Make a parameter the array its dimensions say, where it is one.

    ``ARRAY_SIZE 1`` and ``MATRIX_DIM 1 1 1`` are written by tools that say
    it for everything, and are one value all the same.

    **Which value is which, in more than one dimension.**  The values are
    read in the order they are stored, which is never in doubt. What has to
    be decided is what to call each one. ``MATRIX_DIM x y z`` gives the
    sizes, and the storage is row by row unless the file says column by
    column: row by row, x is the one that changes fastest; column by column,
    it is the last. Here the sizes are put in the order of storage, slowest
    first, so that an element is named as C would name it and the names
    count up in the order the values lie: ``MATRIX_DIM 24 2`` stored row by
    row is two rows of twenty-four, ``[0][0]`` to ``[1][23]``. That is what
    a generator working from ``uint8 table[2][24]`` writes. The file's own
    numbers are kept beside it, to be shown, because generators are not
    agreed on this and somebody may need to see what theirs said.
    """
    count = _values(dimensions)
    if count <= 1:
        return
    if count > MOST_ELEMENTS:
        parameter.unreadable = (
            f"it is {count} values, and an array of up to {MOST_ELEMENTS} is what is opened"
        )
        return
    more_than_one = [size for size in dimensions if size > 1]
    parameter.count = count
    parameter.written = tuple(dimensions)
    parameter.column_dir = by_column
    parameter.sizes = tuple(more_than_one if by_column else reversed(more_than_one))


def _older_address(block: _Block) -> int | None:
    """A measurement's address from its IF_DATA, as files from before
    ``ECU_ADDRESS`` have it: ``KP_BLOB``, in a block named for the interface.

    Each interface laid its blob out its own way. CCP's is the address
    extension and then the address; the others seen put the address first.
    An interface not known here gives no address, rather than a guess at one.
    """
    for data in block.blocks("IF_DATA"):
        fields = data.fields()
        if not fields or "KP_BLOB" not in fields:
            continue
        blob = fields[fields.index("KP_BLOB") + 1 :]
        if fields[0] == "ASAP1B_CCP" and len(blob) >= 2:
            return _integer(blob[1])
        if fields[0] in ("ETK", "ASAP1B_ETK", "ASAP1B_KWP2000", "ASAP1B_ADDRESS") and blob:
            return _integer(blob[0])
    return None


def _memory_segments(module: _Block) -> list[Area]:
    """``MEMORY_SEGMENT name "text" PrgType MemoryType Attribute Address Size ...``

    A programming type that is never flashed is added to the memory type,
    where it is seen in the table and keeps the segment out of flash_areas.
    """
    found = []
    for block in _all(module, "MEMORY_SEGMENT"):
        words = block.fields()
        if len(words) < 7:
            continue
        address, size = _integer(words[5]), _integer(words[6])
        if address is None or size is None:
            continue
        kind, memory = str(words[2]), str(words[3])
        area = Area(str(words[0]), address, size, memory=memory, source="MEMORY_SEGMENT")
        if kind in NOT_FLASHED:
            area = replace(area, memory=f"{memory} ({kind})")
        found.append(area)
    return found


def _checksums(module: _Block) -> dict[str, ChecksumInfo]:
    """The first ``CHECKSUM`` block (XCP) and ``CHECKSUM_PARAM`` block (CCP)."""
    found: dict[str, ChecksumInfo] = {}
    for block in _all(module, "CHECKSUM"):
        words = block.fields()
        if not words:
            continue
        code = CHECKSUM_NAMES.get(str(words[0]), _integer(words[0]))
        if code is None:
            continue
        most = block.after("MAX_BLOCK_SIZE")
        found["XCP"] = ChecksumInfo(_known(code), code, _integer(most[0]) if most else None)
        break
    for block in _all(module, "CHECKSUM_PARAM"):
        words = block.fields()
        code = _integer(words[0]) if words else None
        if code is None:
            continue
        most = _integer(words[1]) if len(words) > 1 else None
        found["CCP"] = ChecksumInfo(_known(code), code, most)
        break
    return found


def _known(code: int) -> int | None:
    """A procedure number this can work out: one of XCP's types."""
    return code if code in CHECKSUM_NAMES.values() and code != 0xFF else None


def _sectors(module: _Block) -> list[Area]:
    """``SECTOR "name" number address length clear-seq program-seq method``,
    from an XCP IF_DATA's PGM block."""
    found = []
    for block in _all(module, "SECTOR"):
        words = block.fields()
        if len(words) < 6:
            continue
        numbers = [_integer(w) for w in words[2:6]]
        if any(n is None for n in numbers):
            continue
        address, size, clear, program = numbers
        found.append(Area(str(words[0]), address, size, clear, program, source="SECTOR"))
    return found


def _timeouts(module: _Block) -> dict[str, float]:
    """``PROTOCOL_LAYER version T1 T2 T3 T4 T5 T6 T7 ...``, in milliseconds.

    XCP on CAN's own, where it has one, since it is for this transport; then
    the first in the file.
    """
    layers = [b for c in _all(module, "XCP_ON_CAN") for b in _all(c, "PROTOCOL_LAYER")]
    for block in layers + _all(module, "PROTOCOL_LAYER"):
        times = [_integer(word) for word in block.fields()[1:8]]
        if len(times) == 7 and None not in times:
            return {f"T{n}": ms / 1000 for n, ms in enumerate(times, 1) if ms > 0}
    return {}


def _xcp_on_can(module: _Block) -> XcpOnCan | None:
    """``CAN_ID_MASTER`` and ``CAN_ID_SLAVE`` from an XCP_ON_CAN block, anywhere
    under the module's IF_DATA. Bit 31 of an identifier says it is 29-bit."""
    for block in _all(module, "XCP_ON_CAN"):
        master, slave = block.after("CAN_ID_MASTER"), block.after("CAN_ID_SLAVE")
        command = _integer(master[0]) if master else None
        response = _integer(slave[0]) if slave else None
        if command is None or response is None:
            continue
        rate = block.after("BAUDRATE")
        return XcpOnCan(
            command_id=command & 0x1FFFFFFF,
            response_id=response & 0x1FFFFFFF,
            extended=bool((command | response) & 0x80000000),
            bitrate=_integer(rate[0]) if rate else None,
            padded=block.has("MAX_DLC_REQUIRED"),
        )
    return None


# --- text to tree ----------------------------------------------------------------------------
def _read(path: Path) -> str:
    """A file's text. A2L is nearly always Latin-1 or UTF-8, and says which
    nowhere; UTF-8 is tried first because Latin-1 never fails."""
    data = path.read_bytes()
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def _tokens(
    text: str, folder: Path | None, depth: int = 0, progress: Progress | None = None
) -> list[str]:
    """The words and strings of a file, with the files it includes in place.

    How far it has got is how far through the file's own text: what it
    includes is read in between and not counted.
    """
    found: list[str] = []
    including = False
    for count, match in enumerate(_TOKEN.finditer(text)):
        if progress is not None and not count % WORDS_A_REPORT:
            progress(WORDS_SHARE * match.start() / len(text))
        token = match.group(0)
        if token.startswith(("/*", "//")):
            continue
        if token.startswith('"'):
            token = _Text(token[1:-1].replace('""', '"').replace('\\"', '"'))
        if including:
            including = False
            if folder is not None and depth < INCLUDE_DEPTH:
                try:
                    found += _tokens(_read(folder / str(token)), folder, depth + 1)
                except OSError:
                    pass  # an include that is not there leaves a gap, not a failure
            continue
        if token == "/include" and not isinstance(token, _Text):
            including = True
            continue
        found.append(token)
    return found


def _tree(tokens: list[str]) -> _Block:
    """Tokens into nested blocks. A block left open at the end of the file is
    closed there, and an ``/end`` with nothing open is ignored: a damaged file
    gives what it can rather than nothing."""
    root = _Block("")
    open_blocks = [root]
    at = 0
    while at < len(tokens):
        token = tokens[at]
        bare = not isinstance(token, _Text)
        if bare and token == "/begin" and at + 1 < len(tokens):
            block = _Block(str(tokens[at + 1]))
            open_blocks[-1].items.append(block)
            open_blocks.append(block)
            at += 2
        elif bare and token == "/end":
            if len(open_blocks) > 1:
                open_blocks.pop()
            at += 2
        else:
            open_blocks[-1].items.append(token)
            at += 1
    return root


def _all(block: _Block, kind: str) -> list[_Block]:
    """Every block of a kind, however deep."""
    found = []
    for item in block.items:
        if isinstance(item, _Block):
            if item.kind == kind:
                found.append(item)
            found += _all(item, kind)
    return found


def _plain(token: str) -> str:
    return str(token)


def _integer(token: str) -> int | None:
    try:
        return int(str(token), 0)
    except ValueError:
        return None


def _number(token: str) -> float | None:
    if isinstance(token, _Text):
        return None
    try:
        return float(int(token, 0)) if token.lower().startswith(("0x", "-0x")) else float(token)
    except ValueError:
        return None
