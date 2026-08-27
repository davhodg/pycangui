"""pycangui J1939 hooks -- edit freely, this file is yours.

Each function below is called by pycangui at a decision point.  Return a value
to take over, or return None to let pycangui do the normal thing.  A function
that raises is reported in the Event Log pane and ignored.  Tools > Reload hooks
picks up changes without a restart.

Where a standard answer exists, the call that fetches it is *in this file*:
your own table is consulted first, and the standard one is the fallback, so
the standard behaviour is visible and yours to change.  Returning None means
"do the usual thing" and lets pycangui's own copy of the function answer, so
return an empty string when you mean "show nothing".

The failure mode identifiers are standard -- fixed meanings from SAE
J1939-73 -- so they are filled in.  The SPNs are not: there are thousands
of them, they are defined in SAE J1939-71, and that document cannot be shipped
in an Apache-2.0 project.  The practical source for SPN names is a J1939 DBC
(File > Load DBC), which names the signals in each PGN; SPN_NAMES below is for
proprietary ones and for anything a DBC does not cover.
"""

from __future__ import annotations

from pycangui.core.hooks import hook
from pycangui.j1939 import fmi_name

#: Your names for suspect parameter numbers, by SPN.
SPN_NAMES: dict[int, str] = {
    # 100: "Engine oil pressure",
    # 110: "Engine coolant temperature",
    # 190: "Engine speed",
    # 520192: "Proprietary sensor A",
}

#: Your names for proprietary PGNs, by PGN.
PGN_NAMES: dict[int, str] = {
    # 0xFF10: "MyStatus",
    # 0xFF11: "MyCommand",
}


@hook
def pgn_name(pgn: int, *, ctx) -> str | None:
    """Short name for a PGN, shown in the trace Kind column and the J1939 pane.

    Yours first, then pycangui's table of the common SAE PGNs, then "PGN <n>".
    """
    return PGN_NAMES.get(pgn)


@hook
def spn_description(spn: int, *, ctx) -> str | None:
    """Description for an SPN in a DM1 / DM2 fault, shown in the fault table.

    There is no standard table to fall back on -- see this file's header -- so
    an SPN that is neither here nor in a loaded DBC shows only its number.
    """
    return SPN_NAMES.get(spn)


@hook
def fmi_description(fmi: int, *, ctx) -> str | None:
    """What a failure mode identifier means, shown beside its number.

    These are fixed by SAE J1939-73 and are the same for every SPN on every
    ECU, so the standard text is almost always what you want: "voltage below
    normal" says a great deal more than "FMI 4".  Return your own text to
    override one, or an empty string to show the number alone -- returning
    None means "do the usual thing", which is this.
    """
    return fmi_name(fmi) or None
