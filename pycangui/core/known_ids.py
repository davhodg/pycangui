# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Every identifier pycangui will put a name to, and where the name comes from.

Nothing is named on the strength of its identifier any more: a database
somebody loaded names what it was written to name, and a protocol names what
it has been told about -- the nodes CANopen knows of, the addresses in the UDS
pane, the identifiers in the XCP pane. That is the right way round, and it
leaves an obvious question, which is what this answers: *what does it know
about?*

Worth having in front of somebody rather than inferred from an empty Kind
column. A frame that is not named is either one nothing was told about or one
whose id is not where it was expected, and those are different problems with
the same appearance.

Collected here rather than in the dialog so that what it says can be checked
without opening a window.
"""

from __future__ import annotations

from dataclasses import dataclass

#: J1939 names frames from the PGN in a 29-bit identifier, so there is no
#: list of them to give: the rule is the answer.
J1939_NOTE = "29-bit frames are named from the PGN in the identifier"


@dataclass(frozen=True)
class Known:
    """One identifier, what it would be called, and who says so."""

    can_id: int
    extended: bool
    name: str
    source: str

    @property
    def shown(self) -> str:
        """The id as the trace writes it: hex, and wide enough to be read."""
        return f"{self.can_id:08X}" if self.extended else f"{self.can_id:03X}"


def from_databases(dbc) -> list[Known]:
    """Every message in every loaded CAN database."""
    out = []
    for message in dbc.messages():
        out.append(
            Known(
                can_id=message.frame_id,
                extended=bool(message.is_extended_frame),
                name=message.name,
                source="Database",
            )
        )
    return out


def from_canopen(canopen) -> list[Known]:
    """What the known nodes account for, including where an EDS moved it."""
    return [
        Known(can_id=can_id, extended=False, name=name, source="CANopen")
        for can_id, name in sorted(canopen.known_ids().items())
    ]


def from_uds(uds) -> list[Known]:
    """The addresses the UDS pane is set to, functional included."""
    from pycangui.uds import NO_ID

    wanted = (
        (uds.config.tx_id, "UDS request (tester to ECU)"),
        (uds.config.rx_id, "UDS response (ECU to tester)"),
        (uds.config.functional_id, "UDS functional request (every ECU)"),
    )
    return [
        Known(can_id=can_id, extended=bool(uds.config.extended_id), name=name, source="UDS")
        for can_id, name in wanted
        if can_id != NO_ID
    ]


def from_xcp(xcp) -> list[Known]:
    """The identifiers typed into the XCP pane, if any have been."""
    engine = getattr(xcp, "engine", None)
    if engine is None:
        return []
    from pycangui.xcp.engine import NO_ID

    extended = bool(getattr(engine, "extended", False))
    return [
        Known(can_id=can_id, extended=extended, name=name, source="XCP")
        for can_id, name in (
            (getattr(engine, "cmd_id", NO_ID), "XCP command (to the slave)"),
            (getattr(engine, "res_id", NO_ID), "XCP response (from the slave)"),
        )
        if can_id != NO_ID
    ]


def collect(dbc=None, canopen=None, uds=None, xcp=None) -> list[Known]:
    """Everything that would be named, in the order the trace asks them.

    Sorted by id within each source rather than overall: two sources can
    name the same id, and seeing both is the point -- that is a clash to
    know about, not something to hide by keeping one of them.
    """
    out: list[Known] = []
    for collector, thing in (
        (from_databases, dbc),
        (from_canopen, canopen),
        (from_uds, uds),
        (from_xcp, xcp),
    ):
        if thing is not None:
            out.extend(sorted(collector(thing), key=lambda known: known.can_id))
    return out


def clashes(known: list[Known]) -> set[int]:
    """Ids two sources both claim, which is worth pointing out.

    The first source in the list wins in the trace, so the second one's name
    never appears; somebody looking for it deserves to see why rather than
    concluding the tool is broken.
    """
    seen: dict[tuple[int, bool], str] = {}
    twice: set[int] = set()
    for entry in known:
        key = (entry.can_id, entry.extended)
        if key in seen and seen[key] != entry.source:
            twice.add(entry.can_id)
        seen[key] = entry.source
    return twice
