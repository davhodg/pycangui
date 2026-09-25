<p align="center">
  <img src="https://raw.githubusercontent.com/davhodg/pycangui/master/pycangui/resources/pycangui.png" alt="pycangui icon: a CAN bus with four node taps and a terminating resistor at each end" width="128">
</p>

# pycangui

A graphical CAN bus tool: live trace and plots, transmit, CANopen, UDS, J1939, XCP and CCP, and Python scripting, on any adapter supported by python-can.

![pycangui on its demo device: the CAN Trace, CAN Transmit and Event Log above, and engine and vehicle speed plotted in Signals and Plot below](https://raw.githubusercontent.com/davhodg/pycangui/master/pycangui/help/main-window.png)

## Install and run

```
pip install pycangui
pycangui
```

Python 3.12 or newer, on Windows, Linux or macOS. `python -m pycangui` starts it too.

To read MDF and MF4 measurement files, add the `mf4` extra. Without it, pycangui offers to install the reader the first time a measurement file is opened.

```
pip install "pycangui[mf4]"
```

Without Python, on Windows: the [installer on the releases page](https://github.com/davhodg/pycangui/releases) is a self-contained application.

## What it does

- **Live trace** of every connected channel on one clock, CAN FD included, with filtering that hides rather than discards, recording to six log formats, and replay of a log onto a bus.
- **Transmit** raw frames, DBC messages edited by signal, or a CANopen RPDO.
- **CANopen**: node list, object dictionary, PDO configuration, EMCY, LSS, SYNC, DCF save and apply.
- **Custom panes**: the CANopen objects a job needs, laid out as a form with labels and units. Built from the object dictionary with no code, and polled into Signals and Plot.
- **UDS** over ISO-TP: sessions, security access, DIDs, DTCs, routines, and firmware transfer in either direction.
- **J1939**: nodes from address claims, DM1 faults with lamp status and the failure mode in words, multi-packet messages (TP.BAM and TP.CM), requesting and sending PGNs, and SPNs decoded from a J1939 DBC.
- **XCP and CCP on CAN**: connect, seed and key, and reading and writing A2L measurements and characteristics by polling.
- **ASCII Log**: reads any CAN identifier as text, for devices that print a console into the data bytes.
- **Signals and Plot** from DBC decode, CANopen TPDOs or XCP and CCP polling, and out to CSV.
- **Python** hooks with hot reload, replaceable components (your own CAN interface, ISO-TP transport, or XCP or CCP engine), a live console and *Run script*.
- **Plugins** that add a pane of their own, installed from a zip. Three come with pycangui: CANopen firmware download (CiA 302-3), DCF compare, and CiA 402 motor control.
- **Simulated nodes**: devices written as Python files, on a virtual bus or standing on a real adapter, and gateways between channels.
- **Workspaces**: one per product, holding its hooks, EDS files, databases, channels and layout, and exported as one zip.

The [manual](https://github.com/davhodg/pycangui/blob/master/pycangui/help/manual.md) covers each of these, and is also under **Help > Documentation** in the application.

## Adapters

Any adapter [python-can](https://github.com/hardbyte/python-can) supports: PEAK, IXXAT, Kvaser, Vector, socketcan, the low-cost USB dongles and more. Install the vendor's driver and python-can finds it at run time.

No hardware is needed to try it: the `virtual` interface's *Demo device* channel has a simulated device on it that answers CANopen, UDS, J1939, XCP and CCP.

## More

- [Source, issues and releases](https://github.com/davhodg/pycangui)
- [Manual](https://github.com/davhodg/pycangui/blob/master/pycangui/help/manual.md)

## Safety notice and licence

pycangui talks to real equipment. It is intended only for people trained and experienced in working with CAN networks and the equipment connected to them.

Joining a bus at the wrong bitrate makes a controller signal an error on every frame it sees, and those error frames go out on the wire -- they can impact the nodes that are already on the bus. Transmitting, replaying a log, writing parameters, enabling a drive and downloading firmware all change what equipment does, and not all of them can be undone.

Know what is on the bus before you join it, and what a device will do before you write to it. The confirmations pycangui asks for along the way are a reminder, not a safeguard: like any software it can have faults, so do not rely on it to keep anything off the bus. Where a mistake could hurt someone or damage equipment, keep a way to stop that equipment within reach.

The hook and simulated-node templates copied into a workspace are under MIT-0, so what you write in them carries no conditions. pycangui itself is provided under the Apache License 2.0, without warranty of any kind.
