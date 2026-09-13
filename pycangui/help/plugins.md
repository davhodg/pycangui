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

Before it unpacks anything it says what is in the package and reminds you what
a plugin is: **Python that runs as part of pycangui, with everything pycangui
can reach.** Install ones you would be willing to run yourself. You can open
a package in any file manager and read it first; that is most of the reason it
is a plain zip.

It belongs to the [workspace](workspaces.md), the same as the hooks, because a screen for a
product is knowledge about that product. *Manage plugins...* is the rest of
it:

* **the tick beside each one** switches it on and off. Off means *not loaded
  at all* -- no pane, no menu entries, and none of its code runs -- while the
  folder stays exactly as you left it, edits and all.
* **Export...** writes an installed plugin back out as a package, which is how
  one of yours gets to somebody else.
* **Remove...** deletes it from the workspace, and says so first: whatever you
  edited into it goes too.
* **Supplied with pycangui** lists the ones that ship with it and are not
  installed here. *Nothing pycangui ships is loaded until you install it*, so
  a window you have not asked anything of has no plugin panes in it at all.

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
supplied here are *CANopen* firmware and *CANopen* motor control, because
neither sequence is the one a UDS or XCP device would want and a list of
plugins reading "Firmware" tells nobody which. Where the profile number is how
people refer to the thing -- CiA 402 is -- it earns its place in brackets.

A plugin folder is a package rooted at itself, so a second file beside
`plugin.py` is reached with `from . import helper`. Name it absolutely and
you reach some other copy of it -- for an installed plugin, the copy
pycangui ships rather than the one you are editing.

`app` is the whole API, and it offers:

| | |
|---|---|
| `add_pane(name, title, build, area, several)` | a dock of its own, hidden until the View menu opens it |
| `on_pane_shown(fn)` | `fn(name, on)` when one of *your* panes appears or is put away |
| `add_pane(..., shutdown=fn)` | `fn(pane)` when that pane goes for good, or your plugin is unloaded |
| `on_closing(fn)` | `fn()` as the window goes, while the buses are still open |
| `add_menu_action(text, callback, tooltip)` | an entry under *Tools > Plugins > your plugin* |
| `add_toolbar_button(text, callback, tooltip)` | a button on the toolbar |
| `add_trace_labeller(fn)` | name frames in every trace: `fn(frame) -> str \| None` |
| `add_field_widget(kind, class)` | an eighth way for a [custom pane](custom-panes.md) to show an object |
| `run_in_background(job, done)` | work off the GUI thread, so the window does not freeze |
| `log` / `warn` / `error` | say something in the Event Log, prefixed with your name |
| `ctx` `hooks` `panes` `channels` `bus` `signals` `canopen` `uds` `j1939` `xcp` `dbc` | the live objects |

Two things it does for you. **A plugin that fails takes only itself down** --
the traceback goes to the Event Log where somebody will see it, rather than to
a console that does not exist, and the rest still load. If it fails part way
through `register`, whatever it had already added is taken back, so the window
is not left with a menu entry that raises whenever it is used.

**Reload means reload.** *Plugins > Reload plugins* takes away everything a
plugin added last time before loading it again, so editing one and pressing
reload is how it gets written -- there is no need to restart, and no second
copy of its pane appears beside the first.

## The plugins that ship with it

Supplied rather than installed: *Plugins > Manage plugins...* is where they
are, and until one is installed none of it runs.

**CANopen firmware** downloads a program to a CANopen node by CiA 302-3: stop the
program (0x1F51), clear it, write the image as a domain (0x1F50), start it
again. Intel HEX, S-record and raw binary are all read; the image has to be
one contiguous block, because a program download *is* one block of bytes and
filling the gaps would put invented bytes into somebody's flash.

**Most devices do not do it that way.** Firmware download over CANopen is
usually a sequence of the maker's own writes to objects of their own choosing,
and no amount of standards reading will produce it. That is exactly why it is
a plugin: install it, then edit the `program.py` in your workspace to be what
the device actually wants. The copy you edit is the one that runs, and the
pane, the progress bar and the reporting go on working around it.

While a device is being programmed it answers very little and slowly, so
timeouts are the expected thing rather than a fault -- and pulling the power
part way through is how a controller is turned into a brick.

**[CANopen DCF compare](compare.md)** puts two configurations side by side --
two files, or a file against a live device, or a device against the EDS it was
built from -- and says what is different. Comparing against a device reads only
the objects the other side names, so it takes seconds and needs no EDS on the
device.

**CANopen motor control (CiA 402)** drives a motor controller: its state, its mode,
its target and what it is actually doing.

Half of that screen could have been a custom pane, and it is worth knowing
which half. The modes, the targets and the actual values are ordinary objects
at standard indices -- point a custom pane at 0x6060, 0x60FF and 0x606C and you
have them, with no code at all.

The other half cannot be. A drive does nothing until it has been walked
through a state machine -- 0x06, then 0x07, then 0x0F -- and *which* of those
writes is needed depends on what the drive answered to the last one. A fault
is cleared by a rising edge rather than by a value, so it is two writes. And
the state is not a value: it is decoded from overlapping masks of the
statusword, where "Ready to switch on" and "Switched on" differ in one bit
while "Fault" is a different mask altogether. No arrangement of boxes on a
form expresses any of that.

**Enable asks first**, once per drive per session, the same as joining a live
bus or transmitting onto one: it is the moment a motor becomes able to move,
and if a target is already set it may move immediately.

What it refuses is as much of the point as what it does. A drive still
starting up, or still reacting to a fault, is left alone -- it leaves those
states by itself, and a controlword written then is ignored rather than
refused, which looks exactly like the tool having done nothing. Quick stop is
offered only to a drive that is running. A target is not offered at all in the
cyclic synchronous modes, where it has to arrive every cycle over a PDO and one
written by hand would be stale before it got there. A value too big for its
object is refused rather than wrapped. And the button that tells a profile
position drive to take its target is offered only while the drive is already
enabled, because making that happen means writing the enable controlword -- a
button that quietly does what another button asks permission for is a hole in
the permission.

**Closing the pane stops the drive.** A demand sent over SDO does not stop
when the window showing it does: the drive holds the last controlword and the
last target it was given and goes on acting on them. So putting the pane away,
closing it for good, switching the plugin off or closing pycangui all halt the
drive first -- and while it is running, a bar across the top of the pane says
so. If it was running and the bus goes instead, nothing can be written, and
the Event Log says that plainly rather than saying nothing.

Stopping is a **halt** (controlword bit 8), not a zero target, and the
difference matters: halt means "come to a standstill" in every mode, whereas
zero is a *place*. Writing zero to the target position of a drive part way
through a move would not stop it -- it would send it to position zero, which
may be the longest move it has been asked for all day. A zero is written to
the target as well, but only in the speed and torque modes, where the target is
a rate and zero really is a stop.

Removing power on top of that is offered as a tick box and is off by default.
It is not obviously the safer of the two: on a vertical axis it is the load
that decides, and whether a brake catches it is a fact about the machine rather
than about the tool.

The numbers are counts, counts per second and per mille of rated torque, which
is what the profile defines. Turning those into millimetres or amps needs the
gearing and the motor rating, which are the drive's business and not
pycangui's.

pycangui's own plugins are packaged, installed and loaded exactly the way one
of yours is -- installing a supplied plugin packs it and unpacks it through the
same code a downloaded one goes through, so the path a stranger's plugin takes
is the path we take every time.
