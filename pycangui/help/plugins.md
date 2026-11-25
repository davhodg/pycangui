[&larr; Contents](manual.md)

# Plugins

A [hook](hooks.md) answers a question pycangui already knows to ask -- which EDS
for this node, what to call it -- from a fixed list of them. A plugin is the other
half: code that adds something that was not there, a pane of its own with its
own buttons, doing something pycangui has never heard of.

## Installing one

A plugin is distributed as a **package**: a plain zip with a `plugin.py` at the
top of it, which is what you send somebody. *Plugins > Install plugin...*
unpacks one into `workspaces\<name>\plugins\` and loads it, and its pane
opens straight away -- you asked for it, so you are shown what you got.

Each plugin has a menu of its own under *Plugins*, by its name. Its panes come
first, ticked while on screen, the same entries as in the View menu; then
anything else it added. Its description is the menu's tooltip, and a plugin
with nothing else there shows it, greyed.

Before it unpacks anything it says what is in the package and reminds you what
a plugin is: **Python that runs as part of pycangui, with everything pycangui
can reach.** Install ones you would be willing to run yourself. You can open
a package in any file manager and read it first; that is most of the reason it
is a plain zip.

It belongs to the [workspace](workspaces.md), the same as the hooks, because a screen for a
product is knowledge about that product. *Manage plugins...* is the rest of
it:

* **the tick beside each one** switches it on and off. Off is designed to mean *not loaded
  at all* -- no pane, no menu entries, and none of its code running -- while the
  folder stays exactly as you left it, edits and all.
* **Install from file...** installs a package, the same as *Plugins > Install
  plugin...*.
* **Update...** replaces one of pycangui's own plugins with the version this
  pycangui ships, and is available only when that one is newer. A plugin is
  installed into a workspace as a *copy*, so updating pycangui leaves the
  copy exactly as it was -- which is right for one you have edited, and not
  what you want for one you have not. The row says which version is
  available, the Event Log says so once when the window opens, and updating
  asks first, because it is a replacement rather than a merge. Mostly this
  goes by the version each one declares; where the supplied copy has changed
  without its version moving -- an author forgetting to say so is the
  ordinary mistake -- the files are compared instead, and the row says the
  supplied one differs. If the copy
  here has been changed since it was installed, it is moved aside and kept as
  `_name.bak` rather than lost -- the same bargain as a hook file's `.bak`,
  and the underscore is what keeps the copy from being loaded as a second
  plugin. An earlier `.bak` is never written over.
* **Export...** writes an installed plugin back out as a package, which is how
  one of yours gets to somebody else.
* **Remove...** deletes it from the workspace, and says so first: whatever you
  edited into it goes too.
* **Supplied with pycangui** lists the ones that ship with it and are not
  installed here -- the same list is a submenu of the **Plugins** menu, one
  click each. *Nothing pycangui ships is designed to load until you install it*, so a
  window you have not asked anything of has no plugin panes in it at all.

Installing a supplied plugin puts a copy in your workspace, and that copy is
the one that runs -- so editing it is editing yours rather than the
installation, and *Reload plugins* picks the edit up without a restart.

The **Plugins** menu is also the answer to what you have installed: every
plugin appears there whether or not it added any entries of its own, one that
is switched off says so, and one that failed to load appears greyed out rather
than silently not being there.

## Writing one

```python
NAME = "Thermistor setup"
VERSION = "1.0"  # yours; shown in the menu, and compared when one replaces another
API_VERSION = 1  # what it was written against; refused if newer than pycangui


def register(app):
    app.add_pane("main", "Thermistor setup", build_the_widget)
    app.add_menu_action("Do the thing", run_it, "what it will do")
