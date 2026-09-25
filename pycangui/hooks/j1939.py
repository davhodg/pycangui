# SPDX-License-Identifier: MIT-0
#
# A starting point, copied into your workspace for you to change. It is
# yours to edit, keep private or give away: pycangui claims nothing in it
# and asks for no credit, so what you write here needs nobody's permission.
"""pycangui J1939 hooks -- edit freely, this file is yours.

Each function below is called by pycangui at a decision point. Return a value
to take over, or return None to let pycangui do the normal thing. A function
that raises is reported in the Event Log pane and ignored. Tools > Reload hooks
picks up changes without a restart.

The name tables live here, filled in rather than hidden inside pycangui, so
that what is known is visible and adding to it is obvious -- the reserved
failure modes, a proprietary PGN, the SPNs you care about. Everything on this
page is a plain dictionary; edit it and use Tools > Reload hooks.

The failure modes are fixed by SAE J1939-73 and the PGN names come from
SAE J1939-71. What is *not* here is the SPN list: several thousand entries,
also J1939-71, and too much of somebody else's document to ship. A J1939 DBC
(File > Load DBC) names the signals in each PGN and is the practical source;
SPN_NAMES below is for proprietary ones and anything a DBC misses.

Returning None from a hook means "do the usual thing", which lets pycangui's
own copy of that function answer. Return an empty string when you mean "show
nothing".
"""

from __future__ import annotations

from pycangui.core.hooks import hook

#: Your names for suspect parameter numbers, by SPN.
SPN_NAMES: dict[int, str] = {
    # 100: "Engine oil pressure",
    # 110: "Engine coolant temperature",
    # 190: "Engine speed",
    # 520192: "Proprietary sensor A",
}

#: Parameter group names, shown in the trace's Kind column and the J1939
#: pane. The common ones from SAE J1939-71; add your proprietary PGNs here
#: (the 0xFF00-0xFFFF range is reserved for exactly that).
PGN_NAMES: dict[int, str] = {
    59392: "ACK",
    59904: "Request",
    60160: "TP.DT",
    60416: "TP.CM",
    60928: "AddressClaim",
    61184: "ProprietaryA",
    61440: "ERC1",
    61441: "EBC1",
    61442: "ETC1",
    61443: "EEC2",
    61444: "EEC1",
    61445: "ETC2",
    65132: "TCO1",
    65198: "AT1T1I",
    65217: "VDHR",
    65226: "DM1",
    65227: "DM2",
    65228: "DM3",
    65235: "DM11",
    65242: "SoftwareID",
    65247: "EEC3",
    65248: "VD",
    65253: "HOURS",
    65254: "TD",
    65257: "LFC",
    65259: "ComponentID",
    65260: "VI",
    65262: "ET1",
    65263: "EFL/P1",
    65265: "CCVS1",
    65266: "LFE1",
    65269: "AMB",
    65270: "IC1",
    65271: "VEP1",
    65272: "TRF1",
    65276: "DD",
    # Your proprietary PGNs go here. J1939 reserves 65280-65535 (0xFF00 up)
    # for them, so nothing standard will ever collide with what you add.
    # 0xFF10: "MyStatus",
    # 0xFF11: "MyCommand",
}


#: What each failure mode identifier means (SAE J1939-73), in plain words
#: rather than the standard's own. 22 to 30 are
#: reserved for future assignment and so are not here: an FMI with no entry
#: shows as a bare number rather than a guess.
FMI_NAMES: dict[int, str] = {
    0: "Value too high, most severe level",
    1: "Value too low, most severe level",
    2: "Value erratic, intermittent or wrong",
    3: "Voltage too high, or shorted to a higher voltage",
    4: "Voltage too low, or shorted to a lower voltage",
    5: "Current too low, or an open circuit",
    6: "Current too high, or shorted to ground",
    7: "Mechanism not responding, or not adjusted correctly",
    8: "Frequency, pulse width or period out of range",
    9: "Updates arriving at the wrong rate",
    10: "Changing too quickly or too slowly",
    11: "Cause not identified",
    12: "Faulty smart device or component",
    13: "Needs calibrating",
    14: "See the manufacturer's instructions",
    15: "Value too high, least severe level",
    16: "Value too high, moderate level",
    17: "Value too low, least severe level",
    18: "Value too low, moderate level",
    19: "Data received over the network is wrong",
    20: "Value drifting high",
    21: "Value drifting low",
    31: "Condition present",
}


@hook
def pgn_name(pgn: int, *, ctx) -> str | None:
    """Short name for a PGN, shown in the trace Kind column and the J1939 pane.

    Anything PGN_NAMES does not cover is shown as "PGN <n> (0x...)".
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

    The same for every SPN on every ECU, which is why they are worth having:
    "voltage too low" says a great deal more than "FMI 4". 22 to 30 are
    reserved by SAE for future assignment, so they are absent above rather
    than guessed at -- if a standard revision fills one in, or your ECU uses
    one anyway, add it to FMI_NAMES and reload.
    """
    return FMI_NAMES.get(fmi)
