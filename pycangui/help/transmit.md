[&larr; Contents](manual.md)

# CAN Transmit

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

## Counters and checksums

A message that carries a rolling counter and a checksum over its own bytes is
completely ordinary -- most safety-relevant messages have both -- and a
receiver that checks either one rejects every frame of a message that never
changes.  Select the row and press *Counter / checksum...*.

A **counter** goes in a whole byte or in either nibble of one, since a
four-bit counter sharing a byte is at least as common as a whole one.  It
starts where you say, steps by what you say, and wraps at whatever the field
holds unless you give it a smaller number -- plenty of protocols count 0 to 5
in a nibble that could hold sixteen.

A **checksum** is computed *after* the counter has been written, over the
whole message except its own bytes.  That default is the usual rule and the
one that is easy to get wrong: including the checksum's own bytes means
hashing a field that is about to be overwritten, so the number never matches
at the other end.  Give it an explicit byte range where a protocol wants one.

| Algorithm | Where you meet it |
|-----------|-------------------|
| XOR, 8-bit sum | Simple in-house protocols |
| Sum, two's complement | The bytes plus the checksum total zero |
| CRC-8 / SAE J1850 | AUTOSAR E2E profile 1 |
| CRC-8 / 0x2F | AUTOSAR CRC8H2F |
| CRC-16 / CCITT | Two bytes, either endianness |

### By signal, on a DBC row

On a message from a database, *Put it in* offers the message's **signals** by
name as well as a byte position.  Name one and the database decides where the
bits go -- which is both harder to get wrong than counting bytes and less work
underneath: a signal that is three bits straddling a byte boundary, in either
of CAN's two bit-numbering conventions, is the database's problem and not
yours.

A checksum named this way is computed over the frame **with its own signal set
to zero**, rather than by leaving whole bytes out.  A signal can share a byte
with data that has to survive, so dropping the byte would drop that too.

The counter's width comes from the database as well, so a one-bit counter
counts 0, 1, 0, 1 without being told to.

Whatever you type into a counter or checksum signal in the expanded row is
overwritten when the frame is sent -- the point of naming it is that pycangui
fills it in.

**Not in the list?**  A maker's own arithmetic is nobody's standard, so it
goes in the `transmit.checksum` [hook](hooks.md): return a number and it is
written wherever the dialog says, return `None` and the chosen algorithm
stands.  The hook is handed the payload with the counter already in it, which
is the same view the built-in algorithms get.

The dialog shows the next few frames as bytes before you send anything.  A
wrong checksum is invisible from the sending end -- the frames go out looking
perfectly healthy -- so seeing them written out is the only check available
without a device to reject them.

**One thing changes when you use either.**  A message with a counter or a
checksum is sent by pycangui's own timer rather than handed to the adapter as
a repeating message, because every frame has to differ and an adapter repeats
fixed bytes.  Expect slightly less even timing than a hardware-timed cyclic
message would give you.  Messages without either are unaffected.

A one-shot *Send* of a counted message advances the counter too: a receiver
has no idea which button sent a frame, and one that repeated the last count
would be rejected like any other repeat.
