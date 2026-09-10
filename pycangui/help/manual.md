# pycangui manual

## Basics

- [Your files](files.md) --- what pycangui keeps, where, and where file dialogs open
- [Workspaces](workspaces.md) --- one folder per product; skip this if you only have one
- [Panes and layout](panes.md) --- docking, floating, pinning, more than one of a pane

## Panes

- [CAN Trace](trace.md) --- filtering and pausing without discarding anything
- [CAN Transmit](transmit.md) --- raw, DBC and CANopen RPDO messages, one-shot or cyclic
- [Databases, signals and the plot](signals.md) --- loading a DBC, watching signals, exporting CSV
- [Event Log](event-log.md) --- everything the tool says, and at what level
- [ASCII Log](ascii-log.md) --- reading a CAN id as text, one pane per id
- [CANopen](canopen.md) --- object dictionary, PDO configuration, EMCY, DCF, LSS
- [UDS](uds.md) --- sessions, DIDs, DTCs, routines and firmware transfer over ISO-TP
- [J1939](j1939.md) --- address claims, DM1 faults, multi-packet messages
- [XCP](xcp.md) --- measurements and characteristics from an A2L
- [Custom panes](custom-panes.md) --- the objects a job needs, laid out as a form
- [Python Console](console.md) --- the live objects, and running a script against them

## CAN bus

- [CAN adapters and channels](channels.md) --- picking an adapter, bitrates and CAN
  FD, what pycangui asks before it can disturb equipment, recording and replay
- [Virtual buses, nodes and gateways](virtual.md) --- trying everything with no adapter
  plugged in, and the demo device

## Customise

- [Hooks](hooks.md) --- customisable Python functions to support any device
- [Plugins](plugins.md) --- code that adds a pane of its own, installed from a file
- [CANopen DCF compare](compare.md) --- a plugin: two configurations side by side
- [Replaceable protocol back ends](backends.md) --- your own engine under a protocol pane

## Also

- [About, licences and updates](about.md)
