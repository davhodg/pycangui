# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""The checksums BUILD_CHECKSUM can answer with, worked out here as well.

An XCP slave says which of these it used, by number, so what it reports can
be checked against the same sum over the file that was sent. CCP says only
how many bytes its checksum has: the algorithm is the ECU's own, and has to
be picked here to be compared at all.

The numbers and the algorithms are ASAM XCP's. The additions read their
words in the slave's byte order, and the CRCs are the common ones they name:
CRC-16 is CRC-16/ARC, CRC-16 CITT is CRC-16/CCITT-FALSE, CRC-32 is zlib's.
"""

from __future__ import annotations

import zlib

ADD_11 = 0x01
ADD_12 = 0x02
ADD_14 = 0x03
ADD_22 = 0x04
ADD_24 = 0x05
ADD_44 = 0x06
CRC_16 = 0x07
CRC_16_CITT = 0x08
CRC_32 = 0x09
USER_DEFINED = 0xFF

NAMES = {
    ADD_11: "ADD_11",
    ADD_12: "ADD_12",
    ADD_14: "ADD_14",
    ADD_22: "ADD_22",
    ADD_24: "ADD_24",
    ADD_44: "ADD_44",
    CRC_16: "CRC_16",
    CRC_16_CITT: "CRC_16_CITT",
    CRC_32: "CRC_32",
    USER_DEFINED: "user defined",
}

#: Each addition: how wide the words added are, and how wide the sum is kept.
_ADDITIONS = {
    ADD_11: (1, 1),
    ADD_12: (1, 2),
    ADD_14: (1, 4),
    ADD_22: (2, 2),
    ADD_24: (2, 4),
    ADD_44: (4, 4),
}


def name(kind: int | None) -> str:
    if kind is None:
        return "not known"
    return NAMES.get(kind, f"0x{kind:02X}")


def word_size(kind: int) -> int:
    """How many bytes a block summed this way has to be a multiple of."""
    return _ADDITIONS.get(kind, (1, 1))[0]


def compute(kind: int, data: bytes, big_endian: bool = False) -> int | None:
    """The checksum of ``data`` as the slave would give it, or None for one
    that cannot be worked out here: user defined, or not a known number."""
    if kind in _ADDITIONS:
        word, width = _ADDITIONS[kind]
        order = "big" if big_endian else "little"
        total = 0
        for at in range(0, len(data) - len(data) % word, word):
            total += int.from_bytes(data[at : at + word], order)
        return total & ((1 << (8 * width)) - 1)
    if kind == CRC_16:
        return _crc16_arc(data)
    if kind == CRC_16_CITT:
        return _crc16_ccitt_false(data)
    if kind == CRC_32:
        return zlib.crc32(data) & 0xFFFFFFFF
    return None


def _crc16_arc(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def _crc16_ccitt_false(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else crc << 1
            crc &= 0xFFFF
    return crc
