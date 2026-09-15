[&larr; Contents](manual.md)

# Workspaces

A workspace is everything about the product you are working on: the
[hooks](hooks.md) that say what its objects mean, its EDS files, its channels and bitrates, its
databases, its transmit list, its watch lists and its pane arrangement. One
folder, so it can be exported as one file, backed up or handed to a colleague whole.

**If you only ever work on one thing, you can stop reading here.** There is a
workspace called `default`, it was made without asking, and everything above
describes it. The title bar names a workspace only when it is *not* `default`,
so nothing on screen will ever mention this.

There is no Save and no unsaved changes: a workspace saves as you go, the way
settings always have. *File > Workspace* has three items for working with
workspaces, and two for moving one between computers.

- **Save as...** keeps everything as it is now under a new name and carries on
  in that one. It is a fork -- the workspace you were in is left exactly as
  you left it -- because what somebody means by it is "keep this and call it
  something else".
- **Switch to** opens another one. Everything reloads, including the channels:
  a workspace holds which adapter at what bitrate, and an adapter can only be
  in one of those states at a time. Switching while connected asks first,
  since it means dropping off the bus and stopping anything being sent
  cyclically.
- **Manage...** renames, deletes and exports. Renaming and deleting are not
  offered for `default`, which is the one that is always there, or for the one
  you are in -- switch away first, so the ground does not move under the window.
- **Export...** writes the workspace you are in to one zip file. To export a
  different one, use *Export...* in *Manage...*.
- **Import...** makes a new workspace from a zip file.

## Giving a workspace to someone else

*Export...* asks where to save the file and writes the workspace folder into
it: settings, layout, hooks, simulated nodes, EDS files, custom panes and
plugins. A few things are left out because they belong to your computer rather
than to the product:

- the numbered `.bak` copies that *Tools > Reset > Restore supplied files...* keeps of your own edits
- `__pycache__` folders and `.pyc` files, which Python makes again
- the folders each file dialog last opened in, and the list of recently replayed logs, since those are paths on your disc

Where the window sits on the screen is not in the workspace to begin with, so
the person you send it to keeps their own.

### Files kept outside the workspace

A workspace remembers files it did not make: the CAN databases you load, the
A2L, and an EDS you chose by hand for a device. Keep those wherever you like.
When you add one from outside the workspace, pycangui asks whether to copy it
in, into the workspace's `dbc`, `a2l` or `eds` folder. **No** keeps using the
file where it is. A file inside the workspace is remembered relative to it, so
it is still found wherever the workspace is unpacked.

*Export...* checks again. If the workspace uses files kept outside it, it lists
them and asks whether to copy them in first. **Yes** copies them and points the
workspace at the copies. **No** exports without them, and on another computer
those links will not work; pycangui says so there, and a remembered EDS that is
missing falls back to the search of the EDS folder. **Cancel** exports nothing.

*Import...* asks for the file and shows the files it will write before it
writes anything. It always makes a new workspace, named after the file. If you
already have a workspace with that name, it asks for another and suggests the
next free one, such as `drive 2`. An existing workspace is never replaced.
When the import is done it offers to open the new workspace.

A workspace can hold hooks, simulated nodes and plugins, and those are Python
that runs as part of pycangui. Import workspaces you would be willing to run
yourself.

Import refuses a file that is not a pycangui workspace, meaning a zip with no
`settings.json` at the top level or in a single top folder. It also refuses a
zip that would write outside the new workspace folder, and one that is
unreasonably large: more than 256 MB unpacked or more than 10,000 files.

The zip is an ordinary zip, so you can open it with any archive tool to see
what is inside. A zip you make yourself from a workspace folder imports in the
same way.

## One at a time

Only one workspace is open at a time. When it feels like you want two, what
you want is usually two [*panes*](panes.md): *View > Standard panes* gives a second trace or
a second plot side by side, within one workspace.
