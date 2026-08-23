# pycangui

A user-friendly CAN bus tool: live trace, transmit, CANopen, UDS, J1939, XCP
and Python scripting, on any adapter supported by python-can.  Apache-2.0.

## Running

Double-click `pycangui.cmd` (Windows) or run `./pycangui.sh` (Linux / macOS).
The first run creates a virtual environment and installs the dependencies;
Python 3.12 or newer must be on the PATH.

For development:

```
python -m venv .venv
.venv\Scripts\pip install -e .[dev]
.venv\Scripts\python -m pycangui
.venv\Scripts\python -m pytest
```

Without hardware: pick interface `virtual`, channel `vcan0`, press Connect,
then enable Tools > Demo CANopen device.  A simulated node 5 appears in the
CANopen pane: its EDS is matched automatically, the object dictionary can be
read (double-click) and written (edit the value), NMT Start makes it transmit
TPDO1, and writing *Speed demand* (0x2001) moves the motor speed in the PDO.

## Customising

Everything in `%APPDATA%\pycangui` is yours: `hooks/*.py` hold small Python
functions pycangui calls at decision points (which EDS to use for a node, how
to name it, ...) with the defaults and commented examples in place; `eds/` is
scanned for EDS files matching a node's vendor/product; `settings.json` holds
what the GUI remembers.  Tools > Reload hooks applies edits without a restart.

**File > Load DBC...** decodes matching frames: the trace shows the message
name and the **Signals** pane lists every signal with its live value.  CANopen
TPDO values appear there too.  Tick *Plot* on any signal to draw it in the
**Plot** pane (rolling window, pause, follow).  `resources/demo.dbc` matches
the demo device.

## Channels

pycangui talks to several CAN buses at once -- a second port on a multi-channel
adapter, or a second adapter entirely.  Use **+** on the toolbar to add a
channel, then give it its own interface, bitrate and connection; each channel's
settings are remembered by name.

The trace, the recorder and the decoders always see **every** connected
channel, on one shared clock, so an ECU forwarding messages between two buses
can be watched from both sides with comparable timestamps.  The channel picked
in the toolbar is the one the protocol panes (CANopen, UDS, J1939, XCP) work
with; switching channel looks to them like a disconnect and a reconnect.

**Record** on the toolbar writes everything on the bus to a log file --
`.blf` (Vector binary), `.asc` (Vector ASCII), `.trc` (PEAK), `.log` (candump),
`.csv` or `.db` (SQLite); the format follows the file extension.  The
**Replay** pane plays a log back with its original timing (0.1x to 20x, with
looping).  With *Transmit* ticked the frames go onto the bus; with it clear
nothing is transmitted and the frames are fed straight to the trace, the
decoders, the signal hub and the plot -- so a colleague's recording can be
examined with no hardware attached at all.

The **Transmit** pane holds one list of everything being sent, with three kinds
of row: **raw** (type the id and bytes), **DBC** (pick a message from a loaded
database and edit its signals in physical units), and **CANopen RPDO** (pick a
node's RPDO -- press *Read RPDO config* in the CANopen pane first -- and edit
its mapped variables).  Expand a row to see its signals; the encoded bytes
update as you type, and a message that is already cycling is updated live.

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
clear, routines, ECU reset and raw requests.  The demo device answers on
0x7E0/0x7E8 with a byte-invert key.

The **J1939** pane lists nodes (NAME from address claims), active faults from
DM1 with lamp status, and reassembled multi-packet messages (TP.BAM / TP.CM via
can-j1939).  Claim a tester address to send requests and multi-packet PGNs; a
J1939 DBC (`VFrameFormat=J1939PG`) is matched by PGN so SPNs land in Signals
and Plot.  The demo device includes an engine at SA 0 (EEC1, CCVS1, DM1, and a
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
| [PySide6](https://www.qt.io/qt-for-python) | GUI toolkit (Qt for Python) | LGPL-3.0 (used under LGPL) |
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

XCP on CAN and its A2L reader are implemented directly in pycangui (no XCP
library dependency).

Only LGPL Qt modules are used (QtCore, QtGui, QtWidgets); the GPL-only Qt
modules such as Qt Charts and Qt Data Visualization are deliberately avoided.
Adapter drivers (PCAN, Kvaser, Vector, ...) are not included: install the
vendor's driver and python-can loads it at run time.

## Development

pycangui is copyright 2026 davhodg and licensed under the Apache License
2.0; see `LICENSE` and `NOTICE`.

Development has made use of Anthropic's Claude.
