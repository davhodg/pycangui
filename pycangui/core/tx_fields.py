"""Fields a transmitted message computes for itself: counters and checksums.

A message that carries a rolling counter and a checksum over its own bytes is
completely ordinary -- most safety-relevant messages have both -- and a tool
that sends the same eight bytes for ever cannot talk to any receiver that
checks either one.  Every frame is rejected, and the reason is invisible from
the sending end.

This module is the arithmetic, with no Qt in it: where the fields sit, what
goes in them, and in what order.  The transmit pane owns the timer and the
dialog; the awkward parts are all here, where they can be tested.

**Order is not optional.** The counter is written first and the checksum is
computed afterwards, over bytes that already include it.  A checksum computed
first is stale on every frame -- and that is a bug which looks like a working
feature from the sending end, right up until somebody reads the receiving end.

Positions are byte- and nibble-addressed rather than arbitrary bit ranges.  A
four-bit counter sharing a byte is at least as common as a whole one, so
nibbles have to be expressible; arbitrary bit ranges would also need a bit
numbering convention, and CAN has two of those that disagree.  Byte and nibble
dodge the argument and cover what people actually build.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

#: Which half of a byte, or all of it.
WHOLE, LOW, HIGH = "all", "low", "high"
PARTS = (WHOLE, LOW, HIGH)

BIG, LITTLE = "big", "little"


class FieldError(ValueError):
    """A counter or checksum that cannot be applied.  The message says why."""


@dataclass(frozen=True)
class Placement:
    """Where a computed value goes in the payload.

    ``byte`` with ``part`` LOW or HIGH is a nibble; ``part`` WHOLE with
    ``width`` 1 or 2 is one or two whole bytes, ``endian`` deciding the order
    of the pair.
    """

    byte: int
    part: str = WHOLE
    width: int = 1
    endian: str = BIG

    def __post_init__(self) -> None:
        if self.part not in PARTS:
            raise FieldError(f"{self.part!r} is not one of {', '.join(PARTS)}")
        if self.part != WHOLE and self.width != 1:
            raise FieldError("a nibble is half of one byte, so its width is one byte")
        if self.width not in (1, 2):
            raise FieldError("a field is one or two bytes wide")
        if self.byte < 0:
            raise FieldError("byte positions start at zero")

    @property
    def bits(self) -> int:
        return 4 if self.part != WHOLE else 8 * self.width

    @property
    def modulus(self) -> int:
        """One past the largest value that fits."""
        return 1 << self.bits

    def covers(self) -> range:
        """The byte positions this field occupies."""
        return range(self.byte, self.byte + self.width)

    def check(self, length: int) -> None:
        if self.byte + self.width > length:
            raise FieldError(
                f"byte {self.byte + self.width - 1} is past the end of a {length}-byte message"
            )

    def read(self, data: bytes) -> int:
        self.check(len(data))
        if self.part == LOW:
            return data[self.byte] & 0x0F
        if self.part == HIGH:
            return data[self.byte] >> 4
        return int.from_bytes(data[self.byte : self.byte + self.width], self.endian)

    def write(self, data: bytearray, value: int) -> None:
        """Put ``value`` in, keeping whatever shares the byte with it.

        Masked rather than refused: a counter told to count past its own
        field is a configuration somebody will make, and wrapping is what
        the field itself does anyway.
        """
        self.check(len(data))
        value &= self.modulus - 1
        if self.part == LOW:
            data[self.byte] = (data[self.byte] & 0xF0) | value
        elif self.part == HIGH:
            data[self.byte] = (data[self.byte] & 0x0F) | (value << 4)
        else:
            data[self.byte : self.byte + self.width] = value.to_bytes(self.width, self.endian)


# --- the algorithms --------------------------------------------------------------------
def _xor(data: bytes) -> int:
    out = 0
    for byte in data:
        out ^= byte
    return out


def _sum8(data: bytes) -> int:
    return sum(data) & 0xFF


def _sum8_twos(data: bytes) -> int:
    """Sum negated, so the bytes plus the checksum come to zero.

    A receiver checking one of these adds everything up and expects nought,
    which is why it is a different algorithm rather than a different check.
    """
    return (-sum(data)) & 0xFF


def _crc(data: bytes, poly: int, init: int, xorout: int, width: int) -> int:
    top = 1 << (width - 1)
    mask = (1 << width) - 1
    crc = init
    for byte in data:
        crc ^= byte << (width - 8)
        for _ in range(8):
            crc = ((crc << 1) ^ poly) if crc & top else (crc << 1)
            crc &= mask
    return crc ^ xorout


def _crc8_j1850(data: bytes) -> int:
    """SAE J1850, and AUTOSAR E2E profile 1's CRC."""
    return _crc(data, poly=0x1D, init=0xFF, xorout=0xFF, width=8)


def _crc8_2f(data: bytes) -> int:
    """The 0x2F polynomial, as AUTOSAR calls CRC8H2F."""
    return _crc(data, poly=0x2F, init=0xFF, xorout=0xFF, width=8)


def _crc16_ccitt(data: bytes) -> int:
    return _crc(data, poly=0x1021, init=0xFFFF, xorout=0x0000, width=16)


#: Name -> (function, how many bytes it produces).  The name is what is saved
#: in settings.json, so these strings are a format and do not change lightly.
ALGORITHMS: dict[str, tuple[Callable[[bytes], int], int]] = {
    "xor": (_xor, 1),
    "sum8": (_sum8, 1),
    "sum8_twos": (_sum8_twos, 1),
    "crc8_j1850": (_crc8_j1850, 1),
    "crc8_2f": (_crc8_2f, 1),
    "crc16_ccitt": (_crc16_ccitt, 2),
}

