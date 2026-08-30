# pycangui

A user-friendly CAN bus tool: live trace, transmit, CANopen, UDS, J1939, XCP
and Python scripting, on any adapter supported by python-can.  Apache-2.0.

## Running

Double-click `pycangui.cmd` (Windows) or run `./pycangui.sh` (Linux / macOS).
Python 3.12 or newer must be on the PATH.

The first run sets itself up: it creates a *virtual environment* -- a folder
called `.venv` holding its own copy of Python and only the libraries pycangui
needs, so nothing else on the machine is touched -- and downloads about 90 MB
into it.  That takes a few minutes once; every later start is immediate, and
deleting `.venv` undoes it.  If `uv` is installed it is used instead of pip,
which makes rebuilding that folder later a matter of seconds.

For development:

```
python -m venv .venv
.venv\Scripts\pip install -e .[dev]
.venv\Scripts\python -m pycangui
.venv\Scripts\python -m pytest
```

Without hardware: pick interface `virtual`, channel **vcan0 (CANopen demo
device)**, and press Connect -- the channel is the switch, so there is nothing
else to turn on.  `vcan1` and `vcan2` are empty loopbacks, for replaying a log
onto or sending your own frames.  A simulated node 5 appears in the
CANopen pane: its EDS is matched automatically, the object dictionary can be
read (double-click) and written (edit the value), NMT Start makes it transmit
TPDO1, and writing *Speed demand* (0x2001) moves the motor speed in the PDO.

## Customising

Everything in `%APPDATA%\pycangui` is yours: `hooks/*.py` hold small Python
functions pycangui calls at decision points (which EDS to use for a node, how
to name it, ...) with the defaults and commented examples in place; `eds/` is
scanned for EDS files matching a node's vendor/product; `settings.json` holds
what the GUI remembers.  Tools > Reload hooks applies edits without a restart.

`settings.json` is sorted, indented JSON with dotted keys, meant to be read and
hand-edited: the channels and their adapters, the databases loaded, the
transmit list, which trace groups are hidden, the trace view mode, the plot
window, the UDS and XCP addresses, whether DBC checks are strict.  Settled
choices are kept; passing state -- a search box, a paused view, the selected
row -- is not, because starting up paused would be a bug rather than a
convenience.  Window geometry and the dock layout go to `QSettings` instead,
since that is what Qt saves and restores itself; *View > Reset layout* puts
those back.

**Help > About** shows the version alongside the Python, Qt, python-can and
canopen versions in a form you can copy into a bug report; **Help > Licences**
shows pycangui's own Apache-2.0 licence, the NOTICE attributions and the full
third-party licence text, all shipped with the application.  **Help > Check
for updates** asks GitHub whether there is a newer release -- only when you
pick it: pycangui makes no network connection of its own accord, sends nothing
about your machine, and downloads nothing.  A newer version just offers to
open the releases page.

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

Controls whose effect is not written on them explain themselves on hover: the
UDS service behind a button, what a routine's Start actually sends, that
clearing DTCs takes the freeze frames with them, that an RPDO needs the node's
configuration read first.  The obvious ones -- Clear, Remove, Connect -- are
left alone, since a tooltip repeating its label is noise.

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

The trace narrows down in three ways, none of which discard anything: the
**filter box** matches text against the id, the decoded name, the channel and
the data (`185`, `txpdo`, `drive bus`, `de ad`; several words must all match),
the **Filter** menu hides whole protocol groups or channels, and **Pause**
holds the display still while capture and recording carry on.  The row count
next to the buttons reads *shown of captured*.  Select rows and press Ctrl+C
to copy them as text.

pycangui asks before it can disturb equipment that is not its own, once a
session for each: joining a **real bus** (naming the bitrate, because a
controller at the wrong one cannot read a frame and signals an error on every
one it sees, which can drive the working nodes off the bus), **transmitting**
onto one, and **replaying** a log onto one.  A `virtual` channel never asks --
nothing leaves pycangui.  Change the bitrate and the connect question comes
back, since getting it wrong is what the question is for.

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

Everything pycangui says goes to the **Event Log** with a level: information,
warning or error.  A warning or an error opens the pane if it has been closed,
a plain note does not, so closing it means "stop chattering at me" rather than
"hide failures from me".  Hooks and console scripts get the same three:
`ctx.log(text)`, `ctx.warn(text)`, `ctx.error(text)`.

The **CANopen** pane also configures a node: the *PDO configuration* tab shows
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

The **ASCII** pane reads a CAN id as text.  Some devices use an id as a
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

The **XCP** pane speaks XCP on CAN: set the command/response ids, Connect,
load an A2L (`File`-style button in the pane) and the MEASUREMENTs and
CHARACTERISTICs appear.  Double-click to read one, edit a characteristic's
value to write it (unlock CAL first -- the seed-to-key algorithm is
`hooks/xcp.py::compute_key`), and tick *Plot* to poll a measurement into the
Signals/Plot panes.  The demo device answers on 0x7A0/0x7A1 and matches
`resources/demo.a2l`.

### Replaceable protocol back ends

