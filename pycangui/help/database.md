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
