[&larr; Contents](manual.md)

# Custom panes

The object dictionary lists one object per row sorted by index, which is the
right way to *find* an object and the wrong way to *use* one. A **custom pane**
is a named group of them laid out as a form: the six parameters a job actually
needs, labelled, with the units on them, in one place.

It is a pane like any other -- it opens as a dock, it is in the View menu, it
is removed under *Remove pane*. The only thing "custom" says is that you built
it rather than the tool shipping it.

**Building one takes no code.** In the CANopen pane, select the objects in the
object dictionary -- several at once -- right-click, and *Add to a custom pane*.
Pick an existing one or make a new one; you are asked what to call it once, not
once per object. *View > Custom panes* opens any you have, and *New custom
pane...* makes an empty one.

Its own *Edit...* has **Add...** too, which matters when the object dictionary
is not the way in: building one at a desk against a DCF or EDS with no bus
present, or adding an object whose index you already have in front of you. It
searches whatever the pane is bound to, and takes a typed index either way -- a
typed one that turns out to be in the file arrives named, the same as a picked
one. *Move up* and *Move down* set the order the fields appear in, and
*Remove* takes the selected one off the pane.

It opens in a window of its own, in front of the main one. Drag it in to dock
it, drop it onto another to tab them, or leave two out side by side comparing
two nodes -- which is what they are for. Wherever you leave one is where it
opens next time.

## Where the values come from

*Values from* at the top of a pane chooses a node on the bus, or a DCF or
EDS file. A custom pane is a statement about a *product*; which controller you
point it at this afternoon is not, so it is not in the file. Pointing one at a file is
how a configuration is built at a desk and taken to the machine: edits change
the file in memory, and saving writes a DCF through the original text so its
comments survive.

## Polling

*Read* reads the pane once. **Poll** reads it over and over, so the values
follow the controller -- which is the only way to watch an object that is not
mapped to a PDO. The box beside it is how often, at most.

**The figure after the box is the rate actually achieved, not the one you
asked for**, and the difference is the point. An SDO read is a request and a
response on the bus against a controller that answers when it feels like it, so
a pane of twelve objects at 50 Hz is asking for six hundred round trips a
second and will not get them. When the two agree it just shows the rate; when
they do not it says so -- `12.0 Hz (asked for 50)` -- because a value read at
12 Hz that looks like it was read at 50 is the sort of thing conclusions get
built on.

A round only starts once the last one has finished, so asking for more than the
bus can do gets you as fast as it can rather than a growing backlog of stale
values. A box you are typing in is not overwritten by an arriving value.

While polling, every numeric field is pushed to **Signals and Plot** under the
source's name, so a polled object plots and exports to CSV like any other
signal. Reading once by hand does not, since a series of one point would only
fill the signal list.

Polling is offered against a node and not against a file: a file does not
change while you watch it.

## How an object is shown

Each field has one of seven kinds, changed in *Edit...*:

| Kind | What it is |
|---|---|
| `value` | read-only, shown in whatever units it is understood in |
| `number` | typed in its own units, refused if the EDS says it is out of range |
| `hex` | the same, in hex, for codes and masks rather than quantities |
| `enum` | a dropdown of the values that have names |
| `flags` | one named tick per bit |
| `bits` | a field packed into some of the bits of a larger object |
| `map` | an array as an editable table beside its graph |

Names, units, scaling, limits and bit meanings come from the EDS where it
carries them and from [`hooks/canopen.py::object_display`](hooks.md) where it
does not --
the same place the object dictionary gets them, so a pane and the tree agree.

Two things a pane will not do. It will not send a number outside the limits
the EDS declared, because a node is free to clamp it silently and a parameter
that did not take is worse than one that was not sent. And a `flags` or `bits`
field will not write until it has read: those write part of an object, part of
an object cannot be written, and a word made mostly of zeroes would clear
every bit the pane is not showing.

Custom panes are JSON in `workspaces\<name>\custom_panes\`, beside the hooks, and meant to
be edited: named bits, map axes and anything else the dialog does not cover
are a line in the file. One with something wrong in it still opens and
says what -- refusing would leave nobody able to see which field it was.
