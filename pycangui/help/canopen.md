[&larr; Contents](manual.md)

# CANopen

## Nodes

Nodes appear in the list as they are heard on the bus, with their name, NMT
state and the EDS matched to them. The EDS is found from the node's identity,
by [`hooks/canopen.py::eds_for_node`](hooks.md), or by **Load EDS...**, which
chooses one by hand for the selected node. The **name** is whatever
[`hooks/canopen.py::node_name`](hooks.md) answers, or the EDS's ProductName,
or `Node <id>` until one of those says otherwise.

**A node that goes away and comes back** -- which is what a controller does
while it is reflashed -- keeps its row, because which node went is the news.
Its identity is *not* read again: probing a node that has just recovered
would be pycangui deciding to put traffic on your bus. What does happen is
that `node_name` and `eds_for_node` are asked again, so a hook that knows a
reflashed controller wants a different file or a different name can say so,
and can read the device itself to find out. Only those two: the remembered
choice, the search of the EDS folder and the file dialog are for a node
nobody has an answer for yet, and a dialog opening every time a heartbeat
came back would be a way of making people unplug things.

**NMT command**, with **Send NMT** beside it, sends Start, Pre-operational,
Stop, Reset node or Reset communication to the selected node, or to every node
when none is selected.
**SYNC producer** transmits SYNC (0x080) for as long as it is ticked, so
synchronous PDOs are exchanged; how often is under *Settings...*, since a rate
is a fact about the bus rather than a decision to take each time.

The list is the dividing line. What is above it acts on the network -- NMT
and SYNC are services the whole bus hears, and **Add node...** puts a row in
the list rather than doing anything to one. Everything below it acts on the
node highlighted in it, and is switched off while no node is highlighted, or
while the highlighted one is lost: a button that looks pressable and then says
"no node selected" is a worse way to find that out than one that is plainly
not.

**Add node...** puts in a node that has not been heard from:
one with its heartbeat switched off, held in pre-operational, or sitting in its
bootloader. It is identified straight away. **Login...** asks the selected
node for an access level, with a password if the device wants one, and **Read
access level** asks which level is held; the **Access** column shows the
answer.
CANopen has no standard way to log in, so both are done by `login` and
`current_level` in [`hooks/canopen.py`](hooks.md), written for your device.
The password is passed to the hook and is neither logged nor kept.

The second row under the list is what the node holds. **Load EDS...** chooses
its description by hand, and **Read RPDO config** reads the selected node's
RPDO mapping from the node itself, so [CAN Transmit](transmit.md) offers the
RPDOs a remapped node actually receives rather than the ones its EDS started
with. **Store** and **Restore defaults** are 0x1010 and 0x1011, and **Save
DCF...** and **Apply DCF...** read every parameter out to a file and write one
back in.

Everything under the list is on the right-click menu of a node as well, so a
node can be worked on where it is rather than by selecting it and then
reaching for a row of buttons.

**Settings...** holds what is set once rather than done, and is kept in the
workspace:

- **SDO timeout** is how long to wait for a node to answer each SDO request,
  and **retries** is how many more times to ask before giving up. The
  defaults, 300 ms and none, are those of the `canopen` library pycangui uses.
  Raise them for a node that is slow to answer or a bus that is busy.
- **Per node** is for a node that needs something other than what pycangui
  works out. **Add** starts a row for the selected node with nothing changed;
  **Remove** puts the node back.
  - **SDO request** and **SDO response** are for a node whose SDO server is not
    on the channel CiA 301 predefines, requests to 0x600 + node and answers on
    0x580 + node. Change the COB-IDs, in hex, to where its server is. Its
    heartbeat, emergencies and NMT are not affected.
  - **Heartbeat timeout**, in milliseconds, is how long without a heartbeat
    before the node is called lost, instead of the timeout worked out for it.
    Leave it blank to keep that.

The SDO settings apply to every SDO pycangui sends: the object dictionary,
custom panes, DCFs and plugins.

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

The lower half of the pane is a row of tabs, the **Object dictionary** first:
it, the live PDOs and LSS each want the whole height, and sharing it between
them left every one too short to read.

