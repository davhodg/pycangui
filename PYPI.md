<p align="center">
  <img src="https://raw.githubusercontent.com/davhodg/pycangui/master/pycangui/resources/pycangui.png" alt="pycangui icon: a CAN bus with four node taps and a terminating resistor at each end" width="128">
</p>

# pycangui

[![CI](https://github.com/davhodg/pycangui/actions/workflows/ci.yml/badge.svg)](https://github.com/davhodg/pycangui/actions/workflows/ci.yml)
[![release](https://img.shields.io/github/v/release/davhodg/pycangui?include_prereleases)](https://github.com/davhodg/pycangui/releases)
[![PyPI](https://img.shields.io/pypi/v/pycangui)](https://pypi.org/project/pycangui/)
![python](https://img.shields.io/badge/python-3.12+-blue)
![platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey)
![license](https://img.shields.io/badge/license-Apache--2.0-green)

A graphical CAN bus tool: live trace and plots, transmit, CANopen, UDS, J1939, XCP and CCP, and Python scripting, on any adapter supported by python-can -- PEAK, Vector, Kvaser, IXXAT, SocketCAN and low-cost SLCAN adapters among them.

![pycangui on its demo device: the CAN Trace, CAN Transmit and Event Log above, and engine and vehicle speed plotted in Signals and Plot below](https://raw.githubusercontent.com/davhodg/pycangui/master/pycangui/help/main-window.png)

## Install and run

To install it:

```
pip install pycangui
```

To run it:

```
pycangui
```

To update it:

```
pip install --upgrade pycangui
```

Python 3.12 or newer, on Windows, Linux or macOS. `python -m pycangui` starts it too.

On Linux, Qt needs a system library to draw the window, and Debian, Ubuntu, Linux Mint and the like often leave it out:

```
sudo apt install libxcb-cursor0
```

To include everything optional up front -- currently that is only the reader for MDF and MF4 measurement files -- install `all`. Without it, pycangui offers to install the reader the first time a measurement file is opened.

```
pip install "pycangui[all]"
```

## What it does

- **Adapters**: every interface python-can supports -- PEAK PCAN, Vector, Kvaser, IXXAT, ETAS, Intrepid neoVI, NI-CAN and NI-XNET, SYS TEC, Neousys, CANalyst-II, CANtact, candleLight and other gs_usb devices, SLCAN (CANable, Lawicel), 8devices USB2CAN, Seeed Studio, Robotell, isCAN and serial adapters; SocketCAN and socketcand on Linux; UDP multicast between computers; and a virtual bus that needs no hardware at all.
- **Live trace** of every connected channel on one timebase, CAN FD included, with recording to six log formats and replay of a log onto a bus. Filtering is at two levels: an acceptance filter on a channel, which drops frames before they arrive, and a filter on the trace, which hides them and discards nothing.
- **Transmit** raw frames, DBC messages edited by signal, or a CANopen RPDO.
- **Signals and Plot** from DBC decode, CANopen TPDOs or XCP and CCP polling, with export to CSV and import of MDF and MF4 measurement files.
- **CANopen**: node list, object dictionary, PDO configuration, EMCY, LSS, SYNC, DCF save, apply and compare.
- **Custom panes**: the CANopen objects a job needs, laid out as a form with labels and units. Built from the object dictionary with no code, and polled into Signals and Plot.
- **UDS** over ISO-TP: sessions, security access, DIDs, DTCs, routines, and firmware transfer in either direction.
- **J1939**: nodes from address claims, DM1 faults with lamp status and the failure mode in words, multi-packet messages (TP.BAM and TP.CM), requesting and sending PGNs, and SPNs decoded from a J1939 DBC.
- **XCP and CCP on CAN**: connect, seed and key, and reading and writing A2L measurements and characteristics by polling.
- **ASCII Log**: reads any CAN identifier as text, for devices that print a console into the data bytes.
- **Python** hooks with hot reload, replaceable components (your own CAN interface, ISO-TP transport, or XCP or CCP engine), a live console and *Run script*.
- **Plugins** that add a pane of their own, installed from a zip. Two come with pycangui: CANopen firmware download (CiA 302-3) and CiA 402 motor control.
- **Simulated nodes**: devices written as Python files, on a virtual bus or standing on a real adapter, and gateways between channels.
- **Workspaces**: one for each device or project you work on, holding its hooks, EDS files, databases, channels and layout, and exported as one zip.

The [manual](https://github.com/davhodg/pycangui/blob/master/pycangui/help/manual.md) covers each of these, and is also under **Help > Documentation** in the application.

## Adapters

Any adapter [python-can](https://github.com/hardbyte/python-can) supports, as listed above. Install the adapter maker's driver and python-can finds it at run time.

No hardware is needed to try it: the `virtual` interface's *Demo device* port has a simulated device on it that answers CANopen, UDS, J1939, XCP and CCP.

## More

- [Source, issues and releases](https://github.com/davhodg/pycangui)
- [Standalone Windows installer](https://github.com/davhodg/pycangui/releases)
- [Questions and ideas](https://github.com/davhodg/pycangui/discussions)
- [Manual](https://github.com/davhodg/pycangui/blob/master/pycangui/help/manual.md)

## Safety notice and licence

pycangui talks to real equipment. It is intended only for people trained and experienced in working with CAN networks and the equipment connected to them.

Joining a bus at the wrong bitrate makes a controller signal an error on every frame it sees, which can impact the nodes that are already on the bus. Transmitting, replaying a log, writing parameters, enabling a drive and downloading firmware all change what equipment does, and not all of them can be undone.

Know what is on the bus before you join it, and what a device will do before you write to it. Like any software, pycangui can have faults, so do not rely on it to keep anything off the bus. Where a mistake could hurt someone or damage equipment, keep a way to stop that equipment within reach.

The hook and simulated-node templates copied into a workspace are under MIT-0, so what you write in them carries no conditions. pycangui itself is provided under the Apache License 2.0, without warranty of any kind.
