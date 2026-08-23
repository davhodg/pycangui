"""pycangui J1939 hooks -- edit freely, this file is yours.

Each function below is called by pycangui at a decision point.  Return a value
to take over, or return None to let pycangui do the normal thing.  A function
that raises is reported in the Log pane and ignored.  Tools > Reload hooks
picks up changes without a restart.
"""

from __future__ import annotations

from pycangui.core.hooks import hook


@hook
def pgn_name(pgn: int, *, ctx) -> str | None:
    """Short name for a PGN, shown in the trace Kind column and the J1939 pane.

    Return None for the default (a built-in table of common SAE PGNs, then
    "PGN <n>").  Useful for proprietary PGNs.

    Example:

        # return {0xFF10: "MyStatus", 0xFF11: "MyCommand"}.get(pgn)
    """
    return None


@hook
def spn_description(spn: int, *, ctx) -> str | None:
    """Description for an SPN in a DM1 / DM2 fault, shown in the DTC table.

    Return None for no description.

    Example:

        # SPNS = {100: "Engine oil pressure", 110: "Engine coolant temperature",
        #         190: "Engine speed", 520192: "Proprietary sensor A"}
        # return SPNS.get(spn)
    """
    return None
