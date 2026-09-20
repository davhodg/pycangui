[&larr; Contents](manual.md)

# Databases, signals and the plot

**File > Load DBC...** decodes matching frames, from a DBC, KCD, SYM or ARXML
database. *File > Remove DBC* lists the ones this workspace knows of, each with
how many messages it holds, and takes one off the list; *Remove all* at the
bottom clears the lot. A database that has moved since it was loaded is on
that list marked *(missing)*, which is how it is dropped without disturbing
the others. Databases are checked
strictly, and one that fails the check -- overlapping signals, a signal past
the end of its message, both common in files real tools produce -- is offered
for loading anyway rather than simply refused; turning off *Tools > Strict DBC
checks* stops the asking. Signals with a `VAL_` table can
be transmitted by name or by number, and the names are listed in the tooltip.
A loaded database is loaded again next time. One kept outside the workspace
can be copied into it when you load it, so it travels with the workspace; see
[Workspaces](workspaces.md).

Once loaded: the trace shows the message
name and the **Signals and Plot** pane lists every signal with its live value.
CANopen TPDO values appear there too. Tick *Plot Y1* on any signal to draw it
alongside the list (rolling window, pause, follow); drag the splitter to give
the plot the whole pane, or the list. `resources/demo.dbc` matches the demo
device.

The filter box above the list narrows it to the signals whose names match, and
*Unplot all* takes everything off the plot at once.

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

**Window** is how many seconds of signal the plot shows, from half a second to
an hour. **Pause** holds the plot still while samples carry on being
collected, **Fit** zooms to everything plotted, wherever in time it is, and
**Clear history** throws away the samples collected so far, for every signal.

**Slow refresh** redraws four times a second instead of the usual twenty, for
a bus busy enough that the curve is a shimmer. It is the same tick box the
[trace](trace.md) has and makes the same promise: it changes the screen and
nothing else, so every sample is still collected, plotted and exported.

*Follow* keeps the newest samples in view as they arrive. It follows the
**data**, not the clock, so it stops when the data does: a quiet bus, or a
disconnected one, holds the trace still rather than scrolling it off the left
edge.

Both Y axes share the one time axis, so *Window*, *Follow* and *Fit* apply to
signals on either side, and *Fit* scales each Y axis to its own signals.

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

**They will not be on screen until you untick *Follow* and press *Fit*.** A
file sits at the times it was recorded at -- 235 seconds into somebody's test,
or last Tuesday -- and *Follow* keeps the last few seconds of the live trace
in view, which is a different part of the number line entirely. *Fit* zooms
to whatever is plotted, wherever it is.

Reading MDF needs the `asammdf` library. The Windows installer includes it,
and a `pip` installation offers to fetch it the first time you open a file that
needs it, or you can ask for it up front with `pip install pycangui[mf4]`.

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
