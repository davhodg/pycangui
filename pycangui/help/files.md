[&larr; Contents](manual.md)

# Your files

pycangui keeps what it remembers for you in one folder. It is `%APPDATA%\pycangui` on
Windows, `~/.config/pycangui` on Linux (or under `$XDG_CONFIG_HOME` if you set
it), and `~/Library/Application Support/pycangui` on macOS; setting
`PYCANGUI_HOME` puts it somewhere else.

Most of it lives in a *workspace* -- `workspaces/default/` unless you make
others -- which holds:

- [`hooks/*.py`](hooks.md), small Python functions pycangui calls at decision
  points (which EDS to use for a node, how to name it, ...) with the defaults
  and commented examples in place -- *Tools > Reload hooks* applies edits
  without a restart;
- `eds/`, scanned for EDS files matching a node's vendor/product;
- `nodes/`, the [simulated nodes](virtual.md);
- `plugins/`, the [plugins](plugins.md) installed in this workspace;
- `custom_panes/`, the [panes you have built](custom-panes.md);
- `settings.json` and `layout.json`.

`settings.json` is sorted, indented JSON with dotted keys, meant to be read and
hand-edited: the channels and their adapters, the databases loaded, the
transmit list, which trace groups are hidden, the trace view mode, the plot
window, the UDS and XCP addresses, whether DBC checks are strict. Settled
choices are kept; passing state -- a search box, a paused view, the selected
row -- is not, because starting up paused would be a bug rather than a
convenience. [`layout.json`](panes.md) holds the dock arrangement, which is Qt's own
opaque data rather than anything to read; *View > Reset layout* puts it back.

Outside the workspaces are the things that belong to the machine rather than
to what you are working on: `backends/`, which is about being able to talk to a
bus at all, the window's position on screen, and the questions you have told
pycangui not to ask again -- all of which stay in `QSettings`. The last of
those is outside a workspace on purpose, so that an agreement about disturbing
equipment cannot travel inside a folder you hand to somebody else.

## Where file dialogs open

A file dialog opens where you last used **that sort of file**. Open an EDS and
the next EDS dialog starts where that one was; a firmware image, a captured log
and a CAN database each keep their own folder, so picking one does not move the
others. The folders live in the workspace, since which folder a product's
files are in is a fact about that product and should not follow you into the
next one.

A remembered folder that no longer exists -- a memory stick unplugged, a folder
deleted -- is ignored, and the dialog opens at its default instead. *Tools >
Reset > Forget remembered folders* puts every one of them back to pycangui's
own folders at once.

## Starting again

*Tools > Reset* holds every way back, so that putting something right is one
place to look rather than four:

| Entry | What it puts back |
| --- | --- |
| Reset layout | Panes where they start, and the window at the size it opens at |
| Forget remembered folders | Every file dialog, to pycangui's own folders |
| Ask about everything again | Every question you told pycangui to stop asking |
| Restore supplied files... | Pycangui's own [hook files](hooks.md) and simulated nodes, renaming yours to `.py.bak` |
| Reset everything... | Nothing -- it explains that a new workspace is the clean slate |

That last one is not evasion. Everything that accumulates is in the workspace,
so a new one already *is* a reset of all of it, and it is the version that
deletes nothing: the workspace you were in is still there to switch back to.
A reset that undid it all in place would have to delete hook files, simulated
nodes and plugins -- code you wrote -- as a side effect of putting window sizes
back. Two things are yours rather than the workspace's and carry across to a
new one: the questions you have stopped being asked, which *Ask about
everything again* brings back, and `backends/`, which only ever holds what you
put there.
