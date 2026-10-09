# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""An A2L reader: what the XCP pane needs out of ASAM MCD-2 MC, and no more.

It lists a file's MEASUREMENTs and CHARACTERISTICs with where each one is, what
type it is, and how its raw value becomes a number somebody recognises. Single
values are read in full. Curves, maps, value blocks and text are listed, so
that the file is seen whole, and marked as not readable here.

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
from dataclasses import dataclass, field
from pathlib import Path

from pycangui.xcp import DATATYPES

#: Comments, quoted strings, and everything else up to the next space. A string
#: may hold a doubled or a backslashed quote.
_TOKEN = re.compile(
    r"""/\*.*?\*/ | //[^\n]*            # a comment, dropped
      | "(?:[^"\\]|\\.|"")*"           # a quoted string, kept whole
      | [^\s"]+                         # a word
    """,
    re.S | re.X,
)

#: A characteristic that is one value, which is what can be read and written.
VALUE = "VALUE"

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

    Linear ones are worked both ways, and so is the other rational function
    real files use: one over a straight line, which is how a period is
    stored for something shown as a frequency. A table of names
    (``choices``) leaves the number alone and says what it is called.
    Anything else -- a formula, a table to interpolate, a rational function
    with a square in it -- is ``exact = False``: the raw value is shown as
    it is, and said to be raw.
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
    #: ``(b, c, e, f)`` of ``raw = (b*phys + c) / (e*phys + f)``, for a rational
    #: function that is not a straight line; None for one that is.
    fraction: tuple[float, float, float, float] | None = None

    def to_phys(self, raw: float) -> float:
        if self.fraction is not None:
            b, c, e, f = self.fraction
            below = e * raw - b
            # The one raw value with no physical one: a period of zero.
            return (c - f * raw) / below if below else math.inf
        return raw * self.factor + self.offset

    def to_raw(self, phys: float) -> float:
        if self.fraction is not None:
            b, c, e, f = self.fraction
            below = e * phys + f
            return (b * phys + c) / below if below else 0.0
        return (phys - self.offset) / self.factor if self.factor else 0.0


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

    @property
    def readable(self) -> bool:
        return not self.unreadable

    @property
    def writable(self) -> bool:
        return self.kind == "CHARACTERISTIC" and self.readable and not self.read_only

    @property
    def unit(self) -> str:
        return self.conversion.unit if self.conversion else ""

    @property
    def choices(self) -> dict[int, str]:
        return self.conversion.choices if self.conversion else {}


@dataclass
class XcpOnCan:
    """The identifiers an A2L gives for XCP on CAN, where it gives them."""

    command_id: int
    response_id: int
    extended: bool = False
    bitrate: int | None = None


