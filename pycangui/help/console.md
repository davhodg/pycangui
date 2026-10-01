[&larr; Contents](manual.md)

# Python Console

The **Python Console** pane is a live console with the same objects the GUI uses:

| Name | What it is |
|---|---|
| `bus` | the channel selected in the toolbar |
| `channels` | every channel |
| `canopen`, `uds`, `j1939`, `xcp` | the protocol managers behind their panes |
| `nodes` | the [simulated nodes](virtual.md) |
| `recorder` | the recorder behind *Record* |
| `ctx`, `hooks` | what [hooks](hooks.md) are handed, and the hooks themselves |
| `window` | the main window |
| `send(id, data, ext=False, fd=False)` | put one frame on the selected channel |
| `wait(seconds)` | let the window and the buses run for that long -- a script's `sleep` |

*Run script...* executes a `.py` file in that namespace.

`wait` rather than `time.sleep`, because the console runs on the window's
thread: `time.sleep` stops the window, and the frames, answers and timers a
script is waiting for stop arriving with it.

## A script from the command line

```
pycangui --run check.py
pycangui --workspace rig --run check.py --skip-start-warning
```

`--run` starts pycangui, waits for the window and the workspace's
[startup hook](hooks.md), runs the script with the names above, and closes.
The exit code is the script's -- 0 when it runs to the end, *n* for
`sys.exit(n)`, 1 for an exception or `sys.exit("why")` -- which is what a
production line or a CI job reads. What it prints goes to the terminal when
there is one, and to the [Event Log](event-log.md) always, with the exit code.

A script is still asked every question pycangui asks: joining a real bus,
transmitting, writing. Each has *Do not ask me this again*, so a run nobody is there to
answer needs them answered once beforehand. The notice about real equipment
cannot be ticked away, so `--skip-start-warning` skips it -- only with `--run`,
and the Event Log says it was skipped. Closing still asks about changes not
saved; the exit code then waits until pycangui is closed.

On Windows, `python -m pycangui` waits for pycangui and prints; the installed
`pycangui.exe` is a window program, so `start /wait pycangui.exe --run check.py`
waits for it and sets `%ERRORLEVEL%`.

## Anything that blocks

Console code runs on the window's own thread. That is what lets it touch
`window` and the panes without locking, and it means a call that waits --
an SDO read, a loop of five hundred of them -- holds the window still until
it finishes.

For those, hand the work to the protocol's own worker:

```python
canopen.background(lambda: canopen.node(5).sdo.upload(0x1018, 4), print)
uds.background(lambda: uds.client.read_data_by_identifier(0xF190))
```

`done(result, error)` is called back on the window's thread; leave it out and
the answer goes to the Event Log, or pass `print` to see it in the console.

**Why the protocol's worker rather than a thread of its own.** A second
thread talking to one CANopen network is two conversations sharing one
listener, and the listener is what matches each reply to whoever asked. One
worker per protocol keeps requests sequential, which is what the protocol
wants; joining that queue also means your job waits its turn behind whatever
a pane has already asked for, instead of talking over it.

**Why not run everything there.** This namespace holds `window`, and a widget
touched from another thread takes the process with it. Backgrounding by hand,
per call, is the only version of this that is safe to offer.

`j1939` and `xcp` have no equivalent: J1939 has nothing that blocks to speak
of, and the XCP manager already queues everything it does onto a worker of
its own.
