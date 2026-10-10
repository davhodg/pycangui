[&larr; Contents](manual.md)

# Databases, signals and the plot

**File > Load DBC...** decodes matching frames, from a DBC, KCD, SYM or ARXML
database. *File > Remove DBC* lists the ones this workspace knows of, each with
how many messages it holds, and takes one off the list; *Remove all* at the
bottom clears the lot. A database that has moved since it was loaded is on
that list marked *(missing)*, which is how it is dropped without disturbing
the others. Databases are checked
strictly, and one that fails the check -- overlapping signals, a signal past
the end of its message, both common in files real tools produce -- is asked
about, and can be loaded anyway, rather than simply refused; turning off *Tools > Settings >
Strict DBC checks* stops the asking. Signals with a `VAL_` table are
picked from a list in [CAN Transmit](transmit.md), or typed by name or by number,
and are shown here with their names: *Run (1)* in the Value column, and in Min
and Max. So are a CANopen TPDO's objects where the EDS or a hook names their
values. The plot and *Export* keep the numbers.

**A named value is written one way everywhere** -- the name, then the number
it stands for, *Run (1)* -- in this list, in CAN Transmit, in the CANopen
object dictionary and in custom panes. Where one can be typed, *1*, *Run* and
*Run (1)* all mean the same.
A loaded database is loaded again next time. One kept outside the workspace
can be copied into it when you load it, so it travels with the workspace; see
[Workspaces](workspaces.md).

Once loaded: the trace shows the message
name and the **Signals and Plot** pane lists every signal with its live value.
CANopen TPDO values appear there too -- except a TPDO a loaded database also
describes, which is listed once, as the database decodes it: the database has
the names and scaling, and the PDO decode would put the same frame beside it
with raw numbers. Removing a database takes its signals off the list and the
plot, and a TPDO it described comes back as the PDO decode has it. Tick *Plot Y1* on any signal to draw it
alongside the list; drag the splitter to give
the plot the whole pane, or the list. `resources/demo.dbc` matches the demo
device.

The filter box above the list narrows it to the signals whose names match.
**Right-click** the list, or its *Plot Y1* or *Plot Y2* heading, to plot or
unplot several at once: *Plot all* and *Unplot all* for every signal, and over
a message the same two for that message's signals. Over the *Plot Y2* column
they go on the right axis, anywhere else on the left. *Plot all* means all
that are listed, so with *speed* typed in the filter it plots the speeds.

The same right-click has **Columns**. **Min** and **Max** sit beside the value
they are the extremes of; **Count** is the samples received and **Rate** the
samples a second, over the last second of the signal's own samples -- how fast
it was arriving when it last arrived, and 0 once it has stopped: after a couple
of seconds with nothing, or three of its own periods if that is longer, so a
slow signal is not called stopped for being slow. Those four are hidden until
asked for.
**Unit** is there too: shown to begin with, and it can be hidden. The
statistics are kept for every signal as it
arrives, whether shown or not and however much of the history has been
trimmed, so they cost next to nothing; *Clear history* starts them again. Each
Signals and Plot pane remembers which it shows.

Tick *Plot Y2* instead to draw a signal against a second Y axis on the right of
the plot, with a scale of its own, so that a speed in thousands and a
temperature in tens can be read on the same plot. A signal is on one axis at a
time: ticking the other box moves it there, and unticking the ticked one takes
it off the plot. The Y2 axis is only there while a signal is on it, and its
signals are marked *(Y2)* in the legend. Where every signal on an axis has the
same unit, the axis is labelled with it.

Each Signals and Plot pane remembers which signals it plots, and on which axis,
from one run to the next. A signal comes back onto the plot as soon as it
appears again: when its first frame is decoded, or when its file is imported.

**Time** is which stretch of time the plot shows, one choice of three:

- **Follow** keeps the newest samples in view, for the number of seconds in
  the box beside it -- from half a second to an hour. It follows the **data**,
  not the clock, so it stops when the data does: a quiet bus, or a
  disconnected one, holds the trace still rather than scrolling it off the
  left edge.
- **All** shows everything held for the plotted signals, wherever in time it
  is, and goes on showing all of it as more arrives. Choose it again to fit
  again.
- **Manual** leaves the plot where you put it. Dragging or zooming the plot
  chooses it for you.

