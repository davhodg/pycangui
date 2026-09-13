[&larr; Contents](manual.md)

# Virtual buses, simulated nodes and gateways

pycangui does not need a CAN adapter to be useful. Its `virtual` interface is
a bus that exists only inside the application, so a log can be replayed, a
message composed, a database checked or a pane built with nothing plugged in at
all.

## The virtual buses

Pick interface `virtual`, channel **vcan0 (Demo device)**, and press Connect
-- the channel is the switch, so there is nothing else to turn on. `vcan1`
and `vcan2` are empty loopbacks, for [replaying a log](channels.md) onto or
sending your own frames.

python-can's `virtual` interface connects buses within one process only, so
these are pycangui's own buses rather than anything another program on the
machine can join.

## The demo device

Connecting that channel starts the **demo device**: four simulated nodes, one
per protocol pycangui speaks, on the one channel.

- [CANopen](canopen.md) node 5 -- its EDS is matched automatically, the
  object dictionary can be read (double-click) and written (edit the value),
  NMT Start makes it transmit TPDO1, and writing *Speed demand* (0x2001)
  moves the motor speed in the PDO. Demand more than it can give and it
  raises an emergency, which the Emergencies tab decodes.
- [UDS](uds.md) on 0x7E0/0x7E8 -- sessions, a seed and key, identifiers,
  stored faults, a routine. Answers longer than one frame are segmented
  properly, so the VIN comes back whole.
- [J1939](j1939.md) -- an engine that claims source address 0 and defends
  it, broadcasts engine and wheel speed, reports a fault, and answers a
  request for its ComponentID as a multi-packet transfer.
- [XCP](xcp.md) on 0x7A0/0x7A1 -- a calibratable memory matching
  `resources/demo.a2l`, with writing locked until a seed and key.

**The demo device is the examples.** Those four are exactly the files in
your workspace's `nodes` folder, described below -- nothing is hidden inside
pycangui. Open one and you are reading the thing you have been talking to,
which is also why they cannot quietly rot: everybody's first run exercises
them.

They appear in **Tools > Simulated nodes** like anything else, so you can stop
one -- to see how your own tool behaves when a device goes quiet, say -- and
they are listed there by name. Disconnecting and reconnecting the channel
brings them back.

## Nodes of your own

A simulated node is a device pycangui pretends to be, so a real one has
something to talk to. A controller that will not leave its fault state until
a peer answers, a slave with nobody polling it, an ECU waiting on an address
claim: the missing half is the node you write.

*Simulated* rather than *virtual*, because the two words were doing different
jobs in one tool. A **virtual channel** is a bus with no hardware behind it.
A **simulated node** is a device with no hardware behind it -- and it is
perfectly happy standing on a real adapter and talking to real equipment,
which is the case worth being clear about.

**Tools > Simulated nodes...** lists what is available, starts one on a channel
and stops it again. That is the whole of the GUI, on purpose -- there is no
node editor and no state-machine builder, because a node is a Python file and
everything interesting about it belongs in the file.

Each file in your workspace's `nodes` folder is one node. It says what it is
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

All four are optional. A device that only announces itself has a `poll`; a
diagnostic server that says nothing until asked has only an `on_frame`.

`node` is the running instance, and is where anything you keep between calls
goes: `node.state` is yours and pycangui never reads it, so two copies of one
node on two channels do not tread on each other. `node.send(id, data)` puts a
frame out, and `node.canopen(eds, node_id)` hands back a whole CANopen server
-- SDO, heartbeat, NMT, PDOs -- built from your EDS, because every CANopen
node wants the same several hundred lines of it.

Everything runs on the same thread as the window, so nothing in a node file
has to think about locks. The other side of that is that a node which blocks
holds the window up, so a `poll` with a second's work to do should take it in
pieces across several polls. A node that raises says so in the [Event
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

The dialog is a convenience, not the way in. `window.nodes` is the same
object from the [Python Console](console.md) and from a
[startup hook](hooks.md), which is the point: standing up the devices a test
needs is setup, and setup belongs in a file rather than in your fingers every
morning.

```python
window.nodes.start("canopen_device", "vcan0")
window.nodes.start("j1939_engine", "vcan1", rate_hz=50)
```

## Gateways

A gateway is a simulated node standing on more than one channel. There is no
second mechanism for it: fill in the **and** box in the dialog, or pass
`extra=` from Python, and the node opens both.

```python
window.nodes.start("gateway", "vcan0", extra=["vcan1"])
```

Inside the file, `node.channels` lists them, `frame.channel` says which one a
frame arrived on -- without which a relay sends every frame straight back at
whoever sent it -- and `node.send(..., channel=...)` picks where it goes.
`gateway.py` is supplied and passes frames both ways with an id map and a
block list, which is a starting point rather than a policy: what should cross
between two segments is a question about your system, so pycangui does not
answer it.

## Which channel a node stands on

**A node uses pycangui's channels, like everything else does.** There is one
notion of a bus in the tool and this is it, so a node's traffic appears in the
[trace](trace.md), its channel appears in the connect bar, and it can be
recorded and replayed like anything else.

Three cases, and you only have to think about the third:

- **A channel that is open** is joined. The node shares the one bus the
  window already has, which is what makes a node on a real adapter work at
  all -- several drivers refuse a second handle on one physical channel.
- **A name pycangui does not know** is added as a virtual channel and
  connected. Asking for a node on a channel that does not exist is asking for
  that channel.
- **A channel that exists but is not connected** is refused, and says so. It
  was configured for something -- quite possibly a real adapter -- and
  connecting it as virtual would be pycangui deciding what your channel is
  for.

### Giving the nodes a bus of their own

Name a channel nothing else uses -- `Simulation`, say -- and start every node
on it. pycangui makes it, and nothing but your nodes is on it. That is the
whole of "a private network for the simulated devices": a channel like any
other, with nothing else connected to it.

Putting a node on the *same* channel as a real device is the other half, and
the more common one: that is how the real device hears it.

**A real adapter asks first.** A node transmits, and transmitting onto a real
bus is what pycangui asks about everywhere else; one that joined quietly
would be the hole in that. The question is asked once for the whole node --
a gateway standing on two channels is one action, not two -- and *Ask about
everything again* in the Tools menu brings it back if you tick it away.

Stopping a node leaves its channels alone. They are the application's, and
one that closed a channel on the way out would disconnect the window.
