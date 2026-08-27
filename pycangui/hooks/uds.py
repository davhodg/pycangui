"""pycangui UDS hooks -- edit freely, this file is yours.

Each function below is called by pycangui at a decision point.  Return a value
to take over, or return None to let pycangui do the normal thing.  A function
that raises is reported in the Event Log pane and ignored.  Tools > Reload hooks
picks up changes without a restart.

``ctx`` is the pycangui context: ctx.log("text"), ctx.settings.get(key), ...

Where a standard answer exists, the call that fetches it is *in this file*:
your own table is consulted first, and the library is the fallback.  The
standard behaviour is therefore visible rather than hidden behind pycangui,
and yours to change.

One thing to know: returning None means "do the usual thing", and pycangui
then runs its own copy of the function you are looking at.  So deleting a
fallback does not remove it -- return an empty string instead, which is an
answer rather than a shrug.

Data identifier names come from ISO 14229-1, via udsoncan.  DTC descriptions
have no standard to fall back on and never will: ISO 14229-1 defines none, and
the text for the SAE codes is a copyrighted document of several thousand
entries.  DTC_DESCRIPTIONS below is therefore empty -- fill in the codes your
ECU actually raises, or read them out of its ODX.
"""

from __future__ import annotations

from pycangui.core.hooks import hook
from pycangui.uds.standard import did_name

#: Your names for data identifiers, tried before the ISO ones.  Anything below
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


@hook
def security_key(level: int, seed: bytes, *, ctx) -> bytes | None:
    """Compute the SecurityAccess key for a seed (service 0x27).

    ``level`` is the odd requestSeed sub-function (1, 3, 5 ...).  Return the
    key bytes, or None if you have no algorithm for this level -- pycangui
    then reports that unlocking is not possible.  This is the hook almost every
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
    gives the ranges around them a meaning.  "Manufacturer specific" is worth
    saying: it means go and read the ECU's documentation rather than look for a
    standard that does not cover this.

    Returning None means "do the usual thing", so dropping the
    ``did_name(did)`` call is not enough to be rid of it -- pycangui's own copy
    of this function would answer instead.  Return an empty string to say
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
    type byte and then on the whole number, so you can describe either.  There
    is no standard list to fall back on -- see this file's header -- so an
    unknown DTC shows its code and nothing else.
    """
    return DTC_DESCRIPTIONS.get(dtc >> 8) or DTC_DESCRIPTIONS.get(dtc)