On the right, **Export...** writes the samples held for every decoded signal --
plotted or not -- to a file a spreadsheet can read, the same as *File > Export
signals*, and **Clear history** throws those samples away. Every decoded
signal is kept whether it is plotted or not, which is why plotting one later
shows its past -- and the status bar says how much that is, as *3 signals
(200k limit/signal), 1.2M total samples*: how many signals, the most kept of
any one of them, and how many samples are held between them all.

**The limit, and long traces.** 200,000 samples of each signal are kept to
begin with, the oldest going first, so a trace that runs long enough loses its
start: at a thousand samples a second that is a little over three minutes. The
limit is each signal's own, so the total passes it as soon as there are a few
signals, and a slow signal may never reach it. A signal is let grow to half as
much again and then cut back to the limit in one go, so the start of a plot
goes in steps and not a sample at a time. The status bar says how many signals
have lost samples that way: *2 reached*. The **Count** column is not held to the
limit: it is every sample that has arrived.
*Tools > Settings > Signal history* raises it -- 500,000, one million, two or five
million a signal -- and each choice says what it costs: the samples are held in
memory, at sixteen bytes each, and only a signal fast enough to fill the limit
uses it. Lowering it takes effect at once. *Count*, *Min* and *Max* cover every
sample that arrived, whatever has been dropped. A trace longer than the largest
choice holds needs the samples kept on disk, which pycangui does not do yet.

The first menu says how the plot keeps up, the same choice the
[trace](trace.md) has, with the same promise: it changes the screen and
nothing else, so every sample is still collected, plotted and exported.
**Live refresh** redraws twenty times a second; **Slow refresh** four times, for a bus
busy enough that the curve is a shimmer; **Paused** holds the plot still to be
looked at, and what arrived meanwhile is there when you go back.

Both Y axes share the one time axis, so the *Time* choice applies to signals
on either side, and *All* scales each Y axis to its own signals.

## Importing a measurement file

**File > Import signals...** reads an **MDF** or **MF4** file -- what a
measurement tool or a data logger records -- and puts its signals on the plot
beside the live ones.

A CAN log and a measurement file are different things with similar names, and
picking the wrong door is the usual confusion. A log holds **frames** and is
[replayed](channels.md) onto a channel; a measurement holds **signals somebody
already decoded**, and there are no frames in it to replay. A file can hold
both, and then the pane says so.

A real export holds thousands of channels, so nothing is read until you have
said what you want: the file is described from its header, and you filter and
tick. Frame fields -- the id, the length, the flags a bus log carries -- are
kept out of the list unless you ask for them, since there are hundreds and
they are plumbing rather than measurements.

Imported signals appear under the file's name, beside the live ones.

**They will not be on screen until you set *Time* to *All*.** A
file sits at the times it was recorded at -- 235 seconds into somebody's test,
or last Tuesday -- and *Follow* keeps the last few seconds of the live trace
in view, which is a different part of the number line entirely. *All* shows
whatever is plotted, wherever it is.

Reading MDF needs the `asammdf` library. The Windows installer includes it,
and a `pip` installation offers to fetch it the first time you open a file that
needs it, or you can ask for it up front with `pip install pycangui[all]`. The
download takes a while on a slow connection -- asammdf brings other libraries with
it -- so the window stays usable meanwhile, says what pip is doing, and has a
Cancel that stops it.

**File > Export signals...** writes what has been decoded to a CSV: DBC
signals, CANopen PDO values and XCP measurements alike. [Recording](channels.md) writes raw
CAN, which is right for a recording -- and what comes back the other way is
*Import signals*, above.

Each signal gets its own **Time (s)** column beside its values, with a blank
column between signals:

```
Time (s),DBC Engine/Speed (rpm),,Time (s),XCP/current (A)
0.010000,1500,,0.012000,12.4
0.020000,1520,,0.022000,12.6
0.030000,1490,,,
```

One shared time column would be tidier and would be a lie -- signals do not
arrive together, so a single timeline can only be built by interpolating, by
holding the last value, or by inventing a grid, and all three put numbers in
the file that were never on the bus. A signal that finishes early leaves its
cells empty for the same reason. The blank column between pairs is also what
stops a spreadsheet reading two signals as one series when you select a block
and ask it for a chart.
