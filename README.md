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
.venv\Scripts\pip install -e .[dev,j1939,uds,xcp]
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
| [can-j1939](https://github.com/juergenH87/python-can-j1939) | J1939 (optional `j1939` extra) | MIT |
| [udsoncan](https://github.com/pylessard/python-udsoncan) | UDS client (optional `uds` extra) | MIT |
| [pyxcp](https://github.com/christoph2/pyxcp) | XCP (optional `xcp` extra) | LGPL-3.0-or-later |

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
