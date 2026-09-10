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

## Nodes of your own

A virtual node is a device pycangui pretends to be, so a real one has
something to talk to.  A controller that will not leave its fault state until
a peer answers, a slave with nobody polling it, an ECU waiting on an address
claim: the missing half is the node you write.

**Tools > Virtual nodes...** lists what is available, starts one on a channel
and stops it again.  That is the whole of the GUI, on purpose -- there is no
node editor and no state-machine builder, because a node is a Python file and
everything interesting about it belongs in the file.

Each file in your workspace's `nodes` folder is one node.  It says what it is
and implements whichever of four functions it needs:

```python
NAME = "Thermistor sender"
DESCRIPTION = "Sends a temperature that drifts."
RATE_HZ = 10


def start(node, *, ctx): ...  # once, before the first poll
def poll(node, *, ctx): ...  # every 1/RATE_HZ seconds
def on_frame(node, frame, *, ctx): ...  # a frame arrived
def stop(node, *, ctx): ...  # once, on the way out
```

All four are optional.  A device that only announces itself has a `poll`; a
diagnostic server that says nothing until asked has only an `on_frame`.

`node` is the running instance, and is where anything you keep between calls
goes: `node.state` is yours and pycangui never reads it, so two copies of one
node on two channels do not tread on each other.  `node.send(id, data)` puts a
frame out, and `node.canopen(eds, node_id)` hands back a whole CANopen server
-- SDO, heartbeat, NMT, PDOs -- built from your EDS, because every CANopen
node wants the same several hundred lines of it.

Everything runs on the same thread as the window, so nothing in a node file
has to think about locks.  The other side of that is that a node which blocks
holds the window up, so a `poll` with a second's work to do should take it in
pieces across several polls.  A node that raises says so in the [Event
Log](event-log.md) and stops, rather than raising ten times a second for the
rest of the day.

### The ones supplied

Four examples arrive in the folder on first run, one per protocol pycangui
speaks, and they are meant to be edited -- what is in your workspace is yours
and is never written over:

| File | What it shows |
|------|---------------|
| `canopen_device.py` | The thick end: `node.canopen()` does the protocol, and the file is only about what the device measures |
| `j1939_engine.py` | The thin end: identifiers composed and bytes packed by hand, which is what most protocols need |
| `uds_server.py` | Reacting only -- no `poll` at all, because a diagnostic server speaks when spoken to |
| `xcp_slave.py` | Both halves: a memory that answers commands, and measurements that move |

### Starting one from Python

The dialog is a convenience, not the way in.  `window.vnodes` is the same
object from the [Python Console](console.md) and from a
[startup hook](hooks.md), which is the point: standing up the devices a test
needs is setup, and setup belongs in a file rather than in your fingers every
morning.

```python
window.vnodes.start("canopen_device", "vcan0")
window.vnodes.start("j1939_engine", "vcan1", rate_hz=50)
```

## Gateways

A gateway is a virtual node standing on more than one channel.  There is no
second mechanism for it: fill in the **and** box in the dialog, or pass
`extra=` from Python, and the node opens both.

```python
window.vnodes.start("gateway", "vcan0", extra=["vcan1"])
```

Inside the file, `node.channels` lists them, `frame.channel` says which one a
frame arrived on -- without which a relay sends every frame straight back at
whoever sent it -- and `node.send(..., channel=...)` picks where it goes.
`gateway.py` is supplied and passes frames both ways with an id map and a
block list, which is a starting point rather than a policy: what should cross
between two segments is a question about your system, so pycangui does not
answer it.

## What a node cannot do yet

**Virtual channels only.**  A node opens its own bus, and python-can's virtual
buses find each other by name inside one process, which is what makes a node
possible with nothing plugged in.  A real adapter is a different question -- a
second open handle on one physical channel is backend-dependent, and disturbing
equipment is something pycangui asks about rather than does quietly -- so
starting a node on a connected real channel is refused for now, with a message
saying why.
