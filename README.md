# pycangui

A user-friendly CAN bus tool: live trace, transmit, CANopen, UDS, J1939, XCP
and Python scripting, on any adapter supported by python-can.  Apache-2.0.

## What it does

- **Live trace** of every connected channel on one clock, with filtering that
  hides rather than discards, and recording to six log formats.
- **Transmit** raw frames, DBC messages edited by signal, or a CANopen RPDO.
- **CANopen**: node list, object dictionary, PDO configuration, EMCY, LSS,
  SYNC, DCF save and apply.
- **UDS** over ISO-TP: sessions, security access, DIDs, the whole of
  ReadDTCInformation, routines, and firmware transfer in either direction.
- **J1939** and **XCP on CAN**, and a pane that reads any identifier as text.
- **Signals and Plot** from DBC decode, CANopen TPDOs or XCP polling.
- **Python** hooks with hot reload, replaceable protocol back ends, a live
  console and *Run script*.

Full details in [the manual](pycangui/help/manual.md), which is also under
**Help > Documentation** in the application.

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

## Supported systems

Windows, Linux and macOS, on **Python 3.12 or newer**.

Any adapter [python-can](https://github.com/hardbyte/python-can) supports --
PEAK, IXXAT, Kvaser, Vector, socketcan, the cheap USB dongles and more.  The
vendor's driver is not bundled: install it and python-can finds it at run
time.  A `virtual` channel needs no hardware at all.

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
