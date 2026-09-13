[&larr; Contents](manual.md)

# Workspaces

A workspace is everything about the product you are working on: the
[hooks](hooks.md) that say what its objects mean, its EDS files, its channels and bitrates, its
databases, its transmit list, its watch lists and its pane arrangement. One
folder, so it can be copied, backed up or handed to a colleague whole.

**If you only ever work on one thing, you can stop reading here.** There is a
workspace called `default`, it was made without asking, and everything above
describes it. The title bar names a workspace only when it is *not* `default`,
so nothing on screen will ever mention this.

There is no Save and no unsaved changes: a workspace saves as you go, the way
settings always have. *File > Workspace* has three items.

- **Save as...** keeps everything as it is now under a new name and carries on
  in that one. It is a fork -- the workspace you were in is left exactly as
  you left it -- because what somebody means by it is "keep this and call it
  something else".
- **Switch to** opens another one. Everything reloads, including the channels:
  a workspace holds which adapter at what bitrate, and an adapter can only be
  in one of those states at a time. Switching while connected asks first,
  since it means dropping off the bus and stopping anything being sent
  cyclically.
- **Manage...** renames and deletes. Not `default`, which is the one that is
  always there, and not the one you are in -- switch away first, so the ground
  does not move under the window.

Only one workspace is open at a time. When it feels like you want two, what
you want is usually two [*panes*](panes.md): *View > Standard panes* gives a second trace or
a second plot side by side, within one workspace.
