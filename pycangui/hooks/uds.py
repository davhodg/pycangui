# SPDX-License-Identifier: MIT-0
#
# A starting point, copied into your workspace for you to change. It is
# yours to edit, keep private or give away: pycangui claims nothing in it
# and asks for no credit, so what you write here needs nobody's permission.
"""pycangui UDS hooks -- edit freely, this file is yours.

Each function below is called by pycangui at a decision point. Return a value
to take over, or return None to let pycangui do the normal thing. A function
that raises is reported in the Event Log pane and ignored. Tools > Reload hooks
picks up changes without a restart.

``ctx`` is the pycangui context: ctx.log("text"), ctx.settings.get(key), ...
ctx.warn("text") says the same thing but opens the Event Log if it has been
closed, which is what to use when somebody is waiting on an answer.

Where a standard answer exists, the call that fetches it is *in this file*:
your own table is consulted first, and the library is the fallback. The
standard behaviour is therefore visible rather than hidden behind pycangui,
and yours to change.

One thing to know: returning None means "do the usual thing", and pycangui
then runs its own copy of the function you are looking at. So deleting a
fallback does not remove it -- return an empty string instead, which is an
answer rather than a shrug.

Data identifier names come from ISO 14229-1, via udsoncan. DTC descriptions
have no standard to fall back on and never will: ISO 14229-1 defines none, and
the text for the SAE codes is a copyrighted document of several thousand
entries. DTC_DESCRIPTIONS below is therefore empty -- fill in the codes your
ECU actually raises, or read them out of its ODX.
"""

from __future__ import annotations

from pycangui.core.hooks import hook
from pycangui.uds.standard import (
    SESSIONS,
    did_name,
    did_names,
    memory_record,
    routine_name,
    routine_names,
)

#: Your names for data identifiers, tried before the ISO ones. Anything below
#: 0xF180 is manufacturer specific, so ISO can only say "manufacturer
#: specific": this is the only place the real name can come from.
DID_NAMES: dict[int, str] = {
    # 0x0101: "Battery voltage",
    # 0xF1A0: "Calibration set",
}

#: Descriptions for the DTCs your ECU raises, keyed on the two-byte code
#: without its failure type byte, so one entry covers every failure type of
#: the same fault.
DTC_DESCRIPTIONS: dict[int, str] = {
    # 0x0123: "Throttle position sensor range",
    # 0x9A01: "CAN bus off",
}

#: Your names for routines, tried before the ISO ones. ISO 14229-1 names only
#: four routines: erase memory (0xFF00), check programming dependencies
#: (0xFF01), erase mirror memory DTCs (0xFF02) and the deploy loop (0xE200).
#: Everything from 0x0200 to 0xDFFF is manufacturer specific, which is where
#: the rest of a flash sequence lives -- 0x0202 below is the number the
#: HIS/AUTOSAR bootloaders use to have the ECU check what it was just given,
#: and it is a convention rather than a standard.
ROUTINE_NAMES: dict[int, str] = {
    0x0202: "Check memory",
}

#: Sessions beyond the four ISO 14229-1 names. 0x40 to 0x5F belong to the
#: vehicle manufacturer and 0x60 to 0x7E to the system supplier, so only the
#: people who built the ECU know what is in there or what it is called. Fill
#: these in and they appear in the Session dropdown.
SESSION_NAMES: dict[int, str] = {
    # 0x40: "End of line",
    # 0x4F: "Development",
}

#: What an identifier actually holds, shown when one is chosen. A name says
#: which identifier it is; a description says what you will get back and in
#: what form, which is the part worth writing down. The few below are from
#: ISO 14229-1; the ones you care about will be your own.
DID_DESCRIPTIONS: dict[int, str] = {
    0xF186: "The session the ECU is in now: one byte, numbered as DiagnosticSessionControl.",
    0xF187: "Manufacturer's spare part number for this ECU, as text.",
    0xF188: "Manufacturer's software number, as text.",
    0xF189: "Manufacturer's software version, as text.",
    0xF18C: "ECU serial number, as text.",
    0xF190: "Vehicle identification number: 17 characters (ISO 3779).",
}

#: What a routine does. The four ISO 14229-1 names are described here rather
#: than in pycangui so that they can be corrected: an ECU is free to make
#: 0xFF00 mean something narrower than the standard's wording.
ROUTINE_DESCRIPTIONS: dict[int, str] = {
    0xFF00: (
        "Erase the memory the option record covers, before it is written. "
        "The option record is usually an address and a length."
    ),
    0xFF01: "Check that the software and calibrations the ECU now holds fit together.",
    0xFF02: "Erase the mirror of the fault memory, leaving the primary memory alone.",
    0x0202: (
        "Have the ECU check what it was just given, usually against a checksum. "
        "A HIS/AUTOSAR convention rather than a standard, so the number varies."
    ),
}


@hook
def security_key(level: int, seed: bytes, *, ctx) -> bytes | None:
    """Compute the SecurityAccess key for a seed (service 0x27).

    ``level`` is the odd requestSeed sub-function (1, 3, 5 ...). Return the
    key bytes, or None if you have no algorithm for this level -- pycangui
    then reports that unlocking is not possible. This is the hook almost every
    ECU needs; there is no standard algorithm, so there is no default.

    Examples:

        # Invert every byte (a common development-ECU scheme)
        # return bytes(b ^ 0xFF for b in seed)

        # XOR with a 32-bit constant, big-endian
        # value = int.from_bytes(seed, "big") ^ 0xDEADBEEF
        # return value.to_bytes(len(seed), "big")

        # Different algorithms per level
        # if level == 1:
        #     return bytes(b ^ 0xFF for b in seed)
        # if level == 3:
        #     return my_vendor_algorithm(seed)
        # return None
    """
    return None


