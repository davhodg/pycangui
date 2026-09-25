<p align="center">
  <img src="pycangui/resources/pycangui.png" alt="pycangui icon: a CAN bus with four node taps and a terminating resistor at each end" width="128">
</p>

# pycangui

<!--
The CI and release badges are live. The release badge includes pre-releases, so before the first one it reads "no releases found". The Python and licence badges are checked against pyproject.toml by tests/test_readme.py.
-->

[![CI](https://github.com/davhodg/pycangui/actions/workflows/ci.yml/badge.svg)](https://github.com/davhodg/pycangui/actions/workflows/ci.yml)
[![release](https://img.shields.io/github/v/release/davhodg/pycangui?include_prereleases)](https://github.com/davhodg/pycangui/releases)
![python](https://img.shields.io/badge/python-3.12+-blue)
![platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey)
![license](https://img.shields.io/badge/license-Apache--2.0-green)

A graphical CAN bus tool: live trace and plots, transmit, CANopen, UDS, J1939, XCP and CCP, and Python scripting, on any adapter supported by python-can. Apache-2.0.

![pycangui on its demo device: the CAN Trace, CAN Transmit and Event Log above, and engine and vehicle speed plotted in Signals and Plot below](pycangui/help/main-window.png)

## What it does

- **Live trace** of every connected channel on one clock, CAN FD included, with filtering that hides rather than discards, recording to six log formats, and replay of a log onto a bus.
- **Transmit** raw frames, DBC messages edited by signal, or a CANopen RPDO.
- **CANopen**: node list, object dictionary, PDO configuration, EMCY, LSS, SYNC, DCF save and apply.
- **Custom panes**: the CANopen objects a job needs, laid out as a form with labels and units. Built from the object dictionary with no code, and polled into Signals and Plot.
- **UDS** over ISO-TP: sessions, security access, DIDs, DTCs, routines, and firmware transfer in either direction.
- **J1939**: nodes from address claims, DM1 faults with lamp status and the failure mode in words, multi-packet messages (TP.BAM and TP.CM), requesting and sending PGNs, and SPNs decoded from a J1939 DBC.
- **XCP and CCP on CAN**: connect, seed and key, and reading and writing A2L measurements and characteristics by polling.
- **ASCII Log**: reads any CAN identifier as text, for devices that print a console into the data bytes.
- **Signals and Plot** from DBC decode, CANopen TPDOs or XCP and CCP polling, and out to CSV for whatever you analyse with.
- **Python** hooks with hot reload, replaceable components (your own CAN interface, ISO-TP transport, or XCP or CCP engine), a live console and *Run script*.
- **Plugins** that add a pane of their own, installed from a zip. Three come with pycangui: CANopen firmware download (CiA 302-3), DCF compare, and CiA 402 motor control.
- **Simulated nodes**: devices written as Python files, on a virtual bus or standing on a real adapter, and gateways between channels.
- **Workspaces**: one per product, holding its hooks, EDS files, databases, channels and layout, and exported as one zip.

Full details in [the manual](pycangui/help/manual.md), which is also under **Help > Documentation** in the application.

## Installing

There are three options, and which one you want depends on whether you have Python:

1. **The Windows installer** -- `pycangui-<version>-setup.exe` from the releases page. Nothing else is needed: **not even Python.** It installs a self-contained application, offers a desktop shortcut, and uninstalls cleanly. This is the one to give somebody who wants a CAN tool rather than a Python package. Your hooks, EDS files and settings stay in `%APPDATA%\pycangui`, and upgrades and uninstalling leave them alone.

2. **From the source folder** -- either clone with Git or download as zip, double-click `pycangui.cmd` (Windows) or run `./pycangui.sh` (Linux). Python 3.12 or newer must be on the PATH.

   The first run sets itself up: it creates a *virtual environment* -- a folder called `.venv` holding its own copy of Python and only the libraries pycangui needs, so nothing else on the machine is touched -- and downloads a couple of hundred megabytes into it. That takes a few minutes once; every later start is immediate, and deleting `.venv` undoes the whole thing. If `uv` is installed it is used instead of pip, which makes rebuilding that folder a matter of seconds.

   The launcher keeps its own `.venv` on purpose and will not use an environment you already have. That is the point of it: it is the way in for somebody who does not want to think about Python environments, and one that sometimes used yours and sometimes did not would be worse than one that never does.

3. **With pip** -- if you already have a Python environment and would rather pycangui went in it, install the wheel and ignore the launcher entirely:

   ```
   pip install pycangui-0.0.1-py3-none-any.whl    # from the releases page
   pycangui                                        # installs a command of that name
   ```

   or from a checkout, `pip install -e .` for the same thing reading the source. Nothing about pycangui needs the launcher: it is an ordinary Python package with an ordinary entry point, and this path leaves the choice of environment to you.

## Supported systems

**Windows and Linux**, on **Python 3.12 or newer**. Both are tested on every push: the newest Python on both, and the oldest supported one on Linux.

**macOS** passes the same tests on Apple silicon and Intel Macs, run for each release, but has not yet been tested on real CAN hardware.

Any adapter [python-can](https://github.com/hardbyte/python-can) supports -- PEAK, IXXAT, Kvaser, Vector, socketcan, the cheap USB dongles and more. The vendor's driver is not bundled: install it and python-can finds it at run time. A `virtual` channel needs no hardware at all.

## Built with

pycangui is Apache-2.0 and is a pure-Python application on top of these packages, each used unmodified under its own licence (licence text as declared in the package metadata). The LGPL components stay separate, replaceable packages, as the LGPL requires.

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
| [python-can-j1939](https://github.com/RaulSMS/python-can-j1939) | J1939 transport and address claim | MIT |
| [pywin32](https://github.com/mhammond/pywin32) | Finding USB2CAN adapters through python-can, on Windows | PSF-2.0 |
| [udsoncan](https://github.com/pylessard/python-udsoncan) | UDS client | MIT |
| [can-isotp](https://github.com/pylessard/python-can-isotp) | ISO-TP transport for UDS | MIT |
| [bincopy](https://github.com/eerimoq/bincopy) | Intel HEX / S-record / binary firmware files | MIT |
| [asammdf](https://github.com/danielhrisca/asammdf) | Reading MDF / MF4 measurement files | LGPL-3.0 |

XCP, CCP and a basic A2L reader are implemented directly in pycangui.

asammdf, the MDF and MF4 reader, is the one **optional** entry: the Windows installer includes it, and a `pip` installation fetches it the first time a measurement file is opened. `pip install pycangui[all]` includes it up front.

Only LGPL Qt modules are used (QtCore, QtGui, QtWidgets). pycangui depends on **PySide6-Essentials** rather than the full PySide6, so the GPL-only add-on modules (Qt Charts, Qt Data Visualization and the rest) are never installed. Adapter drivers (PCAN, Kvaser, Vector, ...) are not included: install the vendor's driver and python-can loads it at run time.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md): the policy for changes, setting up to work on pycangui, running the tests, and building the Windows installer.

## Licence

pycangui is free software, licensed under the Apache License 2.0; see `LICENSE` and `NOTICE`.

The hook and simulated-node templates in `pycangui/hooks` and `pycangui/nodes` are the exception. They are copied into your workspace to be edited, and are released under MIT-0 (`LICENSES/MIT-0.txt`) with no copyright claimed, so what you write in them carries no conditions.

Development has made use of Anthropic's Claude.
