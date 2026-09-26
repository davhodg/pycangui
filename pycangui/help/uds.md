[&larr; Contents](manual.md)

# UDS

The **UDS** pane talks ISO 14229 over ISO-TP (udsoncan + can-isotp): set the
tester/ECU ids, Open, then sessions, SecurityAccess (the seed-to-key algorithm
is [`hooks/uds.py::security_key`](hooks.md)), tester present, DID read/write, DTC read and
clear, routines, ECU reset (its own box, since it interrupts whatever the ECU
was doing) and raw requests. Sessions, identifiers and routines are chosen
from dropdowns of the ones somebody has a name for -- ISO 14229-1's, plus
whatever you add to `DID_NAMES`, `ROUTINE_NAMES` and `SESSION_NAMES` in
`hooks/uds.py` -- and every one stays typeable, because most of the
identifiers and all of the interesting routines on a real ECU are
manufacturer specific and will never be on anybody's list. Hovering an entry
gives its description, which is where "17 characters (ISO 3779)" lives. Data identifiers are named from ISO 14229-1 --
`F190 (VIN)` -- and negative responses by their standard code name. The demo device answers on
0x7E0/0x7E8 with a byte-invert key. *Send raw* is the escape hatch: type the
bytes of a request -- service id first, then whatever that service expects --
and they go out as they are, for the services with no button of their own and
for reproducing a sequence out of a trace or a specification.

## Connecting and the everyday services

