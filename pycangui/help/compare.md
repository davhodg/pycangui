[&larr; Contents](manual.md)

# CANopen DCF compare

A [plugin](plugins.md) that ships with pycangui. *Plugins > Supplied with
pycangui* installs it, as does *Plugins > Manage plugins...*; until then it is
not there.

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

**An object held on one side and not the other is not counted as a
difference.** A DCF carries a value only for the objects that had one, and a
device answers only what it implements, so this is common and almost never a
difference in configuration; those rows say *left only* or *right only* and are
counted separately.

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