class A2l:
    def __init__(self) -> None:
        self.parameters: dict[str, Parameter] = {}
        self.conversions: dict[str, Conversion] = {}
        #: Record layout name -> the data type of the values it lays out.
        self.layouts: dict[str, str] = {}
        #: The file's own byte order, True for most significant first; None
        #: where it does not say, and the slave's own answer stands.
        self.big_endian: bool | None = None
        #: XCP on CAN identifiers from the file's IF_DATA, if it has them.
        self.xcp_on_can: XcpOnCan | None = None
        #: Where it was read from, so the pane can say which file these names
        #: came from. Empty for one parsed from text, as the tests do.
        self.path = ""

    @classmethod
    def parse(cls, text: str, folder: Path | None = None) -> A2l:
        a2l = cls()
        tree = _tree(_tokens(text, folder))
        modules = _all(tree, "MODULE") or [tree]  # a fragment with no MODULE round it
        for module in modules:
            a2l._read_module(module)
        return a2l

    @classmethod
    def load(cls, path: str) -> A2l:
        a2l = cls.parse(_read(Path(path)), Path(path).parent)
        a2l.path = str(path)
        return a2l

    def measurements(self) -> list[Parameter]:
        return [p for p in self.parameters.values() if p.kind == "MEASUREMENT"]

    def characteristics(self) -> list[Parameter]:
        return [p for p in self.parameters.values() if p.kind == "CHARACTERISTIC"]

    def unreadable(self) -> list[Parameter]:
        """The ones listed and not read: curves, maps, types not known."""
        return [p for p in self.parameters.values() if not p.readable]

    # --- one module ---------------------------------------------------------------------
    def _read_module(self, module: _Block) -> None:
        for common in module.blocks("MOD_COMMON"):
            if (order := common.after("BYTE_ORDER")) and order[0] in BYTE_ORDERS:
                self.big_endian = BYTE_ORDERS[order[0]]
        tables = {}
        for kind in ("COMPU_VTAB", "COMPU_VTAB_RANGE", "COMPU_TAB"):
            for block in module.blocks(kind):
                if (table := _table(block)) is not None:
                    tables[table[0]] = table[1]
        for block in module.blocks("COMPU_METHOD"):
            if (conversion := _conversion(block, tables)) is not None:
                self.conversions[conversion.name] = conversion
        for block in module.blocks("RECORD_LAYOUT"):
            fields = block.fields()
            if fields and (values := block.after("FNC_VALUES", 2)):
                self.layouts[fields[0]] = values[1]
        for block in module.blocks("MEASUREMENT"):
            if (parameter := self._measurement(block)) is not None:
                self.parameters[parameter.name] = parameter
        for block in module.blocks("CHARACTERISTIC"):
            if (parameter := self._characteristic(block)) is not None:
                self.parameters[parameter.name] = parameter
        if self.xcp_on_can is None:
            self.xcp_on_can = _xcp_on_can(module)

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
        elif _is_array(block, "MATRIX_DIM", "ARRAY_SIZE"):
            parameter.unreadable = "it is an array, and single values are what is read"
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
        if parameter.shape != VALUE:
            what = {
                "CURVE": "a curve",
                "MAP": "a map",
                "CUBOID": "a three-dimensional map",
                "VAL_BLK": "a block of values",
                "ASCII": "text",
            }.get(parameter.shape, parameter.shape)
            parameter.unreadable = f"it is {what}, and single values are what is read"
        elif not parameter.datatype:
            parameter.unreadable = f"its record layout, {f[4]}, is not in the file"
        elif _is_array(block, "MATRIX_DIM", "NUMBER"):
            parameter.unreadable = "it is an array, and single values are what is read"
        self._common(block, parameter)
        return parameter

    def _common(self, block: _Block, parameter: Parameter) -> None:
        """The optional keywords a measurement and a characteristic share."""
        if mask := block.after("BIT_MASK"):
            parameter.bit_mask = _integer(mask[0])
        if (order := block.after("BYTE_ORDER")) and order[0] in BYTE_ORDERS:
            parameter.big_endian = BYTE_ORDERS[order[0]]
        if parameter.readable and parameter.datatype not in DATATYPES:
            parameter.unreadable = f"its data type, {parameter.datatype}, is not one pycangui reads"
        if extension := block.after("ECU_ADDRESS_EXTENSION"):
            parameter.extension = _integer(extension[0]) or 0
        if parameter.conversion is not None and not parameter.conversion.exact:
            # Read, and shown raw: better a true number than a wrong one.
            parameter.conversion = Conversion(
                parameter.conversion.name, unit="raw", exact=False, kind=parameter.conversion.kind
            )


# --- the blocks this reader knows ------------------------------------------------------------
def _conversion(block: _Block, tables: dict[str, dict[int, str] | None]) -> Conversion | None:
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
    conversion = Conversion(name=f[0], unit=_plain(f[4]), kind=f[2])
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
    if (reference := block.after("COMPU_TAB_REF")) and tables.get(reference[0]):
        conversion.choices = tables[reference[0]]
        return conversion
    conversion.exact = False
    return conversion


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


def _is_array(block: _Block, *keywords: str) -> bool:
    """Whether a block says it is more than one value.

    ``ARRAY_SIZE 1`` and ``MATRIX_DIM 1 1 1`` are written by tools that say
    it for everything, and are one value all the same.
    """
    for keyword in keywords:
        fields = block.fields()
        for at, item in enumerate(fields):
            if item != keyword or isinstance(item, _Text):
                continue
            count = 1
            for size in fields[at + 1 : at + 4]:  # up to three dimensions
                if isinstance(size, _Text) or not str(size).isdigit():
                    break
                count *= int(size)
            if count != 1:
                return True
    return False


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


def _tokens(text: str, folder: Path | None, depth: int = 0) -> list[str]:
    """The words and strings of a file, with the files it includes in place."""
    found: list[str] = []
    including = False
    for match in _TOKEN.finditer(text):
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
