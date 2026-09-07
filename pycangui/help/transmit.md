[&larr; Contents](manual.md)

# Transmit

A transmit pane only sends while it is on screen.  Closing one stops whatever
it was repeating -- frames arriving on a live bus from a pane nobody can see is
the hardest sort of fault to find, since nothing on screen accounts for them --
and it says so in the Event Log.  Bringing it back does not start them again,
because beginning to transmit onto a bus is not something to do unasked.
Tabbing a transmit pane behind another, or detaching it into a window of its
own, are not putting it away: it is still on screen and still sending.

*Stop all cyclic* means all of them, in every transmit pane, however many are
open.

The **Transmit** pane holds one list of everything being sent, with three kinds
of row: **raw** (type the id and bytes), **DBC** (pick a message from a loaded
database and edit its signals in physical units), and **CANopen RPDO** (pick a
node's RPDO -- press *Read RPDO config* in the CANopen pane first -- and edit
its mapped variables).  All three are behind one *Add* button, since the
choice is which source rather than which button.  Expand a row to see its
signals; the encoded bytes update as you type, and a message that is already
cycling is updated live.

Select several rows -- Ctrl+A takes the lot -- and *Send selected*, *Remove
selected* and the space bar all work on the whole selection, so starting or
stopping a set of cyclic messages is one keypress rather than one tick per row.
