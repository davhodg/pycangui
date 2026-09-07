[&larr; Contents](manual.md)

# Panes and layout

pycangui opens with three panes: the **CAN Trace** with the **Event Log** beside
it, and **Signals and Plot** spanning underneath.  The rest (CANopen, UDS, J1939,
XCP, Transmit, Python Console) start hidden, because which of them you want
depends on what you have plugged in; turn any on in the **View** menu, and
*View > Reset layout* puts everything back.  Panes are dockable, so drag them
where you like: the arrangement is remembered.

Drag a pane out of the window, or double-click its title bar, and it floats.
Hold **Ctrl** while dragging one, or it will dock again at the first
opportunity -- the main window is looking for somewhere to put it the whole
time.  Drag it back to dock it, or use *View > Dock all panes*.

A pane that is out grows two buttons at its top right, and loses them again
when it goes back.  Each says what pressing it will do, so **Pin** becomes
**Unpin** and **Detach** becomes **Attach**; hover for what they mean.

Pinning keeps a pane above every other window, pycangui's and everyone
else's -- except while pycangui is asking a question, when a pinned pane
stands down so the dialog can be seen and answered, and goes back on top
afterwards.  Detaching takes it out of its dock altogether, into a window with no
parent: nothing then tries to dock it, it gets a taskbar entry of its own, and
it can be sent to another display and left there.  Attaching puts it back
where it came from -- floating if that is where it was, docked if not --
while closing that window closes the pane, as closing a docked one does, and
*View* shows it again.  *View > Dock all panes* gathers everything up.

Which panes are out on their own, and which are pinned, are remembered like
the rest of the settings: a pane left on a second monitor is still there next
time.

## More than one of a pane

*View > Standard panes* opens a second [**CAN Trace**](trace.md),
[**Signals and Plot**](signals.md) or [**Transmit**](transmit.md) -- the panes pycangui comes with that you can have more than one
of -- numbered after the first: CAN Trace 2, CAN Trace 3.  Each has settings
of its own: its own filter, its own mode, its own plotted signals, its own
list of messages, all remembered separately.  Two traces of the same capture
filtered differently, a plot per subsystem, or the background traffic a rig
needs left running beside a scratch list to try something in, are what this is
for.

Each entry reads *Additional CAN Trace*, because that is what you get: a new
pane with settings of its own, not a copy of the one you were looking at and
not the one that is already open.

A pane you open arrives **in its own window**, in front of the main one, rather
than squeezing into the space the panes already on screen were using.  Drag it
into the main window to dock it, or onto another pane to tab the two together;
wherever you leave it is where it opens next time.  Otherwise it behaves like
any other pane -- pin it, detach it, close it.

*View > Rename pane* calls one whatever the job calls it: two traces are much
clearer as **Drive bus** and **Errors** than as CAN Trace and CAN Trace 2, and
so are two transmit lists as **Background** and **Scratch**.  An empty name
puts the default back.  Only the label changes -- what identifies a pane to the
saved layout is untouched, so renaming one cannot cost you the arrangement you
were renaming.

Closing one puts it away and *View* brings it back, exactly as for the panes
that are always there.  *View > Remove pane* is the other thing: it closes one
for good and forgets it.  The first pane of each sort cannot be removed --
that one is the pane.

Controls whose effect is not written on them explain themselves on hover: the
UDS service behind a button, what a routine's Start actually sends, that
clearing DTCs takes the freeze frames with them, that an RPDO needs the node's
configuration read first.  The obvious ones -- Clear, Remove, Connect -- are
left alone, since a tooltip repeating its label is noise.
