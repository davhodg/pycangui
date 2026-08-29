"""The parts of UDS that are the same on every ECU.

Everything here comes from udsoncan, which already carries the ISO 14229-1
tables, rather than being typed out again: the data identifier names and the
ranges around them, and the negative response codes.

What is *not* here, deliberately:

* **DTC descriptions.**  ISO 14229-1 does not define any.  The text for the
  standard powertrain/chassis/body/network codes is SAE J2012, which is a
  copyrighted document of some thousands of entries and cannot be shipped in
  an Apache-2.0 project.  Everything above those ranges is manufacturer
  specific in any case.  The code itself -- "P0217" -- is arithmetic, and is
  produced by :func:`pycangui.uds.manager.dtc_code`.
* **The failure type byte.**  The low byte of a three-byte DTC (SAE J2012-DA
  "FTB": circuit short to ground, signal stuck high, ...) is from the same
  copyrighted document.

Both are what ``hooks/uds.py`` is for: a manufacturer's own list, or one taken
from an ODX or a DBC, is the right source and only its owner has it.
"""

from __future__ import annotations

from udsoncan import DataIdentifier, Routine

#: Names that are worth shortening.  udsoncan spells the ISO identifiers out in
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
    # Routine identifiers (Annex F).  Four are named individually; the rest of
    # the space is ranges.
    "EraseMemory": "Erase memory",
    "CheckProgrammingDependencies": "Check programming dependencies",
    "EraseMirrorMemoryDTCs": "Erase mirror memory DTCs",
    "DeployLoopRoutineID": "Deploy loop",
    "TachographTestIds": "Tachograph test",
    "OBDTestIds": "OBD test",
    "SafetySystemRoutineIDs": "Safety system",
}


#: The identifiers ISO 14229-1 names one by one, as opposed to the ranges it
#: only gives a meaning to.  udsoncan holds the named ones as class
#: attributes, so this is exact rather than a guess at which is which.
NAMED = {value for value in vars(DataIdentifier).values() if isinstance(value, int)}


def did_name(did: int) -> str:
    """The ISO 14229-1 name of an identifier the standard names individually.

    "" for the rest.  Every identifier belongs to *some* range, so answering
    with the range here would put "(manufacturer specific)" beside every
    identifier an ECU actually uses, on every line -- true, and no help at
    all after the first time.  :func:`did_range` is where that lives.
    """
    if did not in NAMED:
        return ""
    try:
        name = DataIdentifier.name_from_id(did)
    except Exception:  # an identifier outside anything udsoncan knows
        return ""
    return SHORTER.get(name, name)


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
    return SHORTER.get(name, name)


#: The routine identifiers ISO 14229-1 Annex F names one by one.  There are
#: four: erase memory, check programming dependencies, erase mirror memory
#: DTCs and the deploy loop.  Everything else in the space is a range, most of
#: it manufacturer specific -- which is why a flash sequence is never quite
#: the same twice.
NAMED_ROUTINES = {value for value in vars(Routine).values() if isinstance(value, int)}


def routine_name(routine_id: int) -> str:
    """The ISO 14229-1 name of a routine the standard names individually.

    "" for the rest, for the same reason as :func:`did_name`: every routine
    identifier is inside some range, and answering with the range would put
    "(manufacturer specific)" beside every routine an ECU actually has.
    """
    if routine_id not in NAMED_ROUTINES:
        return ""
    return SHORTER.get(Routine.name_from_id(routine_id) or "", "")


def routine_range(routine_id: int) -> str:
    """What ISO 14229-1 reserves this part of the routine space for."""
    name = Routine.name_from_id(routine_id)
    return SHORTER.get(name or "", name or "")


def memory_record(address: int, size: int, width: int | None = None) -> bytes:
    """An address and a length, written the way ISO 14229-1 writes them.

    A leading addressAndLengthFormatIdentifier saying how many bytes each of
    the two takes, then the address, then the length.  This is the shape
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