**Addressing** says how the identifiers are arrived at. *Identifiers* is the
plain way: type the request, response and functional ids, anything from three
hex digits to eight, and nothing is worked out for you. *J1939 addresses* is
ISO 15765-2 normal fixed addressing, which is how UDS is done on a J1939 bus:
give the ECU's 8-bit address and your own, and the identifiers follow --
`18DA<ecu><tester>` for a request, the two addresses the other way round for
the answer, and `18DB<target><tester>` for a functional one, with *Func TA*
the target (`33` is OBD's). The identifiers are still shown, so they can be
compared against a trace, but they are not typed there.

Nothing asks whether these are 29-bit identifiers: an identifier above `7FF`
is one, and an identifier below it is not. A tick box as well would be a
second answer to the same question, and the two could disagree.

If the [J1939](j1939.md) pane has claimed an address, that is the address this
pane sends from -- one tool on the bus rather than two testers.

Everything in this box is written down as each box is finished with, rather
than when a session opens. An identifier cleared on purpose stays cleared at
the next start, instead of the default coming back in its place as though
nobody had said anything.

**Open** makes the ISO-TP connection on those identifiers, and nothing
else on the pane works until it is open. **Pad** is the byte every frame is
filled out to 8 bytes with, for the ECUs that ignore anything shorter -- `00`
unless you say otherwise, and empty for no padding at all. **Transport** picks
the ISO-TP implementation -- your own can be added as a [component](components.md).
On a CAN FD channel **CAN-DL** and **BRS** sit beside them, as
[CAN adapters and channels](channels.md) describes.

**Change** moves the ECU into the session chosen beside it. **Unlock** runs
SecurityAccess at the **Level** beside it: the ECU hands over a seed, and
pycangui answers with the key from `hooks/uds.py::security_key`, or from the
seed and key DLL described below where that hook returns None.

**Tester present** sends TesterPresent every couple of seconds. Without it an ECU drops
back to the default session after a few seconds of quiet, and loses any unlock
with it.

**Level is a level**, counting from 1, and beside it pycangui shows the two
sub-functions that will actually go out -- `req 03  resp 04` for level 2.
SecurityAccess works in pairs: an odd sub-function asks for the seed and the
even one after it carries the key, so level 1 is 01 and 02, level 2 is 03 and
04, and level 9 is 11 and 12. An ECU document that quotes a sub-function
rather than a level is naming the request half of one of those pairs.

The box holds the level rather than the sub-function because nothing is lost
by it: udsoncan normalises whatever it is given to the odd request and its
even answer, so an unpaired combination cannot be sent anyway. Levels run to
63, the last pair being 7D and 7E. The log names both, so what went on the
wire is never inferred: `Security level 2 (req 03 resp 04): unlocked`.

**Reset** restarts the ECU with the chosen **Type**, and the session and any
unlock go with it. **Read DID** and **Write DID** work on the identifier beside
them. A routine has **Start**, **Stop** and **Result** -- RoutineControl
sub-functions 1, 2 and 3 -- and the option bytes beside them are sent with
whichever you press.

## DTCs

**DTCs** have a box to themselves, because ReadDTCInformation (0x19) is
twenty-odd reports wearing one service number. Choose the report and the
boxes below light up according to what it takes -- status mask, severity, a
DTC number, a record number, a user memory, a WWH-OBD functional group -- since
an ECU answers the wrong parameters with NRC 0x13 and no explanation. Which
report takes what is checked against the bytes udsoncan actually builds, so
the pane cannot disagree with the wire. *DTC setting on* is ControlDTCSetting
(0x85): untick it so that working on a vehicle does not leave faults behind,
remembering that the ECU turns it back on itself when the session ends.
*Clear* takes a group, so it need not be all of them. *Standard* is the
edition of ISO 14229-1 requests are built to; it matters here because the 2020
edition withdrew the mirror memory reports.

## Firmware transfer

Its **Transfer** box moves firmware, in either direction. *Download* sends an
Intel HEX, S-record or raw binary to the ECU (0x34, a TransferData per block,
then 0x37); *Upload* reads memory back into a file of whichever of those
formats the name you choose asks for. The rest of the list are RequestFileTransfer
(0x38) operations -- add, replace, resume, read, delete, list a directory -- which
name a file on the ECU's own filesystem instead of an address.

A hex or S-record file carries its own addresses, so the address box fills itself
in and stays locked: the file is right, and a typed number could only be wrong.
A raw binary carries none, so for one of those the address has to be supplied.
Gaps are left as gaps -- a file with a hole in it gets a RequestDownload each side
of it rather than being padded, because padding would write bytes the file never
contained over whatever the ECU had there. A bootloader that wants one
contiguous block should be given a contiguous file. Blocks are sized from the
ECU's own `maxNumberOfBlockLength`, less the two bytes the service id and the
block counter take out of it; both that and the address width can be overridden
for bootloaders that insist.

A download is rarely just a download. **Erase first** runs RoutineControl
0xFF00 over every segment before the first one is written -- all of them first,
not each before its own download, because two segments can share a flash block
and erasing between them would take the first one back out again. **Check
after** runs a routine once each segment has been sent, so the ECU can check
what it was given. Only the erase is standardised: ISO 14229-1 Annex F names
four routines in all (erase memory 0xFF00, check programming dependencies
0xFF01, erase mirror memory DTCs 0xFF02, deploy loop 0xE200) and everything
from 0x0200 to 0xDFFF is manufacturer specific. The 0x0202 offered for the
check is the number the HIS/AUTOSAR bootloaders settled on rather than a
standard, so it is editable. What either routine is *sent* comes from
`hooks/uds.py::erase_options` and `::check_options`, which default to an
address and length in the ISO format; a bootloader wanting a CRC of what it
was given is a couple of lines there.

**Refusing an image that is not for this ECU.** Nothing in a firmware file
says which controller it belongs to, and writing the right file to the wrong
one is the most expensive mistake available here.
[`hooks/uds.py::before_download`](hooks.md) is called once with the image and
the open session, before a single byte is erased or written: read a part
number, a hardware revision or the current session out of the ECU, compare it
with what the file is, and return a reason to stop. The transfer then says
*REFUSED* and names the hook, rather than reporting a failure -- nothing went
wrong, something was prevented. Return None and it goes ahead.

The rest of the box, left to right. **Operation** chooses the transfer.
**Block** is how many data bytes go in each TransferData; left at *from ECU*,
the ECU's own maximum is used. **Width** is how many bits the address and size
are written in, where *auto* uses the narrowest that fits. **DFI** is the
dataFormatIdentifier, and 00 -- plain bytes -- is what nearly every bootloader
wants. **Size** is how much an upload reads, and **On ECU** is the file's name
for the RequestFileTransfer operations.

Anything that writes to the ECU asks first, once a session. **Cancel** stops
after the block being sent at the time, because the ECU is waiting for the
TransferData it has already been promised and stopping part way through one
would leave the two of you out of step.

## The seed and key DLL

There is no standard unlock *algorithm* -- only a few established ways of
shipping one as a Windows DLL, and pycangui calls whichever the DLL exports:

| Function | UDS | XCP | CCP |
|---|---|---|---|
| `GenerateKeyEx` -- the usual way a UDS algorithm is delivered | 1st | 2nd | 3rd |
| `XCP_ComputeKeyFromSeed` -- from the XCP standard | 2nd | 1st | 2nd |
| `ASAP1A_CCP_ComputeKeyFromSeed` -- from the CCP specification | | | 1st |

So a maker who has written a seed and key DLL for another tool has already
written the one pycangui needs. **Seed and key DLL...** beside Unlock is where
it is named, and its **Check the DLL** says which function each protocol will
use.

Each protocol uses the first of these its DLL exports. For UDS,
`GenerateKeyEx` is given the **level number** -- 1 for sub-functions 0x01
and 0x02, 2 for 0x03 and 0x04 -- and `XCP_ComputeKeyFromSeed` the
requestSeed sub-function itself, 0x03 for level 2. XCP and CCP have no
levels, so `GenerateKeyEx` is given 0 for them, and `XCP_ComputeKeyFromSeed`
the resource being unlocked. The variant is always empty. A DLL that does
not know a level says so, and nothing is sent to the ECU.

pycangui asks in this order, and stops at the first answer:

1. the hook -- `hooks/uds.py::security_key` for UDS, `hooks/xcp.py::compute_key`
   for XCP;
2. the DLL, if the hook returned None;
3. otherwise it reports that unlocking is not possible, and says both of the
   places an answer could have come from.

The hook comes first so a workspace can always override, and so a level or a
resource the DLL does not cover can be answered in a few lines of Python:
return a key for the ones you know and None for the rest, and each is dealt
with by whatever can.

**One DLL serves both panes.** An ECU ships one algorithm; UDS SecurityAccess
and XCP or CCP unlocking are the same question asked by different protocols.
Naming the file in either pane names it for both.

### When the DLL is 32-bit

It usually is. These DLLs are generally built 32-bit and handed out that way
even to 64-bit tools, because **no process can load a library of the other
bitness**. There is no flag for it and no setting: a 32-bit DLL and a 64-bit
program cannot share an address space.

What every tool does instead is run the DLL in a helper process of the right
bitness and pass the key back. pycangui does the same: it reads the bitness out
of the file, and where it does not match, runs the DLL through another Python
interpreter -- found through the `py` launcher, or named in the dialog. The
dialog says which of these will happen **when the DLL is chosen**, rather than
leaving it to be discovered while somebody is trying to unlock an ECU.

If there is no interpreter of the right bitness on the machine, there are three
ways out and the dialog names all three: install one, point pycangui at one, or
rebuild the DLL. Rebuilding is the tidiest where the source is yours -- these
projects generally carry a Win32 configuration only, so an x64 one needs
adding.
