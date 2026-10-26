[&larr; Contents](manual.md)

# Pausing and stopping pycangui's own traffic

pycangui sends some things without anybody asking at that moment: polling, SYNC
and TIME, and CANopen's reads of a node it has just heard from. Most of the time
that is the point. During a flash it is not: a node rebooting into its
bootloader was identified, and its PDO configuration read, over SDO in the
middle of the download. The status bar says, on its right, whether that
traffic is running or paused, and who paused it.

## Pause

A pause holds back what pycangui sends by itself, and lets it go on again when
it lifts:

- **polling**, in [custom panes](custom-panes.md), the [CiA 402](cia402.md)
  motor pane and [XCP / CCP](xcp.md). What is ticked stays ticked;
- CANopen's **SYNC and TIME** producers, which start again as they were;
- what [CANopen](canopen.md) reads of a node **unasked**: identifying one first
  heard, and reading its PDO configuration again after a boot-up, an EDS or a
  write to a PDO object. Each is done once, for each node, when the pause lifts.

What you do by hand still goes out, from a pane, the console or a script. So do
**UDS Tester Present** and the **J1939 DM13 hold**: they keep a programming
session open and a network quiet, which is what a flash needs.

**A flash pauses it by itself**, for as long as it runs: a UDS download or upload,
and a program downloaded by the [CANopen firmware](firmware.md) plugin. A pause
can also be held **by hand**, from the status bar's right-click or *Tools > Pause
automatic traffic*, for anything else. While a flash holds it, pausing by hand
is greyed: the flash gives it back when it is done. A flash ending does not lift
a pause somebody set by hand.

The CiA 402 motor pane puts its drive in the same safe state as when the pane
closes -- halted, a speed or torque demand zeroed -- since nothing is watching
it while its polling waits. It is not set moving again when the pause lifts.

## Stop all

**Stop all repeating traffic**, beside it on the right-click and in the Tools
menu, is for good: cyclic messages in every [transmit](transmit.md) pane,
polling of every kind, SYNC and TIME, and whatever a plugin keeps doing -- the
CiA 402 pane halts its drive. Nothing starts again until it is asked for. UDS
Tester Present and the J1939 DM13 hold go on here too, and are stopped in their
own panes. *Stop all cyclic* in CAN Transmit still stops the cyclic messages
alone.

## Simulated nodes and plugins

A [simulated node](virtual.md) stands for another device, so nothing about it is
paused unless it chooses: a node file with an `on_pause(node, paused, *, ctx)`
is told, and `node.paused` says which. A [plugin](plugins.md) hears of a pause
through `app.on_pause`, of Stop all through `app.on_stop_all`, and can hold the
pause for work of its own with `app.hold_traffic`.