@hook
def did_label(did: int, *, ctx) -> str | None:
    """What a data identifier is called, shown beside its number.

    Your DID_NAMES first, then the ISO 14229-1 name -- which exists for every
    identifier, since the standard names them individually from 0xF180 up and
    gives the ranges around them a meaning. "Manufacturer specific" is worth
    saying: it means go and read the ECU's documentation rather than look for a
    standard that does not cover this.

    Returning None means "do the usual thing", so dropping the
    ``did_name(did)`` call is not enough to be rid of it -- pycangui's own copy
    of this function would answer instead. Return an empty string to say
    "nothing to show here" and mean it.
    """
    return DID_NAMES.get(did) or did_name(did) or None


@hook
def did_decode(did: int, data: bytes, *, ctx) -> str | None:
    """Human readable text for a ReadDataByIdentifier (0x22) response.

    Return None for the default (hex bytes, plus ASCII if it looks printable).

    Examples:

        # if did == 0xF190:                      # VIN
        #     return data.decode("ascii", "replace")
        # if did == 0x0101:                      # battery voltage, 0.1 V/bit
        #     return f"{int.from_bytes(data, 'big') / 10:.1f} V"
    """
    return None


@hook
def did_encode(did: int, text: str, *, ctx) -> bytes | None:
    """Bytes to send for a WriteDataByIdentifier (0x2E) from what you typed.

    Return None for the default: hex bytes ("01 02 0A"), or, if the text is not
    valid hex, its ASCII encoding.

    Example:

        # if did == 0x0102:                      # 16-bit setpoint, big-endian
        #     return int(text).to_bytes(2, "big")
    """
    return None


@hook
def dtc_description(dtc: int, *, ctx) -> str | None:
    """Description for a 3-byte DTC number, shown next to its P/C/B/U code.

    DTC_DESCRIPTIONS above, matched first on the fault without its failure
    type byte and then on the whole number, so you can describe either. There
    is no standard list to fall back on -- see this file's header -- so an
    unknown DTC shows its code and nothing else.
    """
    return DTC_DESCRIPTIONS.get(dtc >> 8) or DTC_DESCRIPTIONS.get(dtc)


@hook
def routine_label(routine_id: int, *, ctx) -> str | None:
    """What a routine is called, shown beside its number.

    ROUTINE_NAMES first, then the four ISO 14229-1 names. Unlike data
    identifiers there is no range fallback here: saying "manufacturer
    specific" of a routine number would be true of almost all of them.
    """
    return ROUTINE_NAMES.get(routine_id) or routine_name(routine_id) or None


@hook
def erase_options(address: int, size: int, width, *, ctx) -> bytes | None:
    """The option record sent with the erase routine (0xFF00) before a download.

    ISO 14229-1 names the routine but says nothing about what to give it. An
    address and a length in the usual format -- a byte saying how wide each
    is, then the two numbers -- is what most bootloaders expect, and is what
    ``memory_record`` builds.

    Return b"" for a bootloader that erases a fixed region and wants no
    arguments at all, or build whatever yours does want:

        # A block number rather than an address
        # return bytes([address >> 16])

        # Address and length, always 32 bits each, whatever the numbers are
        # return memory_record(address, size, 32)
    """
    return memory_record(address, size, width)


@hook
def check_options(
    routine: int, address: int, size: int, data: bytes, width, *, ctx
) -> bytes | None:
    """The option record sent with the check routine after a download.

    This one has no standard behind it at all -- the routine itself is
    manufacturer specific -- so the default is the same address and length
    record as the erase, which is the most common shape. Many bootloaders
    want a checksum of what was sent instead, or as well:

        # CRC32 of the segment, appended to the address and length
        # import zlib
        # return memory_record(address, size, width) + zlib.crc32(data).to_bytes(4, "big")

        # The CRC on its own, which is what the HIS bootloaders ask for
        # import zlib
        # return zlib.crc32(data).to_bytes(4, "big")

    ``data`` is the segment that was just sent, so a checksum can be worked
    out here rather than read back off the disk.
    """
    return memory_record(address, size, width)


@hook
def sessions(*, ctx) -> dict[int, str] | None:
    """Which sessions the Session dropdown offers.

    The four ISO 14229-1 names, plus SESSION_NAMES above. Yours win, so an
    ECU that calls 0x03 something of its own can say so.
    """
    return {**SESSIONS, **SESSION_NAMES}


@hook
def did_choices(*, ctx) -> dict[int, str] | None:
    """Which identifiers the DID dropdown offers.

    The ones ISO 14229-1 names individually, plus DID_NAMES. The box stays
    typeable, so this is a shortlist rather than a restriction -- return only
    DID_NAMES to have it offer nothing but your own.
    """
    return {**did_names(), **DID_NAMES}


@hook
def did_description(did: int, *, ctx) -> str | None:
    """What the identifier holds, shown beside it in the dropdown.

    Longer than the name and worth more: "17 characters (ISO 3779)" is the
    difference between reading a response and guessing at it. There is no
    standard list of these, so DID_DESCRIPTIONS is all there is.
    """
    return DID_DESCRIPTIONS.get(did)


@hook
def routine_choices(*, ctx) -> dict[int, str] | None:
    """Which routines the Routine dropdown offers.

    ISO 14229-1 names four; everything from 0x0200 to 0xDFFF is the
    manufacturer's, which is where the rest of a flash sequence lives.
    """
    return {**routine_names(), **ROUTINE_NAMES}


@hook
def routine_description(routine_id: int, *, ctx) -> str | None:
    """What the routine does, shown beside it in the dropdown."""
    return ROUTINE_DESCRIPTIONS.get(routine_id)