#: What to call them on screen.
ALGORITHM_NAMES: dict[str, str] = {
    "xor": "XOR of bytes",
    "sum8": "Sum, 8-bit",
    "sum8_twos": "Sum, two's complement (bytes total zero)",
    "crc8_j1850": "CRC-8 / SAE J1850 (AUTOSAR E2E profile 1)",
    "crc8_2f": "CRC-8 / 0x2F (AUTOSAR CRC8H2F)",
    "crc16_ccitt": "CRC-16 / CCITT",
}


# --- the two fields --------------------------------------------------------------------
@dataclass(frozen=True)
class Counter:
    """A value that moves on every frame sent."""

    at: Placement
    start: int = 0
    step: int = 1
    #: Wrap at this many counts.  Zero means "whatever the field holds", which
    #: is what almost everybody wants and saves saying 16 for a nibble.
    wrap: int = 0

    @property
    def modulus(self) -> int:
        """What the count wraps at: the configured wrap, or the field's own."""
        return self.wrap if self.wrap > 0 else self.at.modulus

    def value(self, sent: int) -> int:
        """The value for the ``sent``-th frame, counting from zero."""
        return (self.start + self.step * sent) % self.modulus


@dataclass(frozen=True)
class Checksum:
    """A value computed over the payload, after the counter is in it."""

    at: Placement
    algorithm: str = "xor"
    #: Which bytes to compute over, inclusive.  ``None`` means the whole
    #: message *except* the checksum's own bytes -- the usual rule, and the
    #: one that is easy to get wrong by including them and hashing a field
    #: that is about to change.
    first: int | None = None
    last: int | None = None

    def function(self) -> Callable[[bytes], int]:
        if self.algorithm not in ALGORITHMS:
            raise FieldError(f"no checksum called {self.algorithm!r}")
        return ALGORITHMS[self.algorithm][0]

    def over(self, length: int) -> list[int]:
        """The byte positions this checksum is computed from."""
        if self.first is None and self.last is None:
            mine = set(self.at.covers())
            return [i for i in range(length) if i not in mine]
        first = 0 if self.first is None else self.first
        last = length - 1 if self.last is None else self.last
        if first > last:
            raise FieldError(f"byte {first} is after byte {last}")
        if last >= length:
            raise FieldError(f"byte {last} is past the end of a {length}-byte message")
        return list(range(first, last + 1))

    def value(self, data: bytes) -> int:
        return self.function()(bytes(data[i] for i in self.over(len(data))))


def width_of(algorithm: str) -> int:
    """How many bytes an algorithm produces, for offering a sensible field."""
    if algorithm not in ALGORITHMS:
        raise FieldError(f"no checksum called {algorithm!r}")
    return ALGORITHMS[algorithm][1]


def apply(
    data: bytes,
    counter: Counter | None = None,
    checksum: Checksum | None = None,
    sent: int = 0,
    computed: int | None = None,
) -> bytes:
    """The payload as it should go on the wire for frame number ``sent``.

    ``computed`` is a checksum somebody else worked out -- a hook, for a
    maker's own arithmetic -- and is used in place of the named algorithm
    while still being written where the configuration says.

    The counter goes in first.  See the module docstring: a checksum computed
    before the counter is stale on every frame.
    """
    out = bytearray(data)
    if counter is not None:
        counter.at.write(out, counter.value(sent))
    if checksum is not None:
        value = checksum.value(out) if computed is None else computed
        checksum.at.write(out, value)
    return bytes(out)


# --- saving and loading ----------------------------------------------------------------
def placement_to_dict(at: Placement) -> dict:
    return {"byte": at.byte, "part": at.part, "width": at.width, "endian": at.endian}


def placement_from_dict(raw: dict) -> Placement:
    return Placement(
        byte=int(raw.get("byte", 0)),
        part=str(raw.get("part", WHOLE)),
        width=int(raw.get("width", 1)),
        endian=str(raw.get("endian", BIG)),
    )


def counter_from_dict(raw: dict | None) -> Counter | None:
    if not raw:
        return None
    return Counter(
        at=placement_from_dict(raw.get("at", {})),
        start=int(raw.get("start", 0)),
        step=int(raw.get("step", 1)),
        wrap=int(raw.get("wrap", 0)),
    )


def counter_to_dict(counter: Counter | None) -> dict | None:
    if counter is None:
        return None
    return {
        "at": placement_to_dict(counter.at),
        "start": counter.start,
        "step": counter.step,
        "wrap": counter.wrap,
    }


def checksum_from_dict(raw: dict | None) -> Checksum | None:
    if not raw:
        return None
    first, last = raw.get("first"), raw.get("last")
    return Checksum(
        at=placement_from_dict(raw.get("at", {})),
        algorithm=str(raw.get("algorithm", "xor")),
        first=None if first is None else int(first),
        last=None if last is None else int(last),
    )


def checksum_to_dict(checksum: Checksum | None) -> dict | None:
    if checksum is None:
        return None
    return {
        "at": placement_to_dict(checksum.at),
        "algorithm": checksum.algorithm,
        "first": checksum.first,
        "last": checksum.last,
    }


def describe(counter: Counter | None, checksum: Checksum | None) -> str:
    """A short phrase for the transmit list, so a row says what it is doing."""
    parts = []
    if counter is not None:
        parts.append(f"count {_where(counter.at)}")
    if checksum is not None:
        parts.append(f"{checksum.algorithm} {_where(checksum.at)}")
    return ", ".join(parts)


def _where(at: Placement) -> str:
    if at.part == LOW:
        return f"b{at.byte}.lo"
    if at.part == HIGH:
        return f"b{at.byte}.hi"
    if at.width == 2:
        return f"b{at.byte}-{at.byte + 1}"
    return f"b{at.byte}"
