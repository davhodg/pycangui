# SPDX-License-Identifier: MIT-0
#
# A starting point, copied into your workspace for you to change. It is
# yours to edit, keep private or give away: pycangui claims nothing in it
# and asks for no credit, so what you write here needs nobody's permission.
"""pycangui CANopen hooks -- edit freely, this file is yours.

Each function below is called by pycangui at a decision point. Return a value
to take over, or return None to let pycangui do the normal thing. A function
that raises is reported in the Event Log pane and ignored, so mistakes here never
break the application. Tools > Reload hooks picks up changes without a
restart. Hooks that a newer pycangui adds are appended to the end of this
file when that version first runs; what you have written is left alone.

``ctx`` is the pycangui context:
    ctx.log("text")          write a line to the Event Log pane
    ctx.warn("text")         the same, but open the pane if it is closed
    ctx.error("text")        for something that went wrong unasked
    ctx.eds_dir              Path of your EDS folder
    ctx.user_dir             Path of the pycangui user folder
    ctx.settings.get(key)    values pycangui remembers (see settings.json)
    ctx.canopen              the CANopen manager: nodes, SDO, PDO, NMT
    ctx.uds, ctx.j1939, ctx.xcp   the other protocol managers
    ctx.channels, ctx.bus    the channels, and the selected one
The managers are the same objects the Python Console has under the same
names, and they are None until the window exists -- in a script that has
built a Context of its own, there is nothing for them to point at.
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

    Asked when a node is first identified, and again whenever one that had
    gone quiet starts heartbeating -- a reflashed controller may want a
    different name, and its identity is not read again.

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
def stored_errors(node, *, ctx) -> list | None:
    """Read this device's fault list its own way.

    CiA 301 keeps recent faults in 0x1003, and pycangui reads that unless
    this hook answers. Plenty of makers do it differently -- a block of
    manufacturer objects, one object holding a packed array, a list you
    have to ask for by writing an index first -- and those are read here,
    where the device's own arrangement belongs.

    Return the entries newest first, or None to let pycangui read 0x1003.
    An entry is either a plain 32-bit number, read the way 0x1003 entries
    are read (code in the low word, the maker's own word in the high one),
    or a ``StoredError`` when you want to name the code yourself: a device
    using its own numbering gets the wrong name out of the CiA table, or
    none at all.

    Returning an empty list means "this device has no faults stored", which
    is not the same as None. Shown on the Faults tab.

    ``node`` is the canopen node object, so ``node.sdo`` reaches it. This
    one runs off the window's thread, unlike most hooks, so a dozen SDO
    reads here are fine: the pane is not waiting on them.

    Examples:

        # A block of manufacturer objects, one fault each, 0 for an empty slot
        # kept = []
        # for sub in range(1, 11):
        #     value = node.sdo.upload(0x5000, sub)
        #     code = int.from_bytes(value, "little")
        #     if code:
        #         kept.append(code)
        # return kept

        # One object holding a packed array, and the maker's own names
        # from pycangui.canopen.faults import StoredError
        # NAMES = {0x41: "Encoder fault", 0x65: "Contactor did not close"}
        # blob = node.sdo.upload(0x5310, 0)
        # return [
        #     StoredError(code=b, text=NAMES.get(b, "")) for b in blob if b
        # ]

        # Ask for each in turn: write the index, then read the answer
        # kept = []
        # for position in range(1, 6):
        #     node.sdo.download(0x5200, 1, bytes([position]))
        #     kept.append(int.from_bytes(node.sdo.upload(0x5200, 2), "little"))
        # return [code for code in kept if code]
    """
    return None


@hook
def clear_stored_errors(node, *, ctx) -> bool | None:
    """Empty this device's fault list its own way.

    The companion to ``stored_errors``: a device that keeps its faults
    somewhere of its own is usually cleared somewhere of its own too, and
    pycangui's default -- writing 0 to 0x1003 sub 0 -- would be writing to
    an object it may not have.

    Return True when the request has been sent, or None to let pycangui
    write to 0x1003. It runs off the window's thread, as ``stored_errors``
    does, so it may take its time.

    Examples:

        # A command object that means "forget the lot"
        # node.sdo.download(0x5001, 0, (1).to_bytes(4, "little"))
        # return True

        # Clear each slot in turn
        # for sub in range(1, 11):
        #     node.sdo.download(0x5000, sub, bytes(4))
        # return True
    """
    return None


@hook
def emcy_manufacturer(code: int, register: int, data: bytes, *, ctx) -> str | None:
    """Decode the five manufacturer-specific bytes of an emergency object.

    Bytes 3..7 of an EMCY mean whatever the device maker decided, so only you
    can decode them. Return the text to show in the Emergencies tab, or None
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


#: How to show particular objects, keyed by (index, sub-index). Every key is
#: optional and what you leave out keeps whatever the EDS said, so naming a
#: unit does not discard the limits the file declared.
#:
#: OBJECT_DISPLAY = {
#: (0x2001, 0): {"unit": "A", "factor": 0.1, "decimals": 1},
#: (0x2002, 0): {"choices": {0: "Off", 1: "Run", 2: "Fault"}},
#: (0x6060, 0): {"name": "Mode of operation"},
#:     }
OBJECT_DISPLAY: dict[tuple[int, int], dict] = {}


@hook
def object_display(index: int, sub: int, extras: dict, identity, *, ctx) -> dict | None:
    """How to show one object dictionary entry: name, unit, scaling, choices.

    Return a dict with any of ``name``, ``description``, ``unit``, ``factor``,
    ``offset``, ``decimals``, ``choices``, ``low``, ``high``. Anything left
    out keeps what the EDS said; returning None keeps all of it.

    ``extras`` is everything the EDS carried about this object that the
    ``canopen`` package did not keep. Mostly that means the maker's own
    comment lines -- CiA 306 defines no key for a unit or for scaling, so a
    maker with that to say hides it in a comment, where it survives every
    conforming reader by being ignored. A line like::

        ;THEIRTAG UNITS=V
        ;THEIRTAG SCALING=0.0625

    arrives here as ``{"THEIRTAG UNITS": "V", "THEIRTAG SCALING": "0.0625"}``.

    **pycangui reads none of it, deliberately.**  Not one of those fields is
    CANopen: the tag, the names and the meanings are one maker's convention,
    and the next maker's ``SCALING`` could as easily be a divisor. Guessing
    would put a wrong number on screen with nothing to say it was a guess, and
    a parameter that reads 10x out is worse than one that reads raw. So the
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


@hook
def login(node, level: int, password: str, *, ctx) -> bool | None:
    """Obtain an access level on a node, for Login... in the CANopen pane.

    CANopen has no standard for this, so each maker does it their own way: a
    password written to an object, a seed read and a key written back, or a
    level that simply follows from a write. Return True when the node granted
    the level, False when it refused, or None when this device has no login,
    which is the default.

    ``node`` is the live node, ``level`` the one asked for, and ``password``
    whatever was typed, empty if nothing was. This runs on a worker thread,
    so SDO calls may take their time:

        node.sdo.upload(index, sub)              read, as bytes
        node.sdo.download(index, sub, data)      write bytes
        node.sdo[index][sub].raw                 a value, where the EDS describes it

    An SDO abort raises, and a hook that raises is reported in the Event Log
    and counts as no login. pycangui neither logs nor keeps the password, so
    do not log it here either.

    Examples:

        # A password, as a 32-bit number, written to one of the maker's objects
        # node.sdo.download(0x2F00, 1, int(password or 0).to_bytes(4, "little"))
        # return True

        # Seed and key: read a seed and answer it
        # seed = int.from_bytes(node.sdo.upload(0x2F00, 2), "little")
        # key = (seed ^ 0x5A5A5A5A) & 0xFFFFFFFF   # the maker's algorithm goes here
        # node.sdo.download(0x2F00, 3, key.to_bytes(4, "little"))
        # return current_level(node, ctx=ctx) == level
    """
    return None


@hook
def current_level(node, *, ctx) -> int | None:
    """The access level a node says is held now, for the Access column.

    Asked after a login is granted and by Read access level. Return the level, or
    None when the device has no way of saying, which is the default: the
    column then shows the level the login was granted.

    Example:

        # return node.sdo.upload(0x2F01, 0)[0]
    """
    return None
