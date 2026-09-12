# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""ReadDTCInformation (0x19): which report, and what each one needs.

The service is really twenty-odd services wearing one number.  A report asks
for a count or a list or one record; some take a status mask, some a DTC
number, some a record number, some a memory selection, and a few take nothing
at all.  Sending the wrong ones is answered with 0x13 and no explanation, so
the table below is what lets the pane grey out the boxes a report has no use
for rather than leaving you to guess.

Which parameters each report takes was read off udsoncan rather than out of
the standard: every entry here was checked by building the request and looking
at the bytes that came out, so the table cannot disagree with what will
actually be sent.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Parameter names, spelled as udsoncan's ``read_dtc_information`` takes them
#: so a report's ``needs`` can be passed straight through as keywords.
STATUS = "status_mask"
SEVERITY = "severity_mask"
DTC = "dtc"
SNAPSHOT = "snapshot_record_number"
EXTENDED = "extended_data_record_number"
MEMORY = "memory_selection"
GROUP = "functional_group_id"

#: No report takes a snapshot record number and an extended data record number
#: at once, so the pane offers one box for both.  This says which it is filling.
RECORDS = (SNAPSHOT, EXTENDED)


@dataclass(frozen=True)
class Report:
    subfunction: int
    name: str
    needs: tuple[str, ...] = ()
    note: str = ""

    @property
    def label(self) -> str:
        return f"0x{self.subfunction:02X}  {self.name}"


#: In ISO 14229-1's own order, which is roughly counts, then lists, then
#: records, then the mirror and OBD variants of the same ideas.
REPORTS: tuple[Report, ...] = (
    Report(0x01, "Number of DTCs by status mask", (STATUS,)),
    Report(0x02, "DTCs by status mask", (STATUS,)),
    Report(0x03, "Snapshot identification"),
    Report(0x04, "Snapshot record by DTC", (DTC, SNAPSHOT)),
    Report(0x05, "Snapshot record by record number", (SNAPSHOT,)),
    Report(0x06, "Extended data by DTC", (DTC, EXTENDED)),
    Report(0x07, "Number of DTCs by severity mask", (STATUS, SEVERITY)),
    Report(0x08, "DTCs by severity mask", (STATUS, SEVERITY)),
    Report(0x09, "Severity of one DTC", (DTC,)),
    Report(0x0A, "Supported DTCs"),
    Report(0x0B, "First failed DTC"),
    Report(0x0C, "First confirmed DTC"),
    Report(0x0D, "Most recent failed DTC"),
    Report(0x0E, "Most recent confirmed DTC"),
    Report(
        0x0F,
        "Mirror memory DTCs by status mask",
        (STATUS,),
        "withdrawn in the 2020 edition; set Standard to 2013 or 2006 to send it",
    ),
    Report(0x10, "Mirror memory extended data by DTC", (DTC, EXTENDED)),
    Report(
        0x11,
        "Number of mirror memory DTCs by status mask",
        (STATUS,),
        "withdrawn in the 2020 edition; set Standard to 2013 or 2006 to send it",
    ),
    Report(0x12, "Number of OBD DTCs by status mask", (STATUS,)),
    Report(0x13, "OBD DTCs by status mask", (STATUS,)),
    Report(0x14, "Fault detection counters"),
    Report(0x15, "DTCs with permanent status"),
    Report(0x16, "Extended data by record number", (EXTENDED,)),
    Report(0x17, "User memory DTCs by status mask", (STATUS, MEMORY)),
    Report(0x18, "User memory snapshot by DTC", (DTC, SNAPSHOT, MEMORY)),
    Report(0x19, "User memory extended data by DTC", (DTC, EXTENDED, MEMORY)),
    Report(
        0x1A,
        "Supported extended data records",
        (),
        "udsoncan sends this without the record number the 2020 edition asks for",
    ),
    Report(0x42, "WWH-OBD DTCs by mask record", (GROUP, STATUS, SEVERITY)),
    Report(0x55, "WWH-OBD DTCs with permanent status", (GROUP,)),
    Report(
        0x56,
        "DTCs by readiness group",
        (),
        "udsoncan sends this without the readiness group identifier",
    ),
)

BY_SUBFUNCTION = {report.subfunction: report for report in REPORTS}

#: The eight status bits of ISO 14229-1, low bit first.  Worth spelling out:
#: the mask is the difference between "every fault this ECU has ever seen" and
#: "the ones that are wrong now".
STATUS_BITS = (
    "0x01 testFailed",
    "0x02 testFailedThisOperationCycle",
    "0x04 pendingDTC",
    "0x08 confirmedDTC",
    "0x10 testNotCompletedSinceLastClear",
    "0x20 testFailedSinceLastClear",
    "0x40 testNotCompletedThisOperationCycle",
    "0x80 warningIndicatorRequested",
)

#: Editions udsoncan can encode to.  It matters here because the 2020 edition
#: withdrew the mirror memory reports, and udsoncan enforces that.
STANDARDS = (2006, 2013, 2020)
DEFAULT_STANDARD = 2020
