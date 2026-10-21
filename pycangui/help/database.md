[&larr; Contents](manual.md)

# Database

What is in the CAN databases that are loaded: every message, its signals, and
where each signal's bits are. *View > Database* opens it, in a window of its own
the first time.

It shows the databases pycangui has loaded -- the ones under *File > Remove DBC*
-- and no others. **Load...** is *File > Load DBC...*, from here. Nothing in
this pane changes a database or the file it came from.

## Finding a message

The list on the left has every message, lowest identifier first, with its
signals underneath when it is opened.

- **Find** shows only the messages with what you type in their name or their
  identifier, or with a signal that has it in its name. A message found by one
  of its signals is opened to show it.
- **File** shows the messages of one database only.

With two databases loaded, a message of the same name or on the same identifier
in both is decoded with the one loaded first. The other is listed in grey, and
its tooltip says which file is used in its place.

## The chosen message

**Message** has its identifier, length, cycle time and sender, its comment, and
the file it is from.

- **Add to Transmit** adds it to [CAN Transmit](transmit.md), as *Add from
  DBC...* there does.
- **Show in Trace** puts its name in the [CAN Trace](trace.md)'s filter box, so
  only its frames are listed.

**Layout** draws the message's bytes, a row each, with bit 7 at the left. Each
signal has a colour and its name across its bits. An Intel signal runs up from
its start bit; a Motorola one starts at its most significant bit and runs down
the byte and on from the top of the next, which is why it is easier seen than
worked out. A bit no signal has shows its number. A bit two signals both have
is outlined in dashes: it is a fault in the database. Point at a bit to be told
what it is; click one to choose its signal.

**A multiplexed message** -- one whose signals take turns at the same bits,
chosen by the value of one of them -- is drawn for one value of its multiplexer
at a time, chosen in the box beside *Multiplexer* at the top of the frame. The
signals that come with that value are drawn, with those that are always there,
and the others are greyed in the list. Choosing a greyed one draws a value it
comes with. A multiplexer under the top one is drawn with all of its signals.

**Signals** lists them:

| Column | |
|---|---|
| Bits | start bit and length, as the database has them: *16:8* |
| Scale | the factor, and the offset if there is one: *0.125*, *1, -125* |
| Range | minimum and maximum with the unit, or how many named values |
| Now | the value last decoded, while the bus is connected |

Under the list is a line about the signal chosen: signed or not, its byte
order, its comment and its named values. **Plot** plots it in [Signals and
Plot](signals.md), once it has arrived.

## Changing a database

A DBC file can be changed here, without touching it: **double-click** a message
to change its name, identifier, length, cycle time, senders or comment, and a
signal -- in either list, or a bit of it in the layout -- to change where it is,
what kind of number it is, its scale, offset, range, unit, receivers, comment
and named values. **Right-click** adds and removes messages and signals.

- The changes are kept apart from the database pycangui decodes and sends with,
  which goes on as it was until the changes are saved. A message changed is in
  italics in the list, and a bar above it says how many changes there are.
- **Undo** takes back the last change; **Discard changes** all of them.
- **Save as...** writes a new file, never over the one it came from, and reads
  it back to check it says what was meant. Something wrong that would stop the
  file being read as meant -- two messages on one identifier, a signal past the
  end of its message, a name the format does not allow -- is said, and nothing
  is written until it is put right. Signals that share bits are said, and saved
  if you choose. Then it offers the new file in place of the old one: loaded
  instead of it now, and next time. A transmit row that names a message by a
  name it no longer has is said in the Event Log.
- Changes not saved are asked about before pycangui closes, before another
  workspace opens, and before the database being changed is removed: **Save**
  writes them as a new file first, **Discard** lets them go, and **Cancel** leaves
  everything as it was.
- One database is changed at a time, and only a DBC: pycangui reads KCD, SYM and
  ARXML, and writes DBC only.
- **Multiplexing** is in a signal's dialog. Tick **Multiplexer** for a signal
  whose value chooses which of the others are in a frame -- a page, a mode, a
  group -- and for each of the others choose under **Sent**: *In every frame*,
  or *When* a multiplexer *is* one of the values given, `0` or `0, 2-4`. A
  multiplexer can itself be sent under another. A value too big for its
  multiplexer's bits is said when saving.
- **Value tables** are named values kept once, by name, for any signal to use.
  Right-click and choose **Value tables...** to add, rename (double-click the
  name) and remove them, and to change their values. **From value table**, in a
  signal's dialog, copies one into its named values.
- **Nodes**, on the right-click: add, rename and remove the database's nodes,
  and give each a comment and its attributes. A node renamed is renamed where it
  sends and receives; one still sending or receiving is not removed. Beside
  **Senders** and **Receivers**, **Choose** ticks nodes in or out of the list.
- **Signal groups**, on a message's right-click: groups of its signals, each
  with how often it is repeated.
- **Attributes** are in the message's and the signal's dialogs, and the
  database's own in **Database attributes...**: left empty, or on *Default*, one
  has the default shown beside it. Which attributes there are -- what each is
  of, its type, its range or its choices, its default -- is in **Attribute
  definitions...**; what one is of and its type cannot change while it is set
  anywhere. Two attributes are fields under another name: *GenMsgCycleTime* is
  the message's cycle time and *GenSigStartValue* a signal's initial value. Two
  are not set here, because the format writes them itself: *VFrameFormat*, from
  whether a message is 29-bit and CAN FD, and *Baudrate*.
- A rename follows through the relation attributes too (*BA_REL_*, such as a
  receiver's timeout for one signal), which are kept as they were and not
  changed here, as are environment variables.
