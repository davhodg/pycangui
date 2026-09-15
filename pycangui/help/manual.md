# pycangui manual

## Basics

- [Your files](files.md) --- what pycangui keeps, where, and where the file dialogs open
- [Workspaces](workspaces.md) --- one folder per product or network; skip this if you only have one
- [Panes and layout](panes.md) --- docking, floating, pinning, more than one of a pane

## Panes

- [CAN Trace](trace.md) --- filtering and pausing without discarding anything
- [CAN Transmit](transmit.md) --- raw, DBC and CANopen RPDO messages, one-shot or cyclic
- [Databases, signals and the plot](signals.md) --- loading a DBC, watching signals, exporting CSV
- [Event Log](event-log.md) --- everything the tool says, and at what level
- [ASCII Log](ascii-log.md) --- reading messages from a single CAN-ID as text
- [CANopen](canopen.md) --- object dictionary, PDO configuration, EMCY, DCF, LSS
- [UDS](uds.md) --- sessions, DIDs, DTCs, routines and firmware transfer over ISO-TP
- [J1939](j1939.md) --- address claims, DM1 faults, multi-packet messages
- [XCP](xcp.md) --- measurements and characteristics from an A2L
- [Custom panes](custom-panes.md) --- the objects a job needs, laid out as a form
- [Python Console](console.md) --- direct access to the underlying Python, or running a custom script 

## CAN bus

- [CAN adapters and channels](channels.md) --- picking an adapter, bitrates and CAN FD, recording and replay
- [Virtual buses, simulated nodes and gateways](virtual.md) --- trying everything with no adapter plugged in, or testing a connected node

## Customise

- [Hooks](hooks.md) --- customisable Python functions to support any device
- [Plugins](plugins.md) --- code that adds a pane of its own, installed from a file
- [CANopen DCF compare](compare.md) --- a plugin: two configurations side by side
- [Replaceable protocol back ends](backends.md) --- your own engine for testing

## Also

- [About, licences and updates](about.md)