Each protocol is split into a *manager* (Qt signals, threading, A2L / EDS /
DBC handling, plotting -- the part that never changes) and a small *engine*
that actually talks the protocol.  Engines are picked at run time, so you can
drop in your own implementation -- a C or Rust library through `ctypes`, or a
different Python package -- without touching pycangui:

| Kind | Interface | Built in |
|------|-----------|----------|
| `xcp` | `pycangui.xcp.engine.XcpEngine` (connect, seed/unlock, read, write) | `native` -- XCP on CAN in pycangui |
| `isotp` | `pycangui.uds.transport.IsoTpTransport` (open, send, recv) -- everything UDS needs from the link | `can-isotp` |

Put a module in `%APPDATA%\pycanguiackends\` (Tools > Open backends folder):

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

The **Python Console** pane is a live console with the same objects the GUI uses
(`bus`, `canopen`, `ctx`, `hooks`, `window`, `send(id, data)`); *Run script...*
executes a `.py` file in that namespace.

## Built with

pycangui is Apache-2.0 and is a pure-Python application on top of these
packages, each used unmodified under its own licence (licence text as declared
in the package metadata).  The LGPL components stay separate, replaceable
packages, as the LGPL requires.

| Package | Used for | Licence |
|---------|----------|---------|
| [Python](https://www.python.org) | Runtime | PSF-2.0 |
| [PySide6-Essentials](https://www.qt.io/qt-for-python) | GUI toolkit (Qt for Python) | LGPL-3.0 (used under LGPL) |
| [shiboken6](https://www.qt.io/qt-for-python) | Qt binding runtime used by PySide6 | LGPL-3.0 (used under LGPL) |
| [python-can](https://github.com/hardbyte/python-can) | CAN adapter abstraction | LGPL-3.0 |
| [canopen](https://github.com/christiansandberg/canopen) | CANopen protocol stack | MIT |
| [cantools](https://github.com/cantools/cantools) | DBC / KCD / SYM / ARXML decoding | MIT |
| [pyqtgraph](https://www.pyqtgraph.org) | Plotting | MIT |
| [numpy](https://numpy.org) | Numeric arrays for plotting | BSD-3-Clause (with 0BSD / MIT / Zlib / CC0 parts) |
| [can-j1939](https://github.com/juergenH87/python-can-j1939) | J1939 transport and address claim | MIT |
| [pywin32](https://github.com/mhammond/pywin32) | Needed by can-j1939 on Windows | PSF-2.0 |
| [udsoncan](https://github.com/pylessard/python-udsoncan) | UDS client | MIT |
| [can-isotp](https://github.com/pylessard/python-can-isotp) | ISO-TP transport for UDS | MIT |
| [bincopy](https://github.com/eerimoq/bincopy) | Intel HEX / S-record / binary firmware files | MIT |

XCP on CAN and its A2L reader are implemented directly in pycangui (no XCP
library dependency).

Only LGPL Qt modules are used (QtCore, QtGui, QtWidgets).  pycangui depends on
**PySide6-Essentials** rather than the full PySide6, so the GPL-only add-on
modules (Qt Charts, Qt Data Visualization and the rest) are never installed --
which also saves about 160 MB.
Adapter drivers (PCAN, Kvaser, Vector, ...) are not included: install the
vendor's driver and python-can loads it at run time.

## Building a distributable

`build.cmd` produces a self-contained Windows application, and an installer if
a compiler for one is present:

```
build.cmd            tests, notices, PyInstaller, checks, then setup.exe
build.cmd nosetup    stop after the checked application folder
```

It runs the tests, regenerates `THIRD-PARTY-NOTICES.txt` from the installed
package metadata, builds a **one-directory** bundle with PyInstaller (so Qt and
python-can stay separate, replaceable DLLs, as the LGPL asks), checks the
result, and then wraps it with **Inno Setup**.  The
result is `dist\pycangui\pycangui.exe` and `dist\pycangui-<version>-setup.exe`;
nothing needs to be installed on the target machine, not even Python.

`build/check_build.py` fails the build if a **GPL-only Qt module** has crept in
(shipping Qt Charts or the Virtual Keyboard would change the licence of the
whole application), if a sample or hook template is missing, or if the built
executable cannot import its protocol stacks and every python-can adapter
backend -- it runs `pycangui.exe --selftest` to find out rather than guessing
from file names.

Adapter drivers are not bundled: install the vendor's driver and python-can
finds it.  Hooks, back ends, EDS files and settings stay in `%APPDATA%\pycangui`
and survive upgrades and uninstallation.

`.github/workflows/ci.yml` runs the tests and lint on every push: the latest
Python on Windows and Linux, and the oldest supported Python on Linux as well
(bugs that only appear on the floor are real, but rarely platform specific, and
the Linux runner is the cheap one).  The installer is built only for a
release -- push a `v*` tag, or start the workflow by hand from the Actions tab
-- because a Windows runner costs double the minutes and the packaging does not
change between tags.

## Development

pycangui is copyright 2026 davhodg and licensed under the Apache License
2.0; see `LICENSE` and `NOTICE`.

Development has made use of Anthropic's Claude.
