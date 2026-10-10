[&larr; Contents](manual.md)

# CANopen DCF compare

Opened with **Compare...** in the [CANopen](canopen.md) pane, which puts the
node or file selected there on the left. It is a window of the CANopen pane's
own, there while a comparison is wanted: not a pane, so it is not in the View
menu and takes no part in the arrangement that is saved. The CANopen pane
stays usable beside it, to look something up with the differences still in
front of you. Closing it puts it away; *Compare...* brings the same one back,
with the comparison still in it, and what its two sides were set to is
remembered from one day to the next.

It was a plugin until pycangui 0.2. A copy of that plugin left in a workspace
is no longer run, and *Plugins > Manage plugins...* lists it as switched off,
saying so; it can be removed there.

Each side is a **DCF or EDS file**, or a **node on the bus**. Pick two, press
*Compare*, and what comes back is the objects they disagree about.

## The three comparisons worth making

**File against file.** Two dcf files offline

**File against a device.** The node is read when you press Compare, and **only for the objects the dcf file names**

**A device against its own EDS.** An EDS holds the defaults, so what comes back is everything that has been configured since.

Two devices can be compared as well, and then something still has to say which
objects to read -- the EDS loaded against one of them. With neither a file nor
an EDS in sight the pane says so, rather than reading a guessed-at range of
indices and calling the result a comparison.

## Reading the result

*Differences only* is ticked to begin with. Untick it to see everything,
including the objects that agree.

**Show** picks which objects are listed at all. *RO + RW* is everything;
*RW only* leaves out the read-only ones, which are measurements and nameplate
-- a speed, a temperature, a serial number. Two readings of those differ
because the machine was doing something at the time, not because anybody
configured it differently, so they are noise in the question *what has been
changed on this device*.

Access comes from an EDS or a DCF. A node read over SDO says nothing about it
-- the device answers with a value or an abort, and neither of those is "this
one is read-only" -- so the side that has a file answers for the side that
does not. An object **neither** side has a file for stays listed under *RW
only*, on purpose: a filter that hides what it cannot classify hides the thing
being looked for.

**An object held on one side and not the other is not counted as a
difference.** A DCF carries a value only for the objects that had one, and a
device answers only what it implements, so this is common and almost never a
difference in configuration; those rows say *left only* or *right only* and are
counted separately.

A long block of bytes -- a DOMAIN object -- is cut short in the table, with its
length, and shown whole when the comparison is copied. Every column can be
widened by dragging its edge.

**A value that changed form has not changed.** A file stores `1000` as text
which a parser turns into a number, and a device hands back a number; a value
that survived that round trip is not reported as a change.

**Different node-IDs are remarked on.** Half the communication objects in a
CANopen device are COB-IDs worked out from the node-ID, so a DCF taken from
node 5 against one from node 6 differs in every one of them -- correctly, and
uselessly. The pane says which node each side came from rather than leaving
you to work out why forty objects changed.

*Copy* puts the comparison on the clipboard as text.

## What it does not do

**It is designed only to read.** Comparing tells you what is different; putting it right
is *Apply DCF* in the [CANopen](canopen.md) pane.
