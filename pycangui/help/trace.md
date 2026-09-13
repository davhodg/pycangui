[&larr; Contents](manual.md)

# CAN Trace

The trace narrows down in three ways, none of which discard anything: the
**filter box** matches text against the id, the decoded name, the channel and
the data (`185`, `txpdo`, `drive bus`, `de ad`; several words must all match),
the **Filter** menu hides whole protocol groups or channels, and **Pause**
holds the display still while capture and recording carry on. The row count
next to the buttons reads *shown of captured*. Select rows and press Ctrl+C
to copy them as text.

## Latest per ID, and what its numbers mean

*View: Latest per ID* keeps one row per identifier rather than one per frame:
the newest data, how many have been seen, and how fast they are arriving.

**Rate** and **Period** are the same measurement written two ways, taken over
a window of the last few seconds -- so they describe *now*, and a message that
changes rate says so within about five seconds. That is what you want while
watching a bus, and it is no use at all for "did this ever slip?", because by
then the evidence has left the window.

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
