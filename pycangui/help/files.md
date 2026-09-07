[&larr; Contents](manual.md)

# Your files

Everything in `%APPDATA%\pycangui` is yours.  Most of it lives in a
*workspace* -- `workspaces\default\` unless you make others -- which holds
[`hooks/*.py`](hooks.md), small Python functions pycangui calls at decision
points (which EDS to use for a node, how to name it, ...) with the defaults and
commented examples in place; `eds/`, scanned for EDS files matching a node's
vendor/product; `settings.json`; and `layout.json`.  Tools > Reload hooks
applies edits without a restart.

`settings.json` is sorted, indented JSON with dotted keys, meant to be read and
hand-edited: the channels and their adapters, the databases loaded, the
transmit list, which trace groups are hidden, the trace view mode, the plot
window, the UDS and XCP addresses, whether DBC checks are strict.  Settled
choices are kept; passing state -- a search box, a paused view, the selected
row -- is not, because starting up paused would be a bug rather than a
convenience.  [`layout.json`](panes.md) holds the dock arrangement, which is Qt's own
opaque data rather than anything to read; *View > Reset layout* puts it back.

Outside the workspaces are the things that belong to the machine rather than
to what you are working on: `backends/`, which is about being able to talk to a
bus at all, and the window's position on screen, which stays in `QSettings`.

## Where file dialogs open

A file dialog opens where you last used **that sort of file**.  Open an EDS and
the next EDS dialog starts where that one was; a firmware image, a captured log
and a CAN database each keep their own folder, so picking one does not move the
others.  The folders live in the workspace, since which folder a product's
files are in is a fact about that product and should not follow you into the
next one.

A remembered folder that no longer exists -- a memory stick unplugged, a folder
deleted -- is ignored, and the dialog opens at its default instead.  *Tools >
Forget remembered folders* puts every one of them back to pycangui's own
folders at once.