The *PDO configuration* tab shows
every TPDO and RPDO with its COB-ID, transmission type, inhibit time, event
timer and mapped objects. **Read from node** reads what the node is actually
configured to send and receive, rather than what its EDS says it was built
with; edit a cell, or use **Map object...** and **Unmap**, and **Write to node**
writes the selected PDO's communication and mapping records back over SDO. The *Live PDOs* tab shows each
PDO with its receive count and rate. **Store** / **Restore
defaults** are objects 0x1010 / 0x1011, and **Save DCF** reads every parameter
from the node into a `.dcf` file while **Apply DCF** writes a `.dcf` back into a
node -- so a device can be commissioned, captured and cloned. What is *different* between two of them is the [CANopen DCF compare](compare.md) plugin.

### Emergencies: what arrived, and what is still wrong

The *Emergencies* tab decodes EMCY objects -- the CiA 301 error code, the
error register bit by bit, and the five manufacturer-specific bytes as decoded
by [`hooks/canopen.py::emcy_manufacturer`](hooks.md), since only the device
maker knows what those mean.

They are **grouped by node**, and each one says whether it is **active** or
**cleared**: an emergency stands until that same node sends a reset (code
`0000`), which clears what that node had outstanding and nothing else -- one
drive recovering says nothing about another. Each node's own row says how many
of its faults are still active, or *all clear*. An arrival log answers "what
happened"; somebody with a machine that will not run is asking "what is still
wrong", and that question is per node.

**Save...** writes the lot to CSV, each entry with its state, the raw error
register byte beside its decoding, and the manufacturer bytes -- something to
attach to a report or send to a maker. **Clear** empties pycangui's record and
does not touch what the nodes kept, which is the next section.

### Faults: what the node says when asked

The *Emergencies* tab holds what was broadcast **while pycangui was
listening**. Plug in after a controller has faulted and it is empty, which
reads as "no faults" and is not. The *Faults* tab asks the selected node
instead, so the answer does not depend on having been there:

- **the error register** (0x1001) -- mandatory in CiA 301, so every node has
  one. Non-zero means the node considers itself faulted *now*, and the node
  list's **Error** column says which categories, unless a hook has listed the
  faults themselves;
- **the manufacturer status register** (0x1002) -- optional, and its meaning
  is the maker's alone;
- **the stored errors** (0x1003) -- optional, the codes the node kept, newest
  first, with the high word of each entry manufacturer-specific.

**Two of the three are optional, and the pane says which answer it is
giving.** A node with no 0x1003 says so in place of the list and its *Clear
stored errors* is switched off; a node with an empty 0x1003 says it is holding
none. Those are different facts and an empty list for both would be wrong half
the time. The same goes for 0x1002: "no manufacturer status register" is not
the same as one reading zero.

Nothing is read until you press **Read** -- the stored list costs an SDO per
entry, and clicking through a node list is not a reason to spend them. What
was read stays, so coming back to a node shows its last answer rather than an
empty pane. **Clear stored errors** writes 0 to 0x1003 sub 0, which is how CiA
301 says to empty it; the Emergencies tab is untouched, because what pycangui
saw and what the node kept are two different records.

A stored error is history. A node that faulted this morning and recovered
still holds the entry, so only the error register answers "is it faulted now".

**What is wrong *now*, on a device that can list it.** CiA 301 has no object
for that, which is worth saying plainly: 0x1001 gives categories -- current,
voltage, temperature -- rather than faults, and 0x1002 is one word a maker may
use for anything or not implement at all. A device that can list its active
faults does it its own way, so
[`hooks/canopen.py::active_faults`](hooks.md) is the only place it fits.
Return the entries and the Faults tab lists them, and the node list's **Error**
column says which fault rather than which category; return an empty list and
the device is saying it is healthy, which is shown as such; return None and
the error register is the only answer, as before.

**A device that keeps its faults somewhere of its own** is read by
[`hooks/canopen.py::stored_errors`](hooks.md), and cleared by
`clear_stored_errors` beside it. 0x1003 is the standard's answer and plenty of
makers have another -- a block of manufacturer objects, one object holding a
packed array, a list you ask for by writing an index first -- and that is a
fact about the device, so it lives in your hooks file. Return the entries and
pycangui shows them in place of 0x1003; return None and it reads 0x1003 as
usual. An entry can carry its own text, for a device whose numbering is its
own and which the CiA table would name wrongly or not at all.

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
reported in the Event Log. The timeout is three heartbeats, from the producer
time in object 0x1017 or the interval actually observed on the bus, unless one
is set for that node under *Settings...*. The *LSS* tab
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
