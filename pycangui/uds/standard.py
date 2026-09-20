# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""The parts of UDS that are the same on every ECU.

Everything here comes from udsoncan, which already carries the ISO 14229-1
tables, rather than being typed out again: the data identifier names and the
ranges around them, and the negative response codes.

What is *not* here, deliberately:

* **DTC descriptions.**  ISO 14229-1 does not define any. The text for the
  standard powertrain/chassis/body/network codes is SAE J2012, which is a
  copyrighted document of some thousands of entries and cannot be shipped in
  an Apache-2.0 project. Everything above those ranges is manufacturer
  specific in any case. The code itself -- "P0217" -- is arithmetic, and is
  produced by :func:`pycangui.uds.manager.dtc_code`.
* **The failure type byte.**  The low byte of a three-byte DTC (SAE J2012-DA
  "FTB": circuit short to ground, signal stuck high, ...) is from the same
  copyrighted document.

Both are what ``hooks/uds.py`` is for: a manufacturer's own list, or one taken
from an ODX or a DBC, is the right source and only its owner has it.
"""

from __future__ import annotations

import re

from udsoncan import DataIdentifier, Routine

#: Names that are worth shortening. udsoncan spells the ISO identifiers out in
#: full, which is right for a library and long for a table cell.
SHORTER = {
    "VINDataIdentifier": "VIN",
    "ActiveDiagnosticSessionDataIdentifier": "Active diagnostic session",
    "IdentificationOptionVehicleManufacturerSpecific": "Manufacturer identification",
    "IdentificationOptionSystemSupplierSpecific": "Supplier identification",
    "VehicleManufacturerSpecific": "Manufacturer specific",
    "SystemSupplierSpecific": "Supplier specific",
    "ISOSAEReserved": "ISO/SAE reserved",
    "ReservedForLegislativeUse": "Reserved for legislative use",
    # Routine identifiers (Annex F). Four are named individually; the rest of
    # the space is ranges.
    "EraseMemory": "Erase memory",
    "CheckProgrammingDependencies": "Check programming dependencies",
    "EraseMirrorMemoryDTCs": "Erase mirror memory DTCs",
    "DeployLoopRoutineID": "Deploy loop",
    "TachographTestIds": "Tachograph test",
    "OBDTestIds": "OBD test",
    "SafetySystemRoutineIDs": "Safety system",
}


#: The sessions ISO 14229-1 names. 0x40 to 0x5F are the manufacturer's own
#: and 0x60 to 0x7E the supplier's, which is what hooks/uds.py::sessions is
#: for -- only the people who built the ECU know what those are called.
SESSIONS = {1: "default", 2: "programming", 3: "extended", 4: "safety system"}

#: How many security levels to offer. The manufacturer range goes far
#: beyond this; ten is where a list stops helping and typing is quicker.
SECURITY_LEVELS_SHOWN = 10


def seed_subfunction(level: int) -> int:
    """The requestSeed sub-function of a security level: 1 -> 0x01, 2 -> 0x03."""
    return level * 2 - 1


def security_level(seed_sub: int) -> int | None:
    """Which level a requestSeed sub-function belongs to, or None if even.

    SecurityAccess pairs its sub-functions: odd asks for the seed, and the
    even one after it sends the key. So a level is a pair, and only the odd
    half of it identifies the level.
    """
    return None if seed_sub % 2 == 0 else (seed_sub + 1) // 2


def security_levels(count: int = SECURITY_LEVELS_SHOWN) -> dict[int, str]:
    """Keyed by requestSeed sub-function, which is what gets sent.

    Named by level as well, because an ECU document says one or the other
    and rarely both: "security level 2" and "unlock with 0x03" are the same
    request. Keyed by the sub-function rather than the level because the
    manufacturer range is where most real unlocking happens -- a bootloader
    on 0x11/0x12 is level 9, which nobody says out loud.
    """
    return {seed_subfunction(n): f"Level {n}" for n in range(1, count + 1)}


def security_pair(seed_sub: int) -> str:
    """ "requestSeed 03, sendKey 04", for saying what will actually be sent."""
    return f"requestSeed {seed_sub:02X}, sendKey {seed_sub + 1:02X}"


def _numbers(holder) -> dict[str, int]:
    """The integer constants on a udsoncan class, and nothing else.

    ``vars()`` on a class also hands back Python's own attributes, and some of
    those are integers: ``__firstlineno__`` is 18, which was quietly making
    identifier 0x0012 and routine 0x0057 look as though the standard named
    them individually.
    """
    return {
        name: value
        for name, value in vars(holder).items()
        if isinstance(value, int) and not name.startswith("_")
    }


def _pretty(name: str) -> str:
    """ "ECUSerialNumberDataIdentifier" -> "ECU serial number".

    udsoncan spells the ISO names out in full and runs the words together,
    which is right for a constant and unreadable in a dropdown. Acronyms are
    left in capitals; everything else is a sentence.
    """
    name = re.sub(r"DataIdentifier$|RoutineID$", "", name)
    words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", name).split()
    if not words:
        return ""
    return " ".join(
        word if word.isupper() else word.lower() if index else word.capitalize()
        for index, word in enumerate(words)
    )


#: The identifiers ISO 14229-1 names one by one, as opposed to the ranges it
#: only gives a meaning to. udsoncan holds the named ones as class
#: attributes, so this is exact rather than a guess at which is which.
NAMED = set(_numbers(DataIdentifier).values())


def did_name(did: int) -> str:
    """The ISO 14229-1 name of an identifier the standard names individually.

    "" for the rest. Every identifier belongs to *some* range, so answering
    with the range here would put "(manufacturer specific)" beside every
    identifier an ECU actually uses, on every line -- true, and no help at
    all after the first time. :func:`did_range` is where that lives.
    """
    if did not in NAMED:
        return ""
    try:
        name = DataIdentifier.name_from_id(did)
    except Exception:  # an identifier outside anything udsoncan knows
        return ""
    return SHORTER.get(name) or _pretty(name)


def did_names() -> dict[int, str]:
    """Every identifier ISO 14229-1 names individually, for the DID dropdown."""
    return {did: did_name(did) for did in sorted(NAMED) if did_name(did)}


def did_range(did: int) -> str:
    """What ISO 14229-1 reserves this part of the identifier space for.

    Always something: knowing 0x0200 is manufacturer specific is the
    difference between reading the ECU's documentation and hunting for a
    standard that does not cover it.
    """
    try:
        name = DataIdentifier.name_from_id(did)
    except Exception:
        return ""
    return SHORTER.get(name) or _pretty(name)


#: The routine identifiers ISO 14229-1 Annex F names one by one. There are
#: four: erase memory, check programming dependencies, erase mirror memory
#: DTCs and the deploy loop. Everything else in the space is a range, most of
#: it manufacturer specific -- which is why a flash sequence is never quite
#: the same twice.
NAMED_ROUTINES = set(_numbers(Routine).values())


def routine_name(routine_id: int) -> str:
    """The ISO 14229-1 name of a routine the standard names individually.

    "" for the rest, for the same reason as :func:`did_name`: every routine
    identifier is inside some range, and answering with the range would put
    "(manufacturer specific)" beside every routine an ECU actually has.
    """
    if routine_id not in NAMED_ROUTINES:
        return ""
    return SHORTER.get(Routine.name_from_id(routine_id) or "", "")


def routine_names() -> dict[int, str]:
    """The four routines ISO 14229-1 names, for the routine dropdown."""
    return {r: routine_name(r) for r in sorted(NAMED_ROUTINES) if routine_name(r)}


def routine_range(routine_id: int) -> str:
    """What ISO 14229-1 reserves this part of the routine space for."""
    name = Routine.name_from_id(routine_id)
    return SHORTER.get(name or "", name or "")


def memory_record(address: int, size: int, width: int | None = None) -> bytes:
    """An address and a length, written the way ISO 14229-1 writes them.

    A leading addressAndLengthFormatIdentifier saying how many bytes each of
    the two takes, then the address, then the length. This is the shape
    RequestDownload uses, and the shape the erase and check routines are
    conventionally given as their option record.

    `width` in bits forces both fields to that size; without it each is as
    narrow as it can be, and never narrower than one byte -- an address of
    zero still has to be written down.
    """
    address_bytes = (width or _narrowest(address)) // 8
    size_bytes = (width or _narrowest(size)) // 8
    identifier = (size_bytes << 4) | address_bytes
    return (
        bytes([identifier])
        + address.to_bytes(address_bytes, "big")
        + size.to_bytes(size_bytes, "big")
    )


def _narrowest(value: int) -> int:
    return max(8, ((value.bit_length() + 7) // 8) * 8)
