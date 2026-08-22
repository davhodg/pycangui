"""pycangui CANopen hooks -- edit freely, this file is yours.

Each function below is called by pycangui at a decision point.  Return a value
to take over, or return None to let pycangui do the normal thing.  A function
that raises is reported in the Log pane and ignored, so mistakes here never
break the application.  Tools > Reload hooks picks up changes without a
restart; Tools > Update hook stubs appends any new hooks added by a newer
pycangui version.

``ctx`` is the pycangui context:
    ctx.log("text")          write to the Log pane
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
