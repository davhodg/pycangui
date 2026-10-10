[&larr; Contents](manual.md)

# UDS

The **UDS** pane talks ISO 14229 over ISO-TP (udsoncan + can-isotp): set the
tester/ECU ids, Open, then sessions, SecurityAccess (the seed-to-key algorithm
is [`hooks/uds.py::security_key`](hooks.md)), tester present, DID read/write, DTC read and
clear, routines, ECU control and raw requests. Sessions, identifiers and routines are chosen
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

**How the pane is laid out.** Two boxes stay in view whatever else is open,
because every request depends on them: **ECU config** -- which ECU, and how the
transport reaches it -- then **Session and security**.
Below them are four tabs: **DIDs, routines and raw**, **DTCs**, **ECU control**
and **Transfer**, which remembers the tab it was left on. The pane's log is below
the tabs and shared by all of them, since a transfer's lines and a DID read's
are one conversation with the ECU.

## Connecting and the everyday services

**Addressing** says how the identifiers are arrived at. *Identifiers* is the
plain way: type the request, response and functional ids, anything from three
hex digits to eight, and nothing is worked out for you. *J1939* is
ISO 15765-2 normal fixed addressing, which is how UDS is done on a J1939 bus:
give the ECU's 8-bit address and your own, and the identifiers follow --
`18DA<ecu><tester>` for a request, the two addresses the other way round for
the answer, and `18DB<target><tester>` for a functional one, with *Func TA*
the target (`33` is OBD's). The identifiers are still shown, as one line beside
the addresses -- request / response / functional -- so they can be compared
against a trace, and selected and copied; they are not typed there.

Nothing asks whether these are 29-bit identifiers: an identifier above `7FF`
is one, and an identifier below it is not. A tick box as well would be a
second answer to the same question, and the two could disagree.

If the [J1939](j1939.md) pane has claimed an address, that is the address this
pane sends from -- one tool on the bus rather than two testers.

Everything in this box is written down as each box is finished with, rather
than when a session opens. An identifier cleared on purpose stays cleared at
the next start, instead of the default coming back in its place as though
nobody had said anything.

### To every ECU at once

**Functional** chooses which services go to every ECU on the bus, on the
functional address, rather than to the one ECU above -- a tick each, since the
usual thing is a mixture. Before a flash, for instance, tester present,
CommunicationControl and DTC setting go to all of them and everything else to
the ECU being flashed; those three and the baud rate change are ticked to
start with. The button says how many are ticked. The services that can go this
way are the session change, ECU reset, clearing DTCs, CommunicationControl,
tester present, DTC setting, the baud rate change, and a raw request that fits
in one frame; security, DIDs, DTC reports, routines and transfers always go to
the one ECU, since they are one ECU's business or answered in more frames than
a functional request can take.

A functional request is one frame, as ISO 15765-2 requires -- nobody could send
flow control for a message addressed to everybody -- with the positive answer
suppressed where the service allows it, so what comes back is which ECU
objected. The log line says who agreed, who refused and with which code, and
when nobody objected. An ECU that does not serve a request sent to all of them
keeps quiet rather than refusing it, so silence is not a failure. On a J1939 bus
every ECU's answer is recognised by its address; with typed identifiers, only
the response identifier above is -- and, with the functional identifier `7DF`,
OBD's `7E8` to `7EF`.

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

**Tester present** sends TesterPresent every couple of seconds -- to every ECU,
as `3E 80` with no answer expected, when it is ticked under *Functional*. Without it an ECU drops
back to the default session after a few seconds of quiet, and loses any unlock
with it. If the adapter will not send it -- its transmit queue full, because
nothing on the bus is acknowledging frames, as while the ECU restarts -- the box
unticks itself and the Event Log says why, rather than it going on failing every
couple of seconds. Tick it again once the ECU is back.

**Timing** says how long a request waits for its answer. An ECU gives its own
P2 (the answer) and P2\* (the answer after a 0x78, response pending) when a
session starts, and the log line for the session says what they were.

- *ECU's*, the default, keeps to them: an answer later than the ECU promised is
  a timeout, which is what testing an ECU wants. Until a session has given
  them, the P2 and P2\* boxes are used.
- *At least* waits for the longer of the ECU's values and the boxes. It is for
  a bootloader that takes longer than it says -- 447 ms to answer the end of a
  transfer, with 50 ms promised, while it finishes writing flash.
- *Forced* uses the boxes and nothing else, shorter or longer.

A change applies from the next request, on a session that is already open too.
Every request waits until its last frame has gone before P2 starts, so a long
TransferData is not timed out while it is still being sent.

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

The **ECU control** tab is three things that change how the ECU behaves on the
bus, each in a frame named for its service and number: *ECUReset (0x11)*,
*CommunicationControl (0x28)* and *LinkControl (0x87)*.
**Reset** restarts it with the chosen type, and the session and any unlock go
with it. **Communication** is CommunicationControl (0x28): whether the ECU
sends and listens, for normal messages, network management or both --
disabling Tx of normal messages is how a flash is usually made quiet for the
rest of the bus, and the ECU puts it back itself when the session ends.
**Check** asks whether the bitrate chosen is possible and stops there: the
first half of a change, with nothing changed and the channel left where it is.

**Change** is the whole of LinkControl (0x87), and it goes to every ECU unless *Baud
rate change* is unticked under *Functional*: an ECU left at the old rate sees
nothing but errors from the rest. Each ECU is asked whether it can move to the
chosen rate, and nothing changes if any refuses or none answers; then they are
told to, and the channel follows -- it is closed and opened again at the new
rate, the session is reopened, and tester present carries on at once, since an
ECU whose session times out falls back to its own rate. The other panes, and a
recording, see the channel disconnect and connect. The new rate is not saved as
the channel's own; the bitrate in the top row shows the rate in use while the
channel runs at it, its tooltip says so, and it goes back to the channel's own on
disconnect.

There is no request to put it back: the new rate lasts for the session it was
set in. So while the channel is away from its own rate, a **Back to ...** button
beside *Change* ends the session -- for every ECU, if the change went to every
ECU -- and reopens the channel at the rate it had before.

On a channel whose bitrate pycangui does not set, the baud rate change sends
nothing and says why: socketcan's rate is the kernel's, from `ip link`, so the
channel could not follow.

Communication and Baud rate both ask first, since other nodes can stop
hearing the ECUs, and so does Reset when it goes to every ECU.

**Read** and **Write**, in the DID frame, work on the identifier beside
them. A routine has **Start**, **Stop** and **Result** -- RoutineControl
sub-functions 1, 2 and 3 -- and the option bytes beside them are sent with
whichever you press.

## DTCs

**DTCs** have a tab to themselves, because ReadDTCInformation (0x19) is
twenty-odd reports wearing one service number. Choose the report and the
boxes below light up according to what it takes -- status mask, severity, a
DTC number, a record number, a user memory, a WWH-OBD functional group -- since
an ECU answers the wrong parameters with NRC 0x13 and no explanation. Which
report takes what is checked against the bytes udsoncan actually builds, so
the pane cannot disagree with what is sent. *DTC setting on* is ControlDTCSetting
(0x85): untick it so that working on a vehicle does not leave faults behind,
remembering that the ECU turns it back on itself when the session ends.
*Clear* takes a group, so it need not be all of them. *Standard* is the
edition of ISO 14229-1 requests are built to; it matters here because the 2020
edition withdrew the mirror memory reports.

**Read all** asks for everything the ECU holds about its faults and writes it
to the log as one report, a part at a time: how many DTCs match the status
mask and which, each one's extended data and severity, which snapshots there
are and each of them, then the first and most recent failed and confirmed
DTCs, the fault detection counters -- faults building up that have not failed
yet -- and the permanent DTCs, which clearing cannot remove. *Supported DTCs
too* adds every DTC the ECU knows of, which can run to hundreds of lines. A
report the ECU does not offer is one line saying so, and the rest carries on;
severity, which many ECUs do not support, is asked about once rather than for
every DTC.

**Extended data and snapshots** are the two reports whose contents the ECU
sizes. ISO 14229-1 numbers extended data records and says nothing about how
long they are, and an answer carrying several runs them together, so they are
split and named by
[`hooks/uds.py::extended_data_record`](hooks.md) -- fill in
`EXTENDED_DATA_RECORDS` with each record's size and name. A record it does not
know is shown as bytes from there on rather than guessed at. A snapshot holds
DIDs, the same ones *Read* in the DID frame reads, so they are named and decoded the same
way. The one thing a snapshot does not carry is how long each value is.
[`hooks/uds.py::did_size`](hooks.md) is asked first -- fill in `DID_SIZES` --
and failing it pycangui reads the DID once to find out, keeping the answer for
the session. The hook is for a DID the ECU will not read in the session you
are in, or one that only ever appears in a snapshot; without either, the rest
of the snapshot is shown as bytes.

## Firmware transfer

The **Transfer** tab moves firmware, in either direction. *Download* sends an
Intel HEX, S-record or raw binary to the ECU (0x34, a TransferData per block,
then 0x37); *Upload* reads memory back into a file of whichever of those
formats the name you choose asks for. The rest of the list are RequestFileTransfer
(0x38) operations -- add, replace, resume, read, delete, list a directory -- which
name a file on the ECU's own filesystem instead of an address.

A hex or S-record file carries its own addresses, so the address box fills itself
in and stays locked: the file is right, and a typed number could only be wrong.
A raw binary carries none, so for one of those the address has to be supplied.
In Intel HEX, a line that does not start with `:` -- a comment or a title some
tools add -- cannot be a record, so it is passed over, and the Transfer log
says how many were.
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
from 0x0200 to 0xDFFF is manufacturer specific. The 0x0202 suggested for the
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

The rest of the tab, top to bottom. **Operation** chooses the transfer.
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
written the one pycangui needs. **Seed and key DLL...**, on the row under Unlock, is where
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
