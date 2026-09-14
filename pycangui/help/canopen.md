[&larr; Contents](manual.md)

# CANopen

## Nodes

Nodes appear in the list as they are heard on the bus, with their name, NMT
state and the EDS matched to them. The EDS is found from the node's identity,
by [`hooks/canopen.py::eds_for_node`](hooks.md), or by **Load EDS...**, which
chooses one by hand for the selected node.

**NMT command** sends Start, Pre-operational, Stop, Reset node or Reset
communication to the selected node, or to every node when none is selected.
**SYNC producer** transmits SYNC (0x080) at the period beside it, so
synchronous PDOs are exchanged. **Read RPDO config** reads the selected node's
RPDO mapping from the node itself, so [CAN Transmit](transmit.md) offers the
RPDOs a remapped node actually receives rather than the ones its EDS started
with.

**Settings...** holds what is set once rather than done, and is kept in the
workspace:

- **SDO timeout** is how long to wait for a node to answer each SDO request,
  and **retries** is how many more times to ask before giving up. The
  defaults, 300 ms and none, are those of the `canopen` library pycangui uses.
  Raise them for a node that is slow to answer or a bus that is busy.
- **SDO channel, per node** is for a node whose SDO server is not on the
  channel CiA 301 predefines, requests to 0x600 + node and answers on
  0x580 + node. **Add** starts a row for the selected node on its predefined
  channel; change the COB-IDs, in hex, to where its server is. **Remove** puts
  the node back. Its heartbeat, emergencies and NMT are not affected.

Both apply to every SDO pycangui sends: the object dictionary, custom panes,
DCFs and plugins.

## The object dictionary

The selected node's dictionary fills from its EDS. Double-click an entry to
read it from the node, and edit a value to write it back. The filter box
matches the index or the name, and several words must all match.

Tick **Watch** against the objects a job uses and **Watched** shows only those.
The list is kept per device, so the next controller of the same kind opens with
the objects you were using on the last one. **Read all** reads every readable
entry, one SDO at a time, which takes a while on a large node. Select some
entries and right-click to add them to a [custom pane](custom-panes.md).

## PDOs, emergencies and DCFs

The **CANopen** pane configures a node: the *PDO configuration* tab shows
every TPDO and RPDO with its COB-ID, transmission type, inhibit time, event
timer and mapped objects. **Read from node** reads what the node is actually
configured to send and receive, rather than what its EDS says it was built
with; edit a cell, or use **Map object...** and **Unmap**, and **Write to node**
writes the selected PDO's communication and mapping records back over SDO. The *Live PDOs* tab shows each
PDO with its receive count and rate, and the *Emergencies* tab decodes EMCY
objects: the CiA 301 error code, the error register bit by bit, and the five
manufacturer-specific bytes as decoded by
[`hooks/canopen.py::emcy_manufacturer`](hooks.md) (only the device maker knows what those
mean, so that is a hook). The **SYNC producer** transmits sync
messages so synchronous PDOs are exchanged, **Store** / **Restore
defaults** are objects 0x1010 / 0x1011, and **Save DCF** reads every parameter
from the node into a `.dcf` file while **Apply DCF** writes a `.dcf` back into a
node -- so a device can be commissioned, captured and cloned. What is *different* between two of them is the [CANopen DCF compare](compare.md) plugin.

**What Apply DCF reports.** Every parameter the node refused is listed,
grouped by the reason the node itself gave -- `abort 0x06010002, Attempt to
write a read only object` once with the objects under it, rather than the same
line two hundred times. The code is what a maker wants quoted at them; the
meaning is what tells you whether it was a read-only object or a value outside
the range the device allows.

The parameters *not* listed were accepted: that is what an SDO write with no
abort means, and reading one straight back would only prove the node can
remember it until the next question. Whether it **keeps** it is a different
matter -- a value that was never stored, or that the device clamped on its way
into the saved image, reads back perfectly until the power goes off. So
pycangui says so instead of implying its own check was the last word: store,
power-cycle the node, and compare it against the DCF in the
[compare](compare.md) pane. That is the check that means something.

A node that stops sending heartbeats is marked **lost** in the node list and
reported in the Event Log; the timeout follows the producer time from object
0x1017, or the interval actually observed on the bus. The *LSS* tab
commissions a device that has no node-ID yet (CiA 305), in the three steps it
is laid out in.

1. **Select the node.** *Fastscan* discovers an unconfigured node's identity
   and leaves it in configuration state, *Select by address* picks a node whose
   identity you already know, and *All nodes* takes every node at once, which
   is only safe with a single device on the bus. *Inquire* reads back the
   selected node's identity and node-ID.
2. **Configure.** *Set node-ID* takes effect once the node is reset. *Set bit
   rate* changes nothing until *Activate*, which switches every node over
   together -- the ones left behind could no longer talk to it -- so reconnect
   pycangui at the new rate afterwards.
3. **Store and finish.** *Store configuration* makes the node-ID and bit rate
   survive a power cycle, and *Back to waiting state* leaves configuration
   state.