```

Say in the name what the plugin talks to, where that is not obvious: the ones
supplied here is *CANopen* motor control, because its sequence is not the
one a UDS or XCP device would want, and a list of plugins reading "Motor
control" tells nobody which. Where the profile number is how
people refer to the thing -- CiA 402 is -- it earns its place in brackets.

A plugin folder is a package rooted at itself, so a second file beside
`plugin.py` is reached with `from . import helper`. Name it absolutely and
you reach some other copy of it -- for an installed plugin, the copy
pycangui ships rather than the one you are editing.

### Packages it needs

A package pycangui does not install can come with the plugin, or be named:

- **In a `lib` folder beside `plugin.py`**, as `pip install --target lib <package>` leaves it.
  It is on the import path while the plugin is loaded, so a pure-Python package needs no
  pip at all, the Windows build included. Two plugins carrying different versions of one
  package cannot both be loaded -- one process holds one copy -- so the second is refused,
  naming both. One pycangui already has is not used from `lib`, and that is said.
- **In `REQUIRES`**, in pip's form: `REQUIRES = ["pyserial>=3.5"]`. A plugin whose
  `REQUIRES` is not met is listed, saying what it needs, rather than failing to import.
  Installing it, or *Install packages...* in *Plugins > Manage plugins...*, offers to fetch
  them with pip. The Windows build has no pip, so there it says what is missing; a package
  with compiled parts needs pycangui installed with pip.

A pycangui from before plugin API 3 ignores both.

### A program of its own

Work that does not belong in pycangui's process -- a fieldbus master written in Rust or
C, a server for another tool -- can be a program in the plugin's folder that
`app.helper("my_helper")` runs and looks after:

- It starts in the plugin's folder. On Windows `.exe` may be left off the name; a `.py`
  file runs with pycangui's Python, which the Windows build does not have.
- The first line it prints is the port it listens on, handed to `on_ready(port)`, so no
  port has to be agreed in advance. One that prints none within `timeout` seconds is
  stopped. With `port=False` it serves nothing, and `on_ready(None)` comes once it starts.
- Everything else it prints goes to the Event Log, what it writes to stderr as warnings.
- It is stopped when the plugin is unloaded or reloaded and when pycangui closes: its
  stdin is closed, so a helper should stop when reading stdin ends, and one still running
  a second later is killed.
- If it stops on its own, the Event Log says so and you are offered to start it again,
  unless the plugin gave `on_exit(code)` to deal with that itself.

`app` is the whole API, and it offers:

| | |
|---|---|
| `add_pane(name, title, build, area, several)` | a dock of its own, hidden until the View menu, or *Plugins > your plugin*, opens it; `area` is `left`, `right`, `top` or `bottom` |
| `open_pane(name)` | show one of your panes, for a plugin with a reason to |
| `show_panes()` | bring all of your panes out, as installing one does |
| `on_pane_shown(fn)` | `fn(name, on)` when one of *your* panes appears or is put away |
| `add_pane(..., shutdown=fn)` | `fn(pane)` when that pane goes for good, or your plugin is unloaded |
| `on_closing(fn)` | `fn()` as the window goes, while the buses are still open |
| `add_menu_action(text, callback, tooltip)` | an entry under *Plugins > your plugin* |
| `add_toolbar_button(text, callback, tooltip, checkable)` | a button on the toolbar, which stays pressed if `checkable` |
| `add_trace_labeller(fn)` | name frames in every trace: `fn(frame) -> str \| None` |
| `add_field_widget(kind, class)` | an eighth way for a [custom pane](custom-panes.md) to show an object |
| `run_in_background(job, done)` | work off the GUI thread, so the window does not freeze |
| `paused`, `on_pause(fn)` | whether pycangui's own traffic is [paused](pause.md), and `fn(on)` when that changes; hold back your own polling too (API 2) |
| `on_stop_all(fn)` | `fn()` when everything that repeats is stopped, for good (API 2) |
| `hold_traffic(why)` / `release_traffic(why)` | pause pycangui's traffic while you do `why`, a download say; given back if you are unloaded (API 2) |
| `with_icon(button, name)` | one of pycangui's icons on a button or menu item, by name: `"start"`, `"stop"`, `"read"` (API 3) |
| `mono_font()` | pycangui's fixed-pitch font, at the size its other text is (API 3) |
| `emcy_text(code)` | what CiA 301 calls an emergency error code (API 3) |
| `poller(parent)` | reads the same objects over and over, no faster than they answer, as custom panes do; `MIN_HZ`, `MAX_HZ`, `DEFAULT_HZ` and `rate_text()` for its rate box (API 3) |
| `devices()`, `device(key)` | the live devices, and one by its key -- a CANopen node's is its node ID: `label`, `read(index, sub)` and `write(index, sub, data)` from inside `background(job, done)`, `has(index, sub)`, `states`, `state()` and `set_state(name)`, and `receives()` for what process data also writes; `changed` and `state_changed` say when to look again (API 3) |
| `on_devices_changed(fn)` | `fn()` when a device appears or goes, not on every heartbeat (API 3) |
| `settings` | your own settings, kept in the workspace: `get(key, default)`, `set(key, value)`, `keys()`, `remove(key)` (API 3) |
| `add_console_name(name, obj)` | `obj` in the Python Console and in scripts as `name`; taken out when your plugin is unloaded, and refused if pycangui or another plugin has the name (API 3) |
| `add_hooks(module, file)` | your own [hooks](hooks.md), as `hooks/<module>.py` in the workspace beside pycangui's: every function in `file` (in your plugin's folder) whose name does not start with `_` is one, taking `ctx` by keyword; call one with `hooks.call(module, name, ...)`. The copy is the user's to edit, kept up to date like pycangui's own, and left when your plugin is uninstalled (API 3) |
| `helper(program, *args, port, on_ready, on_exit, timeout)` | run a program from your plugin's folder beside pycangui, as [above](#a-program-of-its-own); returns it, with `port`, `running`, `stop()` and `start()` (API 3) |
| `log` / `warn` / `error` | say something in the Event Log, prefixed with your name |
| `window` `ctx` `hooks` `panes` `channels` `bus` `signals` `canopen` `uds` `j1939` `xcp` `dbc` | the live objects |
| `confirm` | the confirmations pycangui asks before disturbing equipment, for a plugin that does too |
| `api_version` | the API version this pycangui provides |

Two things it does for you. **A plugin that fails is designed to take only itself down** --
the traceback goes to the Event Log where somebody will see it, rather than to
a console that does not exist, and the rest still load. If it fails part way
through `register`, whatever it had already added is taken back, so the window
is not left with a menu entry that raises whenever it is used.

**Reload means reload.** *Plugins > Reload plugins* takes away everything a
plugin added last time before loading it again, so editing one and pressing
reload is how it gets written -- there is no need to restart, and no second
copy of its pane appears beside the first.

## The plugins that ship with it

Supplied rather than installed: *Plugins > Supplied with pycangui* and
*Plugins > Manage plugins...* are where they are, and until one is installed
none of it is meant to run. Each has a page of its own:

- [CANopen motor control (CiA 402)](cia402.md) --- driving a motor controller

CANopen firmware download used to be a plugin too, and is now the Firmware tab
of the CANopen pane, with a device's own sequence in hooks: see
[CANopen firmware](firmware.md).

pycangui's own plugins are packaged, installed and loaded exactly the way one
of yours is -- installing a supplied plugin packs it and unpacks it through the
same code a downloaded one goes through, so the path a stranger's plugin takes
is the path we take every time.
