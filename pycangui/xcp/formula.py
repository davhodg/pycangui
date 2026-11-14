# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""A FORM conversion's formula, worked out without running anything from the file.

ASAM writes formulas much as C does -- ``X1*0.1-40``, ``sqrt(X1)``,
``(X1 >> 4) & 0x0F`` -- with ``X1`` (or ``X``) for the raw value. The text is
read with Python's own parser, after the few C spellings Python does not
share are changed, and then only arithmetic, comparisons, bit operations and
the functions listed here are allowed. Anything else is refused when the file
is read, not when a value is: a name that is not a function, an attribute,
a subscript. So an A2L, which comes from anywhere, never gets to run code.
"""

from __future__ import annotations

import ast
import math
import operator
import re
from collections.abc import Callable

FUNCTIONS: dict[str, Callable[..., float]] = {
    "abs": abs,
    "acos": math.acos,
    "asin": math.asin,
    "atan": math.atan,
    "cos": math.cos,
    "cosh": math.cosh,
    "exp": math.exp,
    "log": math.log,  # ASAM's log is the natural one
    "log10": math.log10,
    "pow": math.pow,
    "sin": math.sin,
    "sinh": math.sinh,
    "sqrt": math.sqrt,
    "tan": math.tan,
    "tanh": math.tanh,
}

#: The raw value, by the names ASAM gives it.
VARIABLES = ("X1", "X", "x1", "x")

_BINARY = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_BITS = {
    ast.BitAnd: operator.and_,
    ast.BitOr: operator.or_,
    ast.BitXor: operator.xor,
    ast.LShift: operator.lshift,
    ast.RShift: operator.rshift,
}
_COMPARE = {
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
}


class FormulaError(ValueError):
    """A formula that cannot be worked out here, and why."""


def compile_formula(text: str) -> Callable[[float], float]:
    """The formula as a function of the raw value. Raises FormulaError."""
    python = re.sub(r"&&", " and ", text)
    python = re.sub(r"\|\|", " or ", python)
    python = re.sub(r"!(?!=)", " not ", python)
    try:
        tree = ast.parse(python.strip(), mode="eval")
    except SyntaxError as exc:
        raise FormulaError(f"{text!r} is not a formula pycangui can read") from exc
    _check(tree.body, text)

    def worked_out(raw: float) -> float:
        return float(_value(tree.body, raw))

    return worked_out


def _check(node: ast.AST, text: str) -> None:
    """Refuse anything but what is allowed, before it is ever worked out."""
    allowed = (
        ast.BinOp,
        ast.UnaryOp,
        ast.BoolOp,
        ast.Compare,
        ast.IfExp,
        ast.Call,
        ast.Name,
        ast.Constant,
        ast.operator,
        ast.unaryop,
        ast.boolop,
        ast.cmpop,
        ast.Load,
    )
    for part in ast.walk(node):
        if not isinstance(part, allowed):
            raise FormulaError(f"{text!r} has {type(part).__name__} in it")
        if isinstance(part, ast.Name) and part.id not in VARIABLES and part.id not in FUNCTIONS:
            raise FormulaError(f"{text!r} names {part.id}, which is not the raw value")
        if isinstance(part, ast.Call) and not (
            isinstance(part.func, ast.Name) and part.func.id in FUNCTIONS and not part.keywords
        ):
            raise FormulaError(f"{text!r} calls something that is not a known function")
        if isinstance(part, ast.Constant) and not isinstance(part.value, int | float):
            raise FormulaError(f"{text!r} has {part.value!r} in it, which is not a number")


def _value(node: ast.AST, raw: float):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return raw
    if isinstance(node, ast.BinOp):
        left, right = _value(node.left, raw), _value(node.right, raw)
        if type(node.op) in _BITS:
            return _BITS[type(node.op)](int(left), int(right))
        return _BINARY[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp):
        operand = _value(node.operand, raw)
        if isinstance(node.op, ast.USub):
            return -operand
        if isinstance(node.op, ast.UAdd):
            return operand
        if isinstance(node.op, ast.Invert):
            return ~int(operand)
        return int(not operand)
    if isinstance(node, ast.BoolOp):
        values = [_value(v, raw) for v in node.values]
        return int(all(values) if isinstance(node.op, ast.And) else any(values))
    if isinstance(node, ast.Compare):
        left = _value(node.left, raw)
        for op, right_node in zip(node.ops, node.comparators, strict=True):
            right = _value(right_node, raw)
            if not _COMPARE[type(op)](left, right):
                return 0
            left = right
        return 1
    if isinstance(node, ast.IfExp):
        return _value(node.body if _value(node.test, raw) else node.orelse, raw)
    if isinstance(node, ast.Call):
        return FUNCTIONS[node.func.id](*(_value(a, raw) for a in node.args))
    raise FormulaError(f"cannot work out {type(node).__name__}")
