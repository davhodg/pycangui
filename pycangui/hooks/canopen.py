# SPDX-License-Identifier: MIT-0
#
# A starting point, copied into your workspace for you to change.  It is
# yours to edit, keep private or give away: pycangui claims nothing in it
# and asks for no credit, so what you write here needs nobody's permission.
"""pycangui CANopen hooks -- edit freely, this file is yours.

Each function below is called by pycangui at a decision point.  Return a value
to take over, or return None to let pycangui do the normal thing.  A function
that raises is reported in the Event Log pane and ignored, so mistakes here never
break the application.  Tools > Reload hooks picks up changes without a
restart.  Hooks that a newer pycangui adds are appended to the end of this
file when that version first runs; what you have written is left alone.

``ctx`` is the pycangui context:
    ctx.log("text")          write a line to the Event Log pane
    ctx.warn("text")         the same, but open the pane if it is closed
    ctx.error("text")        for something that went wrong unasked
    ctx.eds_dir              Path of your EDS folder
    ctx.user_dir             Path of the pycangui user folder
    ctx.settings.get(key)    values pycangui remembers (see settings.json)
"""

from __future__ import annotations

from pathlib import Path

from pycangui.canopen import NodeIdentity
from pycangui.core.hooks import hook


@hook
def node_name(identity: NodeIdentity, *, ctx) -> str | None:
    """Display name for a node in the CANopen pane.

    Return None for the default, which is "Node <id>" or the name remembered
    from the EDS (DeviceInfo ProductName) once one is loaded.

    Examples:

        # Name by product code
        # NAMES = {0x00001234: "Left motor", 0x00001235: "Right motor"}
        # return NAMES.get(identity.product_code)

        # Name by node id
        # return {1: "Master", 5: "Drive A", 6: "Drive B"}.get(identity.node_id)
    """
    return None


@hook
def eds_for_node(identity: NodeIdentity, *, ctx) -> Path | str | None:
    """Which EDS / DCF file describes this node.

    Return a path, or None for the default, which tries in order:
      1. the file you chose before for this identity (vendor:product:revision)
      2. any EDS in your eds folder whose [DeviceInfo] VendorNumber and
         ProductNumber match the node (exact RevisionNumber preferred)
      3. asking you, with the option to remember the answer

    Examples:

        # Files named <vendor>_<product>.eds in the EDS folder
        # if identity.vendor_id is not None and identity.product_code is not None:
        #     return ctx.eds_dir / f"{identity.vendor_id:08X}_{identity.product_code:08X}.eds"

        # One manufacturer, revision-specific files, anything else falls back
        # if identity.vendor_id == 0x000001A2:
        #     major = (identity.revision or 0) >> 16
        #     return ctx.eds_dir / f"acme_drive_v{major}.eds"

        # Everything on this bus is the same device
        # return ctx.eds_dir / "my_device.eds"
    """
    return None


@hook
def emcy_manufacturer(code: int, register: int, data: bytes, *, ctx) -> str | None:
    """Decode the five manufacturer-specific bytes of an emergency object.

    Bytes 3..7 of an EMCY mean whatever the device maker decided, so only you
    can decode them.  Return the text to show in the Emergencies tab, or None
    to leave the raw bytes on their own.

    ``code`` is the 16-bit error code (the standard part is decoded already),
    ``register`` is object 0x1001, ``data`` is the five bytes.

    Examples:

        # A 16-bit measured value in the first two bytes, then a channel number
        # if code == 0x2310 and len(data) >= 3:
        #     current = int.from_bytes(data[0:2], "little") / 10
        #     return f"{current:.1f} A on channel {data[2]}"

        # A bitfield of internal faults
        # FAULTS = {0x01: "encoder", 0x02: "hall", 0x04: "supply"}
        # if code == 0x5000 and data:
        #     names = [n for bit, n in FAULTS.items() if data[0] & bit]
        #     return "internal: " + (", ".join(names) or "none")

        # Some devices repeat the error code of the *previous* emergency
        # if len(data) >= 2:
        #     return f"previous code 0x{int.from_bytes(data[0:2], 'little'):04X}"
    """
    return None


#: How to show particular objects, keyed by (index, sub-index).  Every key is
#: optional and what you leave out keeps whatever the EDS said, so naming a
#: unit does not discard the limits the file declared.
#:
#:     OBJECT_DISPLAY = {
#:         (0x2001, 0): {"unit": "A", "factor": 0.1, "decimals": 1},
#:         (0x2002, 0): {"choices": {0: "Off", 1: "Run", 2: "Fault"}},
#:         (0x6060, 0): {"name": "Mode of operation"},
#:     }
OBJECT_DISPLAY: dict[tuple[int, int], dict] = {}


@hook
def object_display(index: int, sub: int, extras: dict, identity, *, ctx) -> dict | None:
    """How to show one object dictionary entry: name, unit, scaling, choices.

    Return a dict with any of ``name``, ``description``, ``unit``, ``factor``,
    ``offset``, ``decimals``, ``choices``, ``low``, ``high``.  Anything left
    out keeps what the EDS said; returning None keeps all of it.

    ``extras`` is everything the EDS carried about this object that the
    ``canopen`` package did not keep.  Mostly that means the maker's own
    comment lines -- CiA 306 defines no key for a unit or for scaling, so a
    maker with that to say hides it in a comment, where it survives every
    conforming reader by being ignored.  A line like::

        ;THEIRTAG UNITS=V
        ;THEIRTAG SCALING=0.0625

    arrives here as ``{"THEIRTAG UNITS": "V", "THEIRTAG SCALING": "0.0625"}``.

    **pycangui reads none of it, deliberately.**  Not one of those fields is
    CANopen: the tag, the names and the meanings are one maker's convention,
    and the next maker's ``SCALING`` could as easily be a divisor.  Guessing
    would put a wrong number on screen with nothing to say it was a guess, and
    a parameter that reads 10x out is worse than one that reads raw.  So the
    file's own fields are shown, untouched, on each object's tooltip in the
    CANopen pane -- look there to see what yours carries -- and turning them
    into units and scaling is three lines here, written by somebody who has
    the documentation.

    Example, for a file using the fields above::

        # units = next((v for k, v in extras.items() if k.endswith("UNITS")), "")
        # scale = next((v for k, v in extras.items() if k.endswith("SCALING")), "")
        # found = {}
        # if units:
        #     found["unit"] = units
        # if scale:
        #     found["factor"] = float(scale)
        # return {**found, **OBJECT_DISPLAY.get((index, sub), {})} or None

    What the EDS states in the ordinary way -- ParameterName, LowLimit,
    HighLimit, and Unit or Factor where a file uses those keys -- is read
    already and needs nothing here.
    """
    return OBJECT_DISPLAY.get((index, sub))
