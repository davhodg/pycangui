"""pycangui CANopen hooks -- edit freely, this file is yours.

Each function below is called by pycangui at a decision point.  Return a value
to take over, or return None to let pycangui do the normal thing.  A function
that raises is reported in the Event Log pane and ignored, so mistakes here never
break the application.  Tools > Reload hooks picks up changes without a
restart; Tools > Update hook stubs appends any new hooks added by a newer
pycangui version.

``ctx`` is the pycangui context:
    ctx.log("text")          write to the Event Log pane
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
