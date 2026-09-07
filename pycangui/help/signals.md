[&larr; Contents](manual.md)

# Databases, signals and the plot

**File > Load DBC...** decodes matching frames.  Databases are checked
strictly, and one that fails the check -- overlapping signals, a signal past
the end of its message, both common in files real tools produce -- is offered
for loading anyway rather than simply refused; turning off *Tools > Strict DBC
checks* stops the asking.  Signals with a `VAL_` table can
be transmitted by name or by number, and the names are listed in the tooltip.

Once loaded: the trace shows the message
name and the **Signals and Plot** pane lists every signal with its live value.
CANopen TPDO values appear there too.  Tick *Plot* on any signal to draw it
alongside the list (rolling window, pause, follow); drag the splitter to give
the plot the whole pane, or the list.  `resources/demo.dbc` matches the demo
device.

**File > Export signals...** writes what has been decoded to a CSV: DBC
signals, CANopen PDO values and XCP measurements alike.  [Recording](channels.md) writes raw
CAN, which is right for a recording and means decoding it again elsewhere to
get back what is already on screen here.

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
the file that were never on the bus.  A signal that finishes early leaves its
cells empty for the same reason.  The blank column between pairs is also what
stops a spreadsheet reading two signals as one series when you select a block
and ask it for a chart.
