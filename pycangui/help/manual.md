# pycangui manual

How to drive the panes.  What pycangui *is*, how to install it and what it is
built from are in the README that comes with the source; this is the reference
for using it.

Everything in it is reachable from the application itself: **Help >
Documentation**.

## Without hardware

Without hardware: pick interface `virtual`, channel **vcan0 (CANopen demo
device)**, and press Connect -- the channel is the switch, so there is nothing
else to turn on.  `vcan1` and `vcan2` are empty loopbacks, for replaying a log
onto or sending your own frames.  A simulated node 5 appears in the
CANopen pane: its EDS is matched automatically, the object dictionary can be
read (double-click) and written (edit the value), NMT Start makes it transmit
TPDO1, and writing *Speed demand* (0x2001) moves the motor speed in the PDO.

## Your files

Everything in `%APPDATA%\pycangui` is yours.  Most of it lives in a
*workspace* -- `workspaces\default\` unless you make others -- which holds
`hooks/*.py`, small Python functions pycangui calls at decision points (which
EDS to use for a node, how to name it, ...) with the defaults and commented
examples in place; `eds/`, scanned for EDS files matching a node's
vendor/product; `settings.json`; and `layout.json`.  Tools > Reload hooks
applies edits without a restart.

`settings.json` is sorted, indented JSON with dotted keys, meant to be read and
hand-edited: the channels and their adapters, the databases loaded, the
transmit list, which trace groups are hidden, the trace view mode, the plot
window, the UDS and XCP addresses, whether DBC checks are strict.  Settled
choices are kept; passing state -- a search box, a paused view, the selected
row -- is not, because starting up paused would be a bug rather than a
convenience.  `layout.json` holds the dock arrangement, which is Qt's own
opaque data rather than anything to read; *View > Reset layout* puts it back.

Outside the workspaces are the things that belong to the machine rather than
to what you are working on: `backends/`, which is about being able to talk to a
bus at all, and the window's position on screen, which stays in `QSettings`.

### Where file dialogs open

A file dialog opens where you last used **that sort of file**.  Open an EDS and
the next EDS dialog starts where that one was; a firmware image, a captured log
and a CAN database each keep their own folder, so picking one does not move the
others.  The folders live in the workspace, since which folder a product's
files are in is a fact about that product and should not follow you into the
next one.

A remembered folder that no longer exists -- a memory stick unplugged, a folder
deleted -- is ignored, and the dialog opens at its default instead.  *Tools >
Forget remembered folders* puts every one of them back to pycangui's own
folders at once.

## Workspaces

A workspace is everything about the product you are working on: the hooks that
say what its objects mean, its EDS files, its channels and bitrates, its
databases, its transmit list, its watch lists and its pane arrangement.  One
folder, so it can be copied, backed up or handed to a colleague whole.

**If you only ever work on one thing, you can stop reading here.**  There is a
workspace called `default`, it was made without asking, and everything above
describes it.  The title bar names a workspace only when it is *not* `default`,
so nothing on screen will ever mention this.

There is no Save and no unsaved changes: a workspace saves as you go, the way
settings always have.  *File > Workspace* has three items.

- **Save as...** keeps everything as it is now under a new name and carries on
  in that one.  It is a fork -- the workspace you were in is left exactly as
  you left it -- because what somebody means by it is "keep this and call it
  something else".
- **Switch to** opens another one.  Everything reloads, including the channels:
  a workspace holds which adapter at what bitrate, and an adapter can only be
  in one of those states at a time.  Switching while connected asks first,
  since it means dropping off the bus and stopping anything being sent
  cyclically.
- **Manage...** renames and deletes.  Not `default`, which is the one that is
  always there, and not the one you are in -- switch away first, so the ground
  does not move under the window.

Only one workspace is open at a time.  When it feels like you want two, what
you want is usually two *panes*: *View > New pane* gives a second trace or a
second plot side by side, within one workspace.

## Panels

The object dictionary lists one object per row sorted by index, which is the
right way to *find* an object and the wrong way to *use* one.  A panel is a
named group of them laid out as a form: the six parameters a job actually
needs, labelled, with the units on them, in one place.

**Building one takes no code.**  In the CANopen pane, select the objects in the
object dictionary -- several at once -- right-click, and *Add to panel*.  Pick
an existing panel or make a new one; you are asked what to call it once, not
once per object.  *View > Panels* opens any panel you have, and *New panel...*
makes an empty one.

A panel's own *Edit...* has **Add...** too, which matters when the object
dictionary is not the way in: building a panel at a desk against a DCF or EDS
with no bus present, or adding an object whose index you already have in front
of you.  It searches whatever the panel is bound to, and takes a typed index
either way -- a typed one that turns out to be in the file arrives named, the
same as a picked one.

A panel opens in a window of its own, in front of the main one.  It is a dock
like any other pane, so drag it in to dock it, drop it onto another to tab
them, or leave two out side by side comparing two nodes -- which is what they
are for.  Wherever you leave one is where it opens next time.

### Where the values come from

*Values from* at the top of a panel chooses a node on the bus, or a DCF or EDS
file.  A panel is a statement about a *product*; which controller you point it
at this afternoon is not, so it is not in the file.  Pointing one at a file is
how a configuration is built at a desk and taken to the machine: edits change
the file in memory, and saving writes a DCF through the original text so its
comments survive.

### Polling

*Read* reads the panel once.  **Poll** reads it over and over, so the values
follow the controller -- which is the only way to watch an object that is not
mapped to a PDO.  The box beside it is how often, at most.

**The figure after the box is the rate actually achieved, not the one you
asked for**, and the difference is the point.  An SDO read is a request and a
response on the bus against a controller that answers when it feels like it, so
a panel of twelve objects at 50 Hz is asking for six hundred round trips a
second and will not get them.  When the two agree it just shows the rate; when
they do not it says so -- `12.0 Hz (asked for 50)` -- because a value read at
12 Hz that looks like it was read at 50 is the sort of thing conclusions get
built on.

A round only starts once the last one has finished, so asking for more than the
bus can do gets you as fast as it can rather than a growing backlog of stale
values.  A box you are typing in is not overwritten by an arriving value.

While polling, every numeric field is pushed to **Signals and Plot** under the
source's name, so a polled object plots and exports to CSV like any other
signal.  Reading once by hand does not, since a series of one point would only
fill the signal list.

Polling is offered against a node and not against a file: a file does not
change while you watch it.

### How an object is shown

Each field has one of seven kinds, changed in *Edit...* on the panel:

| Kind | What it is |
|---|---|
| `value` | read-only, shown in whatever units it is understood in |
| `number` | typed in its own units, refused if the EDS says it is out of range |
| `hex` | the same, in hex, for codes and masks rather than quantities |
| `enum` | a dropdown of the values that have names |
| `flags` | one named tick per bit |
| `bits` | a field packed into some of the bits of a larger object |
| `map` | an array as an editable table beside its graph |

Names, units, scaling, limits and bit meanings come from the EDS where it
carries them and from `hooks/canopen.py::object_display` where it does not --
the same place the object dictionary gets them, so a panel and the tree agree.

Two things a panel will not do.  It will not send a number outside the limits
the EDS declared, because a node is free to clamp it silently and a parameter
that did not take is worse than one that was not sent.  And a `flags` or `bits`
field will not write until it has read: those write part of an object, part of
an object cannot be written, and a word made mostly of zeroes would clear
every bit the panel is not showing.

Panels are JSON in `workspaces\<name>\panels\`, beside the hooks, and meant to
be edited: named bits, map axes and anything else the dialog does not cover
are a line in the file.  A panel with something wrong in it still opens and
says what -- refusing would leave nobody able to see which field it was.

## About, licences and updates

**Help > About** shows the version alongside the Python, Qt, python-can and
canopen versions in a form you can copy into a bug report; **Help > Licences**
shows pycangui's own Apache-2.0 licence, the NOTICE attributions and the full
third-party licence text, all shipped with the application.  **Help > Check
for updates** asks GitHub whether there is a newer release -- only when you
pick it: pycangui makes no network connection of its own accord, sends nothing
about your machine, and downloads nothing.  A newer version just offers to
open the releases page.

## Panes and layout

pycangui opens with three panes: the **Trace** with the **Event Log** beside
it, and **Signals and Plot** spanning underneath.  The rest (CANopen, UDS, J1939,
XCP, Transmit, Python Console) start hidden, because which of them you want
depends on what you have plugged in; turn any on in the **View** menu, and
*View > Reset layout* puts everything back.  Panes are dockable, so drag them
where you like: the arrangement is remembered.

Drag a pane out of the window, or double-click its title bar, and it floats.
Hold **Ctrl** while dragging one, or it will dock again at the first
opportunity -- the main window is looking for somewhere to put it the whole
time.  Drag it back to dock it, or use *View > Dock all panes*.

A pane that is out grows two buttons at its top right, and loses them again
when it goes back.  Each says what pressing it will do, so **Pin** becomes
**Unpin** and **Detach** becomes **Attach**; hover for what they mean.

Pinning keeps a pane above every other window, pycangui's and everyone
else's -- except while pycangui is asking a question, when a pinned pane
stands down so the dialog can be seen and answered, and goes back on top
afterwards.  Detaching takes it out of its dock altogether, into a window with no
parent: nothing then tries to dock it, it gets a taskbar entry of its own, and
it can be sent to another display and left there.  Attaching puts it back
where it came from -- floating if that is where it was, docked if not --
while closing that window closes the pane, as closing a docked one does, and
*View* shows it again.  *View > Dock all panes* gathers everything up.

Which panes are out on their own, and which are pinned, are remembered like
the rest of the settings: a pane left on a second monitor is still there next
time.

### More than one of a pane

*View > New pane* opens a second **Trace** or **Signals and Plot**, numbered
after the first -- Trace 2, Trace 3.  Each has settings of its own: its own
filter, its own mode, its own plotted signals, all remembered separately.  Two
traces of the same capture filtered differently, or a plot per subsystem, are
what this is for.

A pane you open arrives **in its own window**, in front of the main one, rather
than squeezing into the space the panes already on screen were using.  Drag it
into the main window to dock it, or onto another pane to tab the two together;
wherever you leave it is where it opens next time.  Otherwise it behaves like
any other pane -- pin it, detach it, close it.

Closing one puts it away and *View* brings it back, exactly as for the panes
that are always there.  *View > Remove pane* is the other thing: it closes one
for good and forgets it.  The first pane of each sort cannot be removed --
that one is the pane.

Controls whose effect is not written on them explain themselves on hover: the
UDS service behind a button, what a routine's Start actually sends, that
clearing DTCs takes the freeze frames with them, that an RPDO needs the node's
configuration read first.  The obvious ones -- Clear, Remove, Connect -- are
left alone, since a tooltip repeating its label is noise.

## Databases

**File > Load DBC...** decodes matching frames.  Databases are checked
strictly, and one that fails the check -- overlapping signals, a signal past
the end of its message, both common in files real tools produce -- is offered
for loading anyway rather than simply refused; turning off *Tools > Strict DBC
checks* stops the asking.  Signals with a `VAL_` table can
be transmitted by name or by number, and the names are listed in the tooltip.

Once loaded: the trace shows the message
name and the **Signals and Plot** pane lists every signal with its live value.
CANopen TPDO values appear there too.  Tick *Plot* on any signal to draw it
alongside the list (rolling window, pause, follow); drag the splitter to give
the plot the whole pane, or the list.  `resources/demo.dbc` matches the demo
device.

**File > Export signals...** writes what has been decoded to a CSV: DBC
signals, CANopen PDO values and XCP measurements alike.  Recording writes raw
CAN, which is right for a recording and means decoding it again elsewhere to
get back what is already on screen here.

Each signal gets its own **Time (s)** column beside its values, with a blank
column between signals:

```
Time (s),DBC Engine/Speed (rpm),,Time (s),XCP/current (A)
0.010000,1500,,0.012000,12.4
0.020000,1520,,0.022000,12.6
0.030000,1490,,,
```

One shared time column would be tidier and would be a lie -- signals do not
arrive together, so a single timeline can only be built by interpolating, by
holding the last value, or by inventing a grid, and all three put numbers in
the file that were never on the bus.  A signal that finishes early leaves its
cells empty for the same reason.  The blank column between pairs is also what
stops a spreadsheet reading two signals as one series when you select a block
and ask it for a chart.

## Channels

pycangui talks to several CAN buses at once -- a second port on a multi-channel
adapter, or a second adapter entirely.  Use **+** on the toolbar to add a
channel, then give it its own interface, bitrate and connection; each channel's
settings are remembered by name.

Pick the interface and press **Detect**: pycangui asks it which adapters are
attached and lists them, so the channel is chosen rather than guessed (it is
`can0` on socketcan, `PCAN_USBBUS1` on a PEAK, and plain `0` on an IXXAT).
Detection also runs by itself when you change interface.  It only enumerates
adapters -- nothing is transmitted and no bitrate is applied.

Channel numbers belong to an adapter, so **two identical dongles both offer
channels 0 and 1**.  The list shows each one's hardware id or serial number to
tell them apart, and the whole configuration is used to open the bus -- picking
the second dongle really does connect to the second dongle, not to whichever
the driver enumerated first.  Which one you chose is saved with the channel and
shown in its description.

Where an interface cannot enumerate, opening the list still offers something
rather than nothing: the conventional names for that interface, the default
the backend declares in its own signature (PCAN says `PCAN_USBBUS1`, NI-XNET
says `CAN1`), and for the serial adapters the serial ports actually present.
The box stays editable, so anything typed in is kept.  An interface with no
channel at all shows it empty and greyed.

The status bar shows each channel's state and its **bus load** -- an estimate
from the frames seen and the configured bitrate, including nominal bit
stuffing.  The trace's *Channel* column says which bus a frame came from.

The trace, the recorder and the decoders always see **every** connected
channel, on one shared clock, so an ECU forwarding messages between two buses
can be watched from both sides with comparable timestamps.  The channel picked
in the toolbar is the one the protocol panes (CANopen, UDS, J1939, XCP) work
with; switching channel looks to them like a disconnect and a reconnect.

## Bitrates and CAN FD

Bitrates run from 50 kbit/s to 1 Mbit/s -- 50 and 100 are ordinary on
machinery and marine buses, where a long backbone costs more than speed.
Ticking **FD** adds a **Data** rate beside it, since the arbitration phase
still runs at the bitrate on the left.  python-can has no way to ask an
adapter which rates it supports, and no common way to set an FD data rate
either: only the IXXAT and Vector backends take a `data_bitrate`, socketcan
takes `fd=True` and gets its data rate from `ip link`, and PCAN, Kvaser and
slcan take neither -- FD is a `can.BitTimingFd` to them, which needs the
controller's clock frequency and cannot be worked out from a bitrate.  Where
the choice cannot be sent, the Event Log says so rather than letting the
channel open as classic CAN with FD ticked on screen.

On an FD channel the **UDS** pane offers **CAN-DL** beside the transport:
how many bytes go in one ISO-TP frame.  Eight is all a classic bus can
carry, and the FD lengths -- 12, 16, 20, 24, 32, 48, 64 -- are what makes
running UDS over FD worth the trouble, since at 64 there are eight times
fewer flow control rounds.  **BRS** beside it switches the data phase to
the faster rate; without it an FD frame runs end to end at the arbitration
bitrate and the data rate never gets used.  Both follow the channel rather
than the box that was ticked: a channel that opened classic offers 8 and
nothing else.

## The trace

The trace narrows down in three ways, none of which discard anything: the
**filter box** matches text against the id, the decoded name, the channel and
the data (`185`, `txpdo`, `drive bus`, `de ad`; several words must all match),
the **Filter** menu hides whole protocol groups or channels, and **Pause**
holds the display still while capture and recording carry on.  The row count
next to the buttons reads *shown of captured*.  Select rows and press Ctrl+C
to copy them as text.

## Before it disturbs equipment

pycangui asks before it can disturb equipment that is not its own, once a
session for each: joining a **real bus** (naming the bitrate, because a
controller at the wrong one cannot read a frame and signals an error on every
one it sees, which can drive the working nodes off the bus), **transmitting**
onto one, and **replaying** a log onto one.  A `virtual` channel never asks --
nothing leaves pycangui.  Change the bitrate and the connect question comes
back, since getting it wrong is what the question is for.

## Recording and replay

**Record** and **Replay** sit together on the toolbar.  Record writes every
connected channel to a log file -- `.blf` (Vector binary), `.asc` (Vector
ASCII), `.trc` (PEAK), `.log` (candump), `.csv` or `.db` (SQLite); the format
follows the file extension, and each frame is tagged with the channel it
arrived on.  A recording is not tied to the channel you have selected, so
switching channel, or a channel dropping, leaves it running.

Replay plays a log back onto the selected channel with its original timing.
The arrow beside the button holds the speed (0.1x to 20x), *Loop*, and the
last few files replayed.  There is no separate offline mode: replaying onto a
**virtual** channel feeds the trace, the decoders, the signal hub and the plot
without touching any hardware, which is how a colleague's recording is
examined with nothing attached -- and if nothing is connected when you press
Replay, pycangui offers to create that virtual channel for you.  Replaying
onto a real bus is real traffic, so it asks first.

## Transmit

The **Transmit** pane holds one list of everything being sent, with three kinds
of row: **raw** (type the id and bytes), **DBC** (pick a message from a loaded
database and edit its signals in physical units), and **CANopen RPDO** (pick a
node's RPDO -- press *Read RPDO config* in the CANopen pane first -- and edit
its mapped variables).  All three are behind one *Add* button, since the
choice is which source rather than which button.  Expand a row to see its
signals; the encoded bytes update as you type, and a message that is already
cycling is updated live.

Select several rows -- Ctrl+A takes the lot -- and *Send selected*, *Remove
selected* and the space bar all work on the whole selection, so starting or
stopping a set of cyclic messages is one keypress rather than one tick per row.

## The Event Log

Everything pycangui says goes to the **Event Log** with a level: information,
warning or error.  A warning or an error opens the pane if it has been closed,
a plain note does not, so closing it means "stop chattering at me" rather than
"hide failures from me".  Hooks and console scripts get the same three:
`ctx.log(text)`, `ctx.warn(text)`, `ctx.error(text)`.

## CANopen

The **CANopen** pane configures a node: the *PDO configuration* tab shows
every TPDO and RPDO with its COB-ID, transmission type, inhibit time, event
timer and mapped objects; edit a cell or map/unmap objects and *Write to node*
writes the communication and mapping records back over SDO.  The *Live PDOs* tab shows each
PDO with its receive count and rate, and the *Emergencies* tab decodes EMCY
objects: the CiA 301 error code, the error register bit by bit, and the five
manufacturer-specific bytes as decoded by
`hooks/canopen.py::emcy_manufacturer` (only the device maker knows what those
mean, so that is a hook).  The **SYNC producer** transmits sync
messages so synchronous PDOs are exchanged, **Store** / **Restore
defaults** are objects 0x1010 / 0x1011, and **Save DCF** reads every parameter
from the node into a `.dcf` file while **Apply DCF** writes a `.dcf` back into a
node -- so a device can be commissioned, captured and cloned.

A node that stops sending heartbeats is marked **lost** in the node list and
reported in the Event Log; the timeout follows the producer time from object
0x1017, or the interval actually observed on the bus.  The *LSS* tab
commissions a device that has no node-ID yet (CiA 305): *Fastscan* discovers an
unconfigured node's identity, *Select by address* addresses a known one, then
set its node-ID and bit rate, store, and return to the waiting state.

## UDS

The **UDS** pane talks ISO 14229 over ISO-TP (udsoncan + can-isotp): set the
tester/ECU ids, Open, then sessions, SecurityAccess (the seed-to-key algorithm
is `hooks/uds.py::security_key`), tester present, DID read/write, DTC read and
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

### DTCs

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

### Firmware transfer

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

## ASCII Log

The **ASCII Log** pane reads a CAN id as text.  Some devices use an id as a
console and print into the data bytes a few characters at a time.  No protocol
settles which identifier that happens on -- CANopen has objects for a console,
but an object is not an identifier -- so you give it the ids.
Several at once, each in its own tab, each with its own *Skip* for devices
that put a length or a sequence number in the first byte or two.  NUL
padding and carriage returns are dropped, newlines and tabs kept, and
anything else shown as a dot -- a stream of dots is how you find out the id
is wrong.  **Own window** gives one stream a top-level window with a taskbar
entry, for watching a device talk while doing something else here; closing
that window brings it back as a tab with its text intact.

## J1939

The **J1939** pane lists nodes (NAME from address claims), active faults from
DM1 with lamp status, and reassembled multi-packet messages (TP.BAM / TP.CM via
can-j1939).  Claim a tester address to send requests and multi-packet PGNs; a
J1939 DBC (`VFrameFormat=J1939PG`) is matched by PGN so SPNs land in Signals
and Plot.  DM1/DM2 faults show the failure mode in words -- "voltage below normal" rather
than "FMI 4" -- next to the SPN.

The J1939 name tables live in `hooks/j1939.py`, filled in rather than hidden
inside pycangui: the PGN names and the failure modes are there to read, and
adding a proprietary PGN or one of the failure modes SAE leaves reserved is a
line in a dictionary.  SPN *names* are not shipped -- several thousand entries
from the copyrighted SAE J1939-71 -- so load a J1939 DBC to name them, or add
the ones you care about to `SPN_NAMES`.  UDS DTC descriptions are the same
story, except that no standard defines them at all.

The demo device includes an engine at SA 0 (EEC1, CCVS1, DM1, and a
BAM ComponentID reply to a request for PGN 65259).

## XCP

The **XCP** pane speaks XCP on CAN: set the command/response ids, Connect,
load an A2L (`File`-style button in the pane) and the MEASUREMENTs and
CHARACTERISTICs appear.  Double-click to read one, edit a characteristic's
value to write it (unlock CAL first -- the seed-to-key algorithm is
`hooks/xcp.py::compute_key`), and tick *Plot* to poll a measurement into the
Signals/Plot panes.  The demo device answers on 0x7A0/0x7A1 and matches
`resources/demo.a2l`.

## Plugins

A hook answers a question pycangui already knows to ask -- which EDS for this
node, what to call it -- from a fixed list of them.  A plugin is the other
half: code that adds something that was not there, a pane of its own with its
own buttons, doing something pycangui has never heard of.

A plugin is a folder with a `plugin.py` in it, in
`workspaces\<name>\plugins\` (*Plugins > Open plugins folder*).  It belongs
to the workspace, the same as the hooks, because a screen for a product is
knowledge about that product.

The **Plugins** menu is also the answer to what you have installed: every
plugin appears there whether or not it added any entries of its own, and one
that failed to load appears greyed out rather than silently not being there.

```python
NAME = "Firmware"
API_VERSION = 1  # what it was written against; refused if newer than pycangui


def register(app):
    app.add_pane("main", "Firmware", build_the_widget)
    app.add_menu_action("Do the thing", run_it, "what it will do")
```

`app` is the whole API, and it offers:

| | |
|---|---|
| `add_pane(name, title, build, area, several)` | a dock of its own, hidden until the View menu opens it |
| `add_menu_action(text, callback, tooltip)` | an entry under *Tools > Plugins > your plugin* |
| `add_toolbar_button(text, callback, tooltip)` | a button on the toolbar |
| `add_trace_labeller(fn)` | name frames in every trace: `fn(frame) -> str \| None` |
| `add_panel_widget(kind, class)` | an eighth way for a panel to show an object |
| `run_in_background(job, done)` | work off the GUI thread, so the window does not freeze |
| `log` / `warn` / `error` | say something in the Event Log, prefixed with your name |
| `ctx` `hooks` `panes` `channels` `bus` `signals` `canopen` `uds` `j1939` `xcp` `dbc` | the live objects |

Two things it does for you.  **A plugin that fails takes only itself down** --
the traceback goes to the Event Log where somebody will see it, rather than to
a console that does not exist, and the rest still load.  If it fails part way
through `register`, whatever it had already added is taken back, so the window
is not left with a menu entry that raises whenever it is used.

**Reload means reload.**  *Plugins > Reload plugins* takes away everything a
plugin added last time before loading it again, so editing one and pressing
reload is how it gets written -- there is no need to restart, and no second
copy of its pane appears beside the first.

pycangui's own plugins are loaded the same way, from the same kind of folder.
One of yours with the same name replaces one of ours, exactly as a hook file
does.

## Replaceable protocol back ends

Each protocol is split into a *manager* (Qt signals, threading, A2L / EDS /
DBC handling, plotting -- the part that never changes) and a small *engine*
that actually talks the protocol.  Engines are picked at run time, so you can
drop in your own implementation -- a C or Rust library through `ctypes`, or a
different Python package -- without touching pycangui:

| Kind | Interface | Built in |
|------|-----------|----------|
| `xcp` | `pycangui.xcp.engine.XcpEngine` (connect, seed/unlock, read, write) | `native` -- XCP on CAN in pycangui |
| `isotp` | `pycangui.uds.transport.IsoTpTransport` (open, send, recv) -- everything UDS needs from the link | `can-isotp` |

Put a module in `%APPDATA%\pycangui\backends\` (Tools > Open backends folder):

```python
from pycangui.core.backends import register_backend
from pycangui.uds.transport import IsoTpTransport


@register_backend("isotp", "my-c-lib", "ISO 15765-2 from my C library")
class MyIsoTp(IsoTpTransport):
    def open(self): ...
    def close(self): ...
    def send(self, payload): ...
    def recv(self, timeout): ...
```

It then appears in the pane's engine/transport selector and the choice is
remembered.  A backend module that fails to import is reported in the Event Log and
skipped; the built-in keeps working.  CANopen and J1939 still call their
libraries directly and will get the same treatment.

## The Python console

The **Python Console** pane is a live console with the same objects the GUI uses
(`bus`, `canopen`, `ctx`, `hooks`, `window`, `send(id, data)`); *Run script...*
executes a `.py` file in that namespace.
