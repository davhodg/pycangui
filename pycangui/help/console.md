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

*Run script...* executes a `.py` file in that namespace.

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
