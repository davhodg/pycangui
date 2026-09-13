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
