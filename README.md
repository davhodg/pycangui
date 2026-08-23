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

The **Python** pane is a live console with the same objects the GUI uses
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

## Development and AI disclosure

pycangui is designed and directed by davhodg.  Much of the code is
written with the assistance of Anthropic's Claude (Claude Fable 5) working as
a pair-programming tool under direction, with the design decisions, review,
testing and acceptance made by the author.  Contributions are reviewed to the
same standard regardless of origin.
