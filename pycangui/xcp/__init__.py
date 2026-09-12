# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""XCP on CAN (ASAM MCD-1 XCP), implemented directly on the shared bus.

Only the command/response layer needed for measurement and calibration is
implemented: CONNECT / DISCONNECT, status, SET_MTA + UPLOAD / SHORT_UPLOAD,
DOWNLOAD, and GET_SEED / UNLOCK (key from a hook).  Measurements are polled
with SHORT_UPLOAD into the signal hub; DAQ lists are a later addition.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

# Command PIDs (master -> slave)
CMD_CONNECT = 0xFF
CMD_DISCONNECT = 0xFE
CMD_GET_STATUS = 0xFD
CMD_SYNCH = 0xFC
CMD_GET_SEED = 0xF8
CMD_UNLOCK = 0xF7
CMD_SET_MTA = 0xF6
CMD_UPLOAD = 0xF5
CMD_SHORT_UPLOAD = 0xF4
CMD_DOWNLOAD = 0xF0

PID_RES = 0xFF  # positive response
PID_ERR = 0xFE

ERROR_CODES = {
    0x00: "ERR_CMD_SYNCH",
    0x10: "ERR_CMD_BUSY",
    0x11: "ERR_DAQ_ACTIVE",
    0x12: "ERR_PGM_ACTIVE",
    0x20: "ERR_CMD_UNKNOWN",
    0x21: "ERR_CMD_SYNTAX",
    0x22: "ERR_OUT_OF_RANGE",
    0x23: "ERR_WRITE_PROTECTED",
    0x24: "ERR_ACCESS_DENIED",
    0x25: "ERR_ACCESS_LOCKED",
    0x26: "ERR_PAGE_NOT_VALID",
    0x27: "ERR_MODE_NOT_VALID",
    0x28: "ERR_SEGMENT_NOT_VALID",
    0x29: "ERR_SEQUENCE",
    0x2A: "ERR_DAQ_CONFIG",
    0x30: "ERR_MEMORY_OVERFLOW",
    0x31: "ERR_GENERIC",
    0x32: "ERR_VERIFY",
}

RESOURCE_CAL = 0x01
RESOURCE_DAQ = 0x04
RESOURCE_STIM = 0x08
RESOURCE_PGM = 0x10


@dataclass(frozen=True)
class ConnectInfo:
    resources: int  # RESOURCE_* bits available
    protection: int = 0  # bits still locked (from GET_STATUS)
    big_endian: bool = False
    max_cto: int = 8
    max_dto: int = 8

    def resource_names(self, bits: int) -> str:
        names = []
        for bit, name in (
            (RESOURCE_CAL, "CAL"),
            (RESOURCE_DAQ, "DAQ"),
            (RESOURCE_STIM, "STIM"),
            (RESOURCE_PGM, "PGM"),
        ):
            if bits & bit:
                names.append(name)
        return "+".join(names) or "none"


# A2L-style data types -> (struct format char, size)
DATATYPES = {
    "UBYTE": ("B", 1),
    "SBYTE": ("b", 1),
    "UWORD": ("H", 2),
    "SWORD": ("h", 2),
    "ULONG": ("I", 4),
    "SLONG": ("i", 4),
    "A_UINT64": ("Q", 8),
    "A_INT64": ("q", 8),
    "FLOAT32_IEEE": ("f", 4),
    "FLOAT64_IEEE": ("d", 8),
}


def decode_value(data: bytes, datatype: str, big_endian: bool) -> float:
    fmt, size = DATATYPES[datatype]
    return struct.unpack((">" if big_endian else "<") + fmt, data[:size])[0]


def encode_value(value: float, datatype: str, big_endian: bool) -> bytes:
    fmt = DATATYPES[datatype][0]
    if fmt not in "fd":
        value = round(value)
    return struct.pack((">" if big_endian else "<") + fmt, value)
