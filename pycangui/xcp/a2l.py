# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""A minimal A2L reader: enough of ASAM MCD-2 MC to list MEASUREMENTs and
CHARACTERISTICs with their address, data type and (optional) conversion, which
is what the XCP pane needs to read and write values.  Full A2L (RECORD_LAYOUT,
AXIS, compu-methods beyond linear, ...) is out of scope.

A2L is a keyword-and-brace text format:

    /begin MEASUREMENT EngineSpeed "Engine speed"
        UWORD Speed_conv 0 0 0 8000
        ECU_ADDRESS 0x1230
    /end MEASUREMENT
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_COMMENT = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)
_BLOCK = re.compile(r"/begin\s+(MEASUREMENT|CHARACTERISTIC)\s+(.*?)/end\s+\1", re.S)
_ADDRESS = re.compile(r"ECU_ADDRESS\s+(0x[0-9A-Fa-f]+|\d+)")
# COMPU_METHOD <name> "<description>" <type> "<format>" "<unit>" <coefficients...>
_COMPU = re.compile(
    r'COMPU_METHOD\s+(\w+)\s+"[^"]*"\s+\w+\s+"[^"]*"\s+"([^"]*)"(.*?)/end\s+COMPU_METHOD',
    re.S,
)
_COEFFS = re.compile(r"COEFFS_LINEAR\s+([-\d.eE+]+)\s+([-\d.eE+]+)")
_COEFFS_RAT = re.compile(r"COEFFS\s+([-\d.eE+ ]+)")

DATATYPES = (
    "FLOAT64_IEEE",
    "FLOAT32_IEEE",
    "A_UINT64",
    "A_INT64",
    "UBYTE",
    "SBYTE",
    "UWORD",
    "SWORD",
    "ULONG",
    "SLONG",
)


@dataclass
class Conversion:
    name: str
    factor: float = 1.0
    offset: float = 0.0
    unit: str = ""

    def to_phys(self, raw: float) -> float:
        return raw * self.factor + self.offset

    def to_raw(self, phys: float) -> float:
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

    @property
    def writable(self) -> bool:
        return self.kind == "CHARACTERISTIC"

    @property
    def unit(self) -> str:
        return self.conversion.unit if self.conversion else ""


class A2l:
    def __init__(self) -> None:
        self.parameters: dict[str, Parameter] = {}
        self.conversions: dict[str, Conversion] = {}

    @classmethod
    def parse(cls, text: str) -> A2l:
        a2l = cls()
        text = _COMMENT.sub(" ", text)
        for name, unit, body in _COMPU.findall(text):
            factor, offset = 1.0, 0.0
            if m := _coeffs_linear_search(body):
                factor, offset = m
            a2l.conversions[name] = Conversion(name, factor, offset, unit)
        for kind, body in ((m.group(1), m.group(2)) for m in _BLOCK.finditer(text)):
            param = _parse_block(kind, body, a2l.conversions)
            if param is not None:
                a2l.parameters[param.name] = param
        return a2l

    @classmethod
    def load(cls, path: str) -> A2l:
        with open(path, encoding="utf-8", errors="replace") as f:
            return cls.parse(f.read())

    def measurements(self) -> list[Parameter]:
        return [p for p in self.parameters.values() if p.kind == "MEASUREMENT"]

    def characteristics(self) -> list[Parameter]:
        return [p for p in self.parameters.values() if p.kind == "CHARACTERISTIC"]


def _parse_block(kind: str, body: str, conversions: dict[str, Conversion]) -> Parameter | None:
    tokens = body.split()
    if len(tokens) < 2:
        return None
    name = tokens[0]
    description = ""
    rest = body
    if '"' in body:  # description is the first quoted string
        start = body.index('"')
        end = body.index('"', start + 1)
        description = body[start + 1 : end]
        rest = body[end + 1 :]
    words = rest.split()
    datatype = next((w for w in words if w in DATATYPES), None)
    if datatype is None:
        return None
    addr_match = _ADDRESS.search(body)
    if addr_match is None:
        # CHARACTERISTIC/MEASUREMENT positional address is the token after the datatype
        # (kind name datatype address ...) -- fall back to that if present
        try:
            idx = words.index(datatype)
            address = int(words[idx + 1], 0)
        except (ValueError, IndexError):
            return None
    else:
        address = int(addr_match.group(1), 0)
    conv_name = next((w for w in words if w in conversions), None)
    conversion = conversions.get(conv_name) if conv_name else None
    lower = upper = None
    nums = [w for w in words if _is_number(w)]
    if len(nums) >= 2:
        lower, upper = float(nums[-2]), float(nums[-1])
    return Parameter(name, kind, datatype, address, description, conversion, lower, upper)


def _coeffs_linear_search(body: str):
    m = _COEFFS.search(body)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = _COEFFS_RAT.search(body)
    if m:
        # ASAM RAT_FUNC COEFFS a b c d e f defines raw as a function of phys:
        #   raw = (a*phys^2 + b*phys + c) / (d*phys^2 + e*phys + f)
        # For the linear case (a=d=e=0): raw = (b*phys + c) / f, so
        #   phys = (f/b)*raw - c/b  ->  factor = f/b, offset = -c/b
        vals = [float(x) for x in m.group(1).split()]
        if len(vals) == 6 and vals[0] == 0 and vals[3] == 0 and vals[4] == 0 and vals[1] != 0:
            return vals[5] / vals[1], -vals[2] / vals[1]
    return None


def _is_number(token: str) -> bool:
    try:
        float(token)
        return True
    except ValueError:
        return False
