# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""CANopen emergency (EMCY) decoding.

An emergency object carries eight bytes: a 16-bit error code, the error
register (object 0x1001), and five **manufacturer-specific** bytes.  The code
and register are standardised by CiA 301 and decoded here; the five remaining
bytes mean whatever the device maker decided, so they are handed to the
``canopen.emcy_manufacturer`` hook for the user to decode.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

# CiA 301 error codes.  Specific codes first, then the category of the high
# nibble / byte for anything not listed.
EMCY_CODES: dict[int, str] = {
    0x0000: "Error reset or no error",
    0x1000: "Generic error",
    0x2000: "Current, generic",
    0x2100: "Current, device input side",
    0x2110: "Short circuit, device input",
    0x2200: "Current inside the device",
    0x2300: "Current, device output side",
    0x2310: "Continuous over current",
    0x2320: "Short circuit or earth leakage, output",
    0x3000: "Voltage, generic",
    0x3100: "Mains voltage",
    0x3110: "Mains over voltage",
    0x3120: "Mains under voltage",
    0x3130: "Phase failure",
    0x3200: "Voltage inside the device",
    0x3210: "DC link over voltage",
    0x3220: "DC link under voltage",
    0x3300: "Output voltage",
    0x3310: "Output over voltage",
    0x4000: "Temperature, generic",
    0x4100: "Ambient temperature",
    0x4110: "Excess ambient temperature",
    0x4120: "Low ambient temperature",
    0x4200: "Device temperature",
    0x4210: "Excess device temperature",
    0x4220: "Low device temperature",
    0x5000: "Device hardware",
    0x5100: "Supply, internal",
    0x5200: "Control, internal",
    0x5300: "Operating unit",
    0x5400: "Power section",
    0x5530: "EEPROM or flash fault",
    0x6000: "Device software",
    0x6010: "Internal software reset or watchdog",
    0x6060: "Application software",
    0x6080: "Cycle time exceeded",
    0x6100: "Internal software",
    0x6200: "User software",
    0x6300: "Data set",
    0x6301: "Data record error",
    0x6310: "Loss of parameters",
    0x6320: "Parameter error",
    0x7000: "Additional modules",
    0x7100: "Power",
    0x7200: "Measurement circuit",
    0x7300: "Sensor",
    0x7400: "Computation circuit",
    0x7500: "Communication",
    0x7600: "Data storage, external",
    0x8000: "Monitoring",
    0x8100: "Communication, generic",
    0x8110: "CAN overrun (objects lost)",
    0x8120: "CAN in error passive mode",
    0x8130: "Life guard or heartbeat error",
    0x8140: "Recovered from bus off",
    0x8150: "CAN-ID collision",
    0x8200: "Protocol error",
    0x8210: "PDO not processed due to length error",
    0x8220: "PDO length exceeded",
    0x8230: "DAM MPDO not processed, destination object not available",
    0x8240: "Unexpected SYNC data length",
    0x8250: "RPDO timeout",
    0x8300: "Torque control",
    0x8400: "Velocity speed controller",
    0x8500: "Position controller",
    0x8600: "Positioning controller",
    0x8700: "Sync controller",
    0x8800: "Winding controller",
    0x8900: "Process data monitoring",
    0x9000: "External error",
    0xF000: "Additional functions",
    0xFF00: "Device specific",
}

# Broader categories, tried when the exact code is not listed.
_CATEGORIES: tuple[tuple[int, int, str], ...] = (
    (0x0000, 0xFF00, "Error reset or no error"),
    (0x1000, 0xFF00, "Generic error"),
    (0x2000, 0xF000, "Current"),
    (0x3000, 0xF000, "Voltage"),
    (0x4000, 0xF000, "Temperature"),
    (0x5000, 0xFF00, "Device hardware"),
    (0x6000, 0xF000, "Device software"),
    (0x7000, 0xFF00, "Additional modules"),
    (0x8000, 0xF000, "Monitoring"),
    (0x9000, 0xFF00, "External error"),
    (0xF000, 0xFF00, "Additional functions"),
    (0xFF00, 0xFF00, "Device specific"),
)

#: Object 0x1001, the error register.
ERROR_REGISTER_BITS: tuple[tuple[int, str], ...] = (
    (0x01, "generic"),
    (0x02, "current"),
    (0x04, "voltage"),
    (0x08, "temperature"),
    (0x10, "communication"),
    (0x20, "device profile"),
    (0x40, "reserved"),
    (0x80, "manufacturer"),
)


def describe_code(code: int) -> str:
    """Text for an emergency error code, exact if known, else its category."""
    if (text := EMCY_CODES.get(code)) is not None:
        return text
    if (text := EMCY_CODES.get(code & 0xFF00)) is not None:
        return text
    for base, mask, text in _CATEGORIES:
        if code & mask == base:
            return text
    return "Unknown"


def describe_register(register: int) -> str:
    """The error register as a list of the bits that are set."""
    if register == 0:
        return "no error"
    names = [name for bit, name in ERROR_REGISTER_BITS if register & bit]
    return ", ".join(names) if names else "unknown"


@dataclass(frozen=True)
class Emcy:
    """One emergency object, as received."""

    node_id: int
    code: int
    register: int
    data: bytes = b""  # the five manufacturer-specific bytes
    timestamp: float = 0.0
    manufacturer_text: str = field(default="", compare=False)

    @property
    def is_reset(self) -> bool:
        return self.code == 0x0000

    @property
    def description(self) -> str:
        return describe_code(self.code)

    @property
    def register_text(self) -> str:
        return describe_register(self.register)

    @property
    def data_hex(self) -> str:
        return self.data.hex(" ").upper()

    def as_int(self, offset: int = 0, size: int = 2, signed: bool = False) -> int | None:
        """Helper for hooks: read a number out of the manufacturer bytes."""
        if offset + size > len(self.data):
            return None
        return int.from_bytes(self.data[offset : offset + size], "little", signed=signed)

    def __str__(self) -> str:
        text = f"node {self.node_id}: 0x{self.code:04X} {self.description}"
        text += f" [register 0x{self.register:02X}: {self.register_text}]"
        if self.data:
            text += f" data {self.data_hex}"
        if self.manufacturer_text:
            text += f" -- {self.manufacturer_text}"
        return text


def encode(code: int, register: int, data: bytes = b"") -> bytes:
    """Build the eight bytes of an emergency object (used by the demo device)."""
    return struct.pack("<HB", code, register) + bytes(data).ljust(5, b"\x00")[:5]
