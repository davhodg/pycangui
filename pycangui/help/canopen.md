[&larr; Contents](manual.md)

# CANopen

The **CANopen** pane configures a node: the *PDO configuration* tab shows
every TPDO and RPDO with its COB-ID, transmission type, inhibit time, event
timer and mapped objects; edit a cell or map/unmap objects and *Write to node*
writes the communication and mapping records back over SDO. The *Live PDOs* tab shows each
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
commissions a device that has no node-ID yet (CiA 305): *Fastscan* discovers an
unconfigured node's identity, *Select by address* addresses a known one, then
set its node-ID and bit rate, store, and return to the waiting state.
