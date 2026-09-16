[&larr; Contents](manual.md)

# Workspaces

A workspace is everything about the product you are working on: the
[hooks](hooks.md) that say what its objects mean, its EDS files, its channels and bitrates, its
databases, its transmit list, its watch lists and its pane arrangement. One
folder, so it can be exported as one zip file.

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
into the workspace's `dbc`, `a2l` or `eds` folder. **No** keeps using the
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
next free one, such as `drive 2`. Import is designed never to replace an existing workspace.
When the import is done it offers to open the new workspace.

A workspace can hold hooks, simulated nodes and plugins, and those are Python
that runs as part of pycangui. Only import workspaces you would be willing to run
yourself.

Import is designed to refuse a file that is not a pycangui workspace, meaning a zip with no
`settings.json` at the top level or in a single top folder. It also refuses a
zip that would write outside the new workspace folder, and one that is
unreasonably large: more than 256 MB unpacked or more than 10,000 files.

The zip is an ordinary zip, so you can open it with any archive tool to see
what is inside. A zip you make yourself from a workspace folder imports in the
same way.

### Sharing on a computer with several logins

Workspaces live in your own user folder (see [Your files](files.md)), so on a
lab computer each person who logs on has their own. There are two ways to
share.

**Export and import.** Export to a folder everyone can reach, such as
`C:\Users\Public` or a network drive, and each person imports from there. An
import is a copy, so everyone can change their own without affecting anyone
else; a change reaches the others only when it is exported and imported again.

**One folder for everyone.** Set the `PYCANGUI_HOME` environment variable to
the same folder for every login -- for all users in the system settings, or in
a shortcut or script that starts pycangui -- and everyone uses the same
workspaces. The folder has to be writable by all of them. Changes one person
makes, including to hooks, simulated nodes and plugins, are what the next
person gets, so agree who looks after them. The "do not ask me this again"
answers stay with each login. It suits people taking turns at the computer;
pycangui saves as you go and does not lock anything, so two logins using the
same folder at the same moment overwrite each other's changes.

Setting the variable for every login needs administrator rights:

- **Windows:** open *Start*, search for *Edit the system environment
  variables*, press **Environment Variables...**, and under **System
  variables** press **New...**. Name it `PYCANGUI_HOME` and give it the
  folder, for example `C:\Users\Public\pycangui`. From an administrator
  command prompt, `setx PYCANGUI_HOME "C:\Users\Public\pycangui" /M` does the
  same.
- **Linux:** add `PYCANGUI_HOME=/srv/pycangui` as a line in
  `/etc/environment`.
- **macOS:** there is no single place that reaches applications started from
  the Dock. Start pycangui from a script instead, with
  `export PYCANGUI_HOME=/Users/Shared/pycangui` before it.

A login only picks up the change after logging off and on again. To use a
shared folder for one shortcut or script instead of the whole computer, set
the variable there: `set PYCANGUI_HOME=C:\Users\Public\pycangui` on the line
before pycangui starts in a `.cmd` file, or
`PYCANGUI_HOME=/srv/pycangui pycangui` on Linux and macOS.

To give everyone write access to the folder on Windows, open its
*Properties*, then *Security*, *Edit...*, select **Users** and tick
**Modify**. On Linux and macOS, a common group with write permission does the
same.

## One at a time

Only one workspace is open at a time. When it feels like you want two, what
you want is usually two [*panes*](panes.md): *View > Standard panes* gives a second trace or
a second plot side by side, within one workspace.
