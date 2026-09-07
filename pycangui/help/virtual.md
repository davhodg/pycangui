[&larr; Contents](manual.md)

# Virtual buses, nodes and gateways

pycangui does not need a CAN adapter to be useful.  Its `virtual` interface is
a bus that exists only inside the application, so a log can be replayed, a
message composed, a database checked or a pane built with nothing plugged in at
all.

## The virtual buses

Pick interface `virtual`, channel **vcan0 (CANopen demo device)**, and press
Connect -- the channel is the switch, so there is nothing else to turn on.
`vcan1` and `vcan2` are empty loopbacks, for [replaying a log](channels.md)
onto or sending your own frames.

python-can's `virtual` interface connects buses within one process only, so
these are pycangui's own buses rather than anything another program on the
machine can join.

## The demo device

A simulated node 5 appears in the [CANopen](canopen.md) pane: its EDS is
matched automatically, the object dictionary can be read (double-click) and
written (edit the value), NMT Start makes it transmit TPDO1, and writing *Speed
demand* (0x2001) moves the motor speed in the PDO.

It is a real `canopen.LocalNode` rather than a recording: it answers SDO reads
and writes, sends a heartbeat, obeys NMT and transmits TPDO1 every 100 ms with
a motor speed that follows whatever you write to *Speed demand* -- by SDO, or
by sending it RPDO1.  It also answers [UDS](uds.md) on 0x7E0/0x7E8 with a
byte-invert key, [J1939](j1939.md) as an engine at source address 0, and
[XCP](xcp.md) on 0x7A0/0x7A1 against `resources/demo.a2l`.

## Nodes of your own, and gateways

Not yet.  The demo device is written into pycangui rather than being one
example of something you can write, and there is no way to put a gateway
between two channels so that frames on one appear on the other.

Both are wanted and neither is built, so this page says so rather than leaving
you looking for a button.  A virtual node of your own is the larger of the two:
the demo device already does everything one would need to -- answers, sends
cyclically, reacts to what it is written -- and what is missing is only the
seam that would let it be your device instead of ours.
