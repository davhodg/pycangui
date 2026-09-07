[&larr; Contents](manual.md)

# UDS

The **UDS** pane talks ISO 14229 over ISO-TP (udsoncan + can-isotp): set the
tester/ECU ids, Open, then sessions, SecurityAccess (the seed-to-key algorithm
is [`hooks/uds.py::security_key`](hooks.md)), tester present, DID read/write, DTC read and
clear, routines, ECU reset (its own box, since it interrupts whatever the ECU
was doing) and raw requests.  Sessions, identifiers and routines are chosen
from dropdowns of the ones somebody has a name for -- ISO 14229-1's, plus
whatever you add to `DID_NAMES`, `ROUTINE_NAMES` and `SESSION_NAMES` in
`hooks/uds.py` -- and every one stays typeable, because most of the
identifiers and all of the interesting routines on a real ECU are
manufacturer specific and will never be on anybody's list.  Hovering an entry
gives its description, which is where "17 characters (ISO 3779)" lives.  Data identifiers are named from ISO 14229-1 --
`F190 (VIN)` -- and negative responses by their standard code name.  The demo device answers on
0x7E0/0x7E8 with a byte-invert key.  *Send raw* is the escape hatch: type the
bytes of a request -- service id first, then whatever that service expects --
and they go out as they are, for the services with no button of their own and
for reproducing a sequence out of a trace or a specification.

## DTCs

**DTCs** have a box to themselves, because ReadDTCInformation (0x19) is
twenty-odd reports wearing one service number.  Choose the report and the
boxes below light up according to what it takes -- status mask, severity, a
DTC number, a record number, a user memory, a WWH-OBD functional group -- since
an ECU answers the wrong parameters with NRC 0x13 and no explanation.  Which
report takes what is checked against the bytes udsoncan actually builds, so
the pane cannot disagree with the wire.  *DTC setting on* is ControlDTCSetting
(0x85): untick it so that working on a vehicle does not leave faults behind,
remembering that the ECU turns it back on itself when the session ends.
*Clear* takes a group, so it need not be all of them.  *Standard* is the
edition of ISO 14229-1 requests are built to; it matters here because the 2020
edition withdrew the mirror memory reports.

## Firmware transfer

Its **Transfer** box moves firmware, in either direction.  *Download* sends an
Intel HEX, S-record or raw binary to the ECU (0x34, a TransferData per block,
then 0x37); *Upload* reads memory back into a file of whichever of those
formats the name you choose asks for.  The rest of the list are RequestFileTransfer
(0x38) operations -- add, replace, resume, read, delete, list a directory -- which
name a file on the ECU's own filesystem instead of an address.

A hex or S-record file carries its own addresses, so the address box fills itself
in and stays locked: the file is right, and a typed number could only be wrong.
A raw binary carries none, so for one of those the address has to be supplied.
Gaps are left as gaps -- a file with a hole in it gets a RequestDownload each side
of it rather than being padded, because padding would write bytes the file never
contained over whatever the ECU had there.  A bootloader that wants one
contiguous block should be given a contiguous file.  Blocks are sized from the
ECU's own `maxNumberOfBlockLength`, less the two bytes the service id and the
block counter take out of it; both that and the address width can be overridden
for bootloaders that insist.

A download is rarely just a download.  **Erase first** runs RoutineControl
0xFF00 over every segment before the first one is written -- all of them first,
not each before its own download, because two segments can share a flash block
and erasing between them would take the first one back out again.  **Check
after** runs a routine once each segment has been sent, so the ECU can check
what it was given.  Only the erase is standardised: ISO 14229-1 Annex F names
four routines in all (erase memory 0xFF00, check programming dependencies
0xFF01, erase mirror memory DTCs 0xFF02, deploy loop 0xE200) and everything
from 0x0200 to 0xDFFF is manufacturer specific.  The 0x0202 offered for the
check is the number the HIS/AUTOSAR bootloaders settled on rather than a
standard, so it is editable.  What either routine is *sent* comes from
`hooks/uds.py::erase_options` and `::check_options`, which default to an
address and length in the ISO format; a bootloader wanting a CRC of what it
was given is a couple of lines there.
