[&larr; Contents](manual.md)

# CAN Trace

The trace narrows down in three ways, none of which discard anything: the
**filter box** matches text against the id, the decoded name, the channel and
the data (`185`, `txpdo`, `drive bus`, `de ad`; several words must all match),
the **Filter** menu hides whole protocol groups or channels, and **Pause**
holds the display still while capture and recording carry on. The row count
next to the buttons reads *shown of captured*.

**Slow refresh** is the fourth, and it narrows nothing at all: it repaints
four times a second instead of twenty, for a bus busy enough that the rows
are a blur. The frames still arrive, are classified, recorded and exported
exactly as before -- they are added in one go at the end of each quarter
second rather than a dribble at a time, which is also why it costs less to
draw. Kept per pane, so a trace opened on a quiet bus need not have it.

## What names a frame

The **Kind** column says what a frame is, and nothing is named on the strength
of its identifier alone. In order: a [`trace.frame_kind` hook](hooks.md), which
is this workspace's own answer; then the [CAN databases](signals.md) you have
loaded, which name what they were written to name; then each protocol, and only
where it has been told what it is looking at -- the nodes
[CANopen](canopen.md) has heard from or you have added by hand, the addresses
in the [UDS](uds.md) pane, the identifiers in the [XCP](xcp.md) pane.

That last point is the whole of it. CANopen's predefined connection set claims
0x180 to 0x67F, so reading every identifier that way labels an ordinary CAN bus
as a CANopen one, and hides the names from a database somebody loaded on
purpose. Until a node is known, those ids are just ids. Where an EDS says a
node's PDOs are somewhere other than the predefined places, that is what is
used -- which in practice means a DCF, since it carries the identifiers a node
was configured with, where an EDS often declares the objects and leaves the
values to `$NODEID` or to nothing at all. A PDO switched off in the
configuration is left at its predefined place, since it is not on the wire to
be named.

A frame nothing can account for keeps its identifier and sits under *Other*.
*Help > Known CAN ids...* lists everything that would be named and where each
name comes from, which is the difference between "nothing was told about this
id" and "this id is not where it was expected".

The **Filter** menu lists the protocol groups -- NMT, SYNC/TIME, EMCY, PDO,
SDO, Heartbeat, LSS, UDS, J1939, XCP, Bus errors and Other -- and every
channel that has been seen, with *Show all* to bring everything back.
**Columns** chooses which columns show, in either view, and a right-click on
the header does the same. **Autoscroll** keeps the newest frame in view, and
**Clear** empties the pane.

Select rows and press Ctrl+C, or right-click and choose *Copy*, to copy them as
text with the column headings, ready to paste into a report or a spreadsheet.

## Latest per ID, and what its numbers mean

*View: Latest per ID* keeps one row per identifier rather than one per frame:
the newest data, how many have been seen, and how fast they are arriving.

**Rate** and **Period** are the same measurement written two ways, taken over
a window of the last few seconds -- so they describe *now*, and a message that
changes rate says so within about five seconds. That is what you want while
watching a bus, and it is no use at all for "did this ever slip?", because by
then the evidence has left the window.

Every measured figure on this view is **redrawn twice a second**, whatever the
bus is doing: a number changing faster than about four times a second cannot
be read at all. The data, the count and the timestamps still change as frames
arrive -- those are what the pane is for. Nothing is smoothed to achieve it,
because on this view the variation *is* the measurement: a row whose figures
were averaged into stillness would be hiding the thing worth seeing. A row
that has just appeared, or one that has started again after stopping, shows
its rate at once rather than waiting for the next redraw.

So the **Columns** button offers five more that do not forget:

| Column | What it is |
| --- | --- |
| First | Bus time of the first frame counted |
| Period min | The shortest gap seen |
| Period avg | The mean gap -- over everything, not over the window |
| Period max | The longest gap seen |
| Jitter | Longest minus shortest |

Jitter is the one to sort by. A TPDO configured for 100 ms that reads
`min 99.8 ms, max 512 ms, jitter 412 ms` is arriving, is arriving at about the
right rate, and stalled once -- which none of Rate, Period or Count will tell
you, because all three have moved on. Zero jitter is an answer too, and reads
as `0.0 ms` rather than as a blank.

They start hidden, because fifteen columns at once is a table nobody reads.
What you turn on is remembered, per pane and per view, and a right-click on
the header does the same as the button. Everything is counted from when the
id first appeared, so **Clear** is how to start the numbers again.

Error frames are rows here like any other frame, under the *Bus errors*
group, so how many there have been and how fast they are arriving is answered
by these same columns -- including which identifier is producing them. What
the [Event Log](event-log.md) adds is the controller's own state, which is
not a frame and so cannot be a row.
