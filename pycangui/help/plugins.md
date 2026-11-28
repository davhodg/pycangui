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
* **Check for plugins** reads the plugin lists online -- see below -- and fills
  *Available from the plugin lists* with what you have not got; **Install**
  downloads one and installs it. It is the only time *Manage plugins* goes
  online, and nothing is installed until you choose.
* **Update...** installs the newer version a list offers over the one here,
  and is available only after a check has found one: the row says which
  version. It asks first, because it is a replacement rather than a merge. If
  the copy here has been changed since it was installed, it is moved aside
  and kept as `_name.bak` rather than lost -- the same bargain as a hook
  file's `.bak`, and the underscore is what keeps the copy from being loaded
  as a second plugin. An earlier `.bak` is never written over.
* **Export...** writes an installed plugin back out as a package, which is how
  one of yours gets to somebody else.
* **Remove...** deletes it from the workspace, and says so first: whatever you
  edited into it goes too.

An installed plugin is a copy in your workspace, and that copy is the one that
runs -- so editing it is editing yours, and *Reload plugins* picks the edit up
without a restart.

### The plugin lists

pycangui ships no plugins of its own: each lives wherever its author keeps it.
What pycangui has is a list of where to find them, `plugins/index.json` in its
repository, which *Check for plugins* reads -- and *Sites...* adds lists of your
own beside it, a URL or a file on this computer each. Nothing about the host is
assumed: a list is a JSON file at an `https` URL, or a local file, and the zips
it names can be anywhere.

```json
{
  "format": 1,
  "plugins": [
    {
      "name": "thermistor",
      "title": "Thermistor setup",
      "description": "Calibrating the thermistor inputs.",
      "version": "1.2",
      "api_version": 3,
      "url": "thermistor-1.2.zip",
      "sha256": "64 hex digits",
      "page": "https://example.org/thermistor"
    }
  ],
  "include": ["https://example.org/other-plugins.json"]
}
```

- `name` is the plugin's folder name, and the zip must hold that plugin.
- `url`, `page` and `include` can be relative to the list, so a list can sit
  beside its zips.
- `sha256` is the zip's: a download that does not match is refused, so a list
  and a zip cannot disagree unnoticed.
- `include` reads other lists as well. pycangui's own list includes each
  plugin repository's list, and one repository with several plugins lists
  several.
- A plugin whose `api_version` is newer than this pycangui's is listed, saying
  it needs a newer pycangui, and not installed.
- Where two lists offer the same name, the first read is used, pycangui's own
  first, and the Event Log says so.

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

Say in the name what the plugin talks to, where that is not obvious: the
CiA 402 plugin is *CANopen* motor control, because its sequence is not the
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
- The first line it reads on its stdin is a token, new each time it starts, which your
  plugin has as `helper.token`. Any program on the computer can connect to a port on
  127.0.0.1, so a helper should take only a connection that says the token first, and drop
  any other. With `port=False` there is no token.
- Everything else it prints goes to the Event Log, what it writes to stderr as warnings.
- It is stopped when the plugin is unloaded or reloaded and when pycangui closes: its
  stdin is closed, so a helper should stop when reading stdin ends, and one still running
  a second later is killed.
- If it stops on its own, the Event Log says so and you are offered to start it again,
  unless the plugin gave `on_exit(code)` to deal with that itself.

`app` is the whole API, and it offers:

| | |
|---|---|
| `add_pane(name, title, build, area, several)` | a dock of its own, hidden until the View menu, or *Plugins > your plugin*, opens it; `area` is `left`, `right`, `top` or `bottom`. Its title bar adds your `VERSION`: *title v1.2* |
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
| `signal(group, name, value, unit)` | a value for [Signals and Plot](signals.md), timed now on the clock frames are: plotted, exported and shared like any other signal, under `group` (API 3) |
| `poller(parent)` | reads the same objects over and over, no faster than they answer, as custom panes do; `MIN_HZ`, `MAX_HZ`, `DEFAULT_HZ` and `rate_text()` for its rate box (API 3) |
| `devices()`, `device(key)` | the live devices, and one by its key -- a CANopen node's is its node ID: `label`, `read(index, sub)` and `write(index, sub, data)` from inside `background(job, done)`, `has(index, sub)`, `states`, `state()` and `set_state(name)`, `receives()` for what process data also writes, and `cycles(index, sub)` for whether a write is sent again every cycle as process data, so a value written once is held; `changed` and `state_changed` say when to look again, and `leaving` that it is about to go on purpose, while it can still be written to -- whatever is driving it stops then (API 3) |
| `on_devices_changed(fn)` | `fn()` when a device appears or goes, not on every heartbeat (API 3) |
| `add_device_provider(provider)` | devices of your own, by a protocol pycangui does not speak, in every device list after the CANopen nodes: `provider` is a `QObject` with a `changed` signal, `devices()` and `device(key)`, and each device has everything listed for `devices()` above. Keys must not be ints, which are node IDs: use a tuple that starts with your plugin's name, `("myplugin", 1)`. Taken out when your plugin is unloaded, each device you can still reach saying `leaving` first; your helper keeps running until every plugin's `on_closing` has run (API 3) |
| `settings` | your own settings, kept in the workspace: `get(key, default)`, `set(key, value)`, `keys()`, `remove(key)` (API 3) |
| `add_console_name(name, obj)` | `obj` in the Python Console and in scripts as `name`; taken out when your plugin is unloaded, and refused if pycangui or another plugin has the name (API 3) |
| `add_hooks(module, file)` | your own [hooks](hooks.md), as `hooks/<module>.py` in the workspace beside pycangui's: every function in `file` (in your plugin's folder) whose name does not start with `_` is one, taking `ctx` by keyword; call one with `hooks.call(module, name, ...)`. The copy is the user's to edit, kept up to date like pycangui's own, and left when your plugin is uninstalled (API 3) |
| `helper(program, *args, port, on_ready, on_exit, timeout)` | run a program from your plugin's folder beside pycangui, as [above](#a-program-of-its-own); returns it, with `port`, `token`, `running`, `stop()` and `start()` (API 3) |
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

## Plugins there are

*Check for plugins* lists them all; these are the ones pycangui's list names:

- [Motor control (CiA 402)](https://github.com/davhodg/pycangui-cia402) --- driving
  a motor controller, a CANopen node or a drive another plugin provides. It came with
  pycangui until 0.6, as CANopen motor control; a workspace that has it keeps
  it, and *Check for plugins* offers the newer version.

CANopen firmware download used to be a plugin too, and is now the Firmware tab
of the CANopen pane, with a device's own sequence in hooks: see
[CANopen firmware](firmware.md).
