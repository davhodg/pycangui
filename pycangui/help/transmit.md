[&larr; Contents](manual.md)

# CAN Transmit

A transmit pane only sends while it is on screen. Closing one stops whatever
it was repeating -- frames arriving on a live bus from a pane nobody can see is
the hardest sort of fault to find, since nothing on screen accounts for them --
and it says so in the Event Log. Bringing it back does not start them again.
Tabbing a transmit pane behind another, or detaching it into a window of its
own, are not putting it away: it is still on screen and still sending.

*Stop all cyclic* means all of them, in every transmit pane, however many are
open.

Each **CAN Transmit** pane holds one list of everything it sends, with three kinds
of row: **raw** (type the id and bytes), **DBC** (pick a message from a loaded
database and edit its signals in physical units), and **CANopen RPDO** (pick a
node's RPDO -- press *Read PDO config* in the CANopen pane first -- and edit
its mapped variables). All three are behind one *Add* button, since the
choice is which source rather than which button. Expand a row to see its
signals; the encoded bytes update as you type, and a message that is already
cycling is updated live.

A signal the database gives **named values** (a `VAL_` table) is picked from a
list: double-click its value and choose, or type a number or a name into the
same box. However it goes in, it is shown as name and number together --
*Run (1)* -- and the number is what is sent. A number the table does not name
is sent as that number; a name it does not have is refused, and says so in the
Event Log, rather than being sent as zero.

A **CANopen RPDO** row has the same list for a mapped object whose values are
named, by the EDS or by your `object_display` [hook](hooks.md).

A DBC or RPDO row takes its name, identifier and data from where it came from,
so those cells are not typed into -- but its **Period ms** is yours to set,
like a raw row's. A row added from a database starts at the cycle time the
database gives the message (`GenMsgCycleTime`) where it gives one, and at
100 ms where it does not. Hover over a DBC row's name for the database file it
came from. If that database is removed the row says *not loaded*, and the
same tooltip still names the file to load.

Whether an **ID** is 11-bit or 29-bit is in how it is written, so there is no
box to tick: anything above `7FF` is 29-bit, and so is an id written out in
eight digits. `123` is the 11-bit id 0x123, and `00000123` is the 29-bit one.

Select several rows -- Ctrl+A takes the lot -- and *Send selected*, *Remove
selected* and the space bar all work on the whole selection, so starting or
stopping a set of cyclic messages is one keypress rather than one tick per row.
*Send selected*, *Counter / checksum...* and *Remove selected* are greyed
until a message is selected.

## Counters and checksums

A message that carries a rolling counter and a checksum over its own bytes is
completely ordinary -- most safety-relevant messages have both -- and a
receiver that checks either one rejects every frame of a message that never
changes. Select the row and press *Counter / checksum...*.

A **counter** goes in a whole byte or in either nibble of one, since a
four-bit counter sharing a byte is at least as common as a whole one. It
starts where you say, steps by what you say, and wraps at whatever the field
holds unless you give it a smaller number -- plenty of protocols count 0 to 5
in a nibble that could hold sixteen.

A **checksum** is computed *after* the counter has been written, over the
whole message except its own bytes. That default is the usual rule and the
one that is easy to get wrong: including the checksum's own bytes means
hashing a field that is about to be overwritten, so the number never matches
at the other end. Give it an explicit byte range where a protocol wants one.

| Algorithm | Where you meet it |
|-----------|-------------------|
| XOR, 8-bit sum | Simple in-house protocols |
| Sum, two's complement | The bytes plus the checksum total zero |
| CRC-8 / SAE J1850 | The catalogue's: start 0xFF, final XOR 0xFF |
| CRC-8 / SAE J1850, start 0x00 | The same polynomial with no start value and no final XOR, which many devices mean by "J1850" |
| CRC-8 / 0x2F | AUTOSAR CRC8H2F |
| CRC-16 / CCITT | Two bytes, either endianness |

### By signal, on a DBC row

On a message from a database, *Put it in* offers the message's **signals** by
name as well as a byte position. Name one and the database decides where the
bits go -- which is both harder to get wrong than counting bytes and less work
underneath: a signal that is three bits straddling a byte boundary, in either
of CAN's two bit-numbering conventions, is the database's problem and not
yours.

The message's own row says where such a field is, not what it is called: *count
52:4* is a counter in the signal starting at bit 52, four bits long, as the
database numbers them. A signal's name can be long and that is one cell of the
row. Hover over it for the name, or expand the message -- the signal's own row
is marked.

A checksum named this way is computed over the frame **without its own
bytes**, exactly as one at a position is, where the signal is whole bytes --
a CRC in the last byte, say. Where it shares a byte with data that has to
survive, leaving the byte out would drop that too, so the frame is hashed
whole with the checksum's bits at zero. (Earlier versions always did the
second, which for a CRC is a different number from the one a receiver
computes: a zero byte is not the same as no byte.)

The counter's width comes from the database as well, so a one-bit counter
counts 0, 1, 0, 1 without being told to.

A signal named as a counter or a checksum stops being editable in the
expanded row: it is greyed, the *Counter / checksum* column says which of the
two it is, and the value is filled in as the frame is sent. That is the point
of naming it -- and a box that took an edit and then ignored it would be a box
that had lied. Stop computing it in the dialog and it goes back to being an
ordinary signal you can type into.

AUTOSAR E2E profile 1 uses the J1850 polynomial too, but also hashes a data
ID that is not in the frame, so it is not either of the entries above: it is
a hook's job.

**The bytes are tinted on the message itself.** In the list, the hex digits a
counter will overwrite have a blue background and a checksum's a green one --
a single digit where the field is a nibble -- so it shows on the row which
bytes are not there to be set.

**Not in the list?** A maker's own arithmetic is nobody's standard, so it
goes in the `transmit.checksum` [hook](hooks.md): return a number and it is
written wherever the dialog says, return `None` and the chosen algorithm
stands. The hook is handed the payload with the counter already in it, which
is the same view the built-in algorithms get.

The dialog shows the next few frames as bytes before you send anything. A
wrong checksum is invisible from the sending end -- the frames go out looking
perfectly healthy -- so seeing them written out is the only check available
without a device to reject them.

**One thing changes when you use either.** A message with a counter or a
checksum is sent by pycangui's own timer rather than handed to the adapter as
a repeating message, because every frame has to differ and an adapter repeats
fixed bytes. Expect slightly less even timing than a hardware-timed cyclic
message would give you. Messages without either are unaffected.

A one-shot *Send* of a counted message advances the counter too: a receiver
has no idea which button sent a frame, and one that repeated the last count
would be rejected like any other repeat.
