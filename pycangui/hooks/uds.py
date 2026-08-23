"""pycangui UDS hooks -- edit freely, this file is yours.

Each function below is called by pycangui at a decision point.  Return a value
to take over, or return None to let pycangui do the normal thing.  A function
that raises is reported in the Event Log pane and ignored.  Tools > Reload hooks
picks up changes without a restart.

``ctx`` is the pycangui context: ctx.log("text"), ctx.settings.get(key), ...
"""

from __future__ import annotations

from pycangui.core.hooks import hook


@hook
def security_key(level: int, seed: bytes, *, ctx) -> bytes | None:
    """Compute the SecurityAccess key for a seed (service 0x27).

    ``level`` is the odd requestSeed sub-function (1, 3, 5 ...).  Return the
    key bytes, or None if you have no algorithm for this level -- pycangui
    then reports that unlocking is not possible.  This is the hook almost every
    ECU needs; the default is deliberately *not* a working algorithm.

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

    Return None for no description.

    Example:

        # DTCS = {0x0123: "Throttle position sensor range", 0x9A01: "CAN bus off"}
        # return DTCS.get(dtc & 0xFFFF)
    """
    return None
