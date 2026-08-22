"""pycangui trace hooks -- edit freely, this file is yours.

Each function below is called by pycangui at a decision point.  Return a value
to take over, or return None to let pycangui do the normal thing.  A function
that raises is reported in the Log pane and ignored.  Tools > Reload hooks
picks up changes without a restart.
"""

from __future__ import annotations

from pycangui.core.bus import Frame  # re-exported for user code
from pycangui.core.hooks import hook


@hook
def frame_kind(frame: Frame, *, ctx) -> str | None:
    """Label shown in the trace's Kind column for a frame.

    Called for every frame, so keep it quick.  Return None for the default,
    which names CANopen messages from the predefined connection set
    ("TPDO1 n5", "SDO-T n5", "HB n5", "NMT", "SYNC", "EMCY n5", ...).
    The first word of your label decides the filter group: NMT, SYNC, TIME,
    EMCY, TPDO/RPDO/PDO, SDO, HB, LSS, anything else -> Other.

    frame.can_id, frame.extended, frame.fd, frame.dlc, frame.data, frame.rx

    Examples:

        # Name your own application messages
        # NAMES = {0x123: "Pump status", 0x124: "Pump command"}
        # return NAMES.get(frame.can_id)

        # Mark a J1939-style 29-bit id by its PGN
        # if frame.extended:
        #     return f"PGN {(frame.can_id >> 8) & 0x3FFFF:05X}"
    """
    return None
