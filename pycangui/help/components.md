[&larr; Contents](manual.md)

# Replaceable components

A **component** is the small part of pycangui that actually talks on the wire.
Everything above it -- the pane, the threading, the settings, the plotting --
stays put, and the component underneath can be swapped: a C or Rust library
through `ctypes`, a maker's DLL, or a different Python package, without
touching pycangui.

| Kind | Chosen in | Built in |
|------|-----------|----------|
| CAN interface | the Connect bar | python-can's own adapters |
| `isotp` -- ISO-TP transport | UDS pane, *Transport* | `can-isotp` |
| `xcp` -- XCP or CCP engine | XCP pane, *Engine* | `xcp-builtin`, `ccp-builtin` |

## Your components folder

**The built-in components are part of pycangui and are not kept in a folder.**
The components folder holds only yours, so it is empty until you put something
in it -- that is the normal state, not something missing. While it has nothing
of yours in it, it carries a README saying so.

A Python file there does one of two things:

- **adds** a component, which then appears beside the built-in ones; or
- **replaces** a built-in one, by registering the same kind and name.

*Tools > Open custom components folder* opens it. It is
`%APPDATA%\pycangui\components\` on Windows; [Your files](files.md) says where
it is on Linux and macOS. Files are read when pycangui starts, and one whose
name begins with an underscore is ignored.

*Tools > List components* prints to the Event Log everything that is
registered -- built in or yours, which file each of yours came from, what it
replaces, and which one is in use -- followed by what is in the folder,
including a file that loaded but registered nothing, and one that failed to
load with the reason.

## An ISO-TP transport or an XCP engine

```python
from pycangui.core.components import register_component
from pycangui.uds.transport import IsoTpTransport


@register_component("isotp", "my-c-lib", "ISO 15765-2 from my C library")
class MyIsoTp(IsoTpTransport):
    def open(self): ...
    def close(self): ...
    def send(self, payload): ...
    def recv(self, timeout): ...
```

It then appears in the pane's selector, and the choice is remembered. The
interface to implement is `pycangui.uds.transport.IsoTpTransport` (open, send,
recv) for a transport, and `pycangui.xcp.engine.XcpEngine` (connect,
seed/unlock, read, write) for an engine.

## A CAN interface

An adapter python-can does not support is added as a python-can bus class --
a subclass of `can.BusABC` with `send()`, `_recv_internal()` and `shutdown()`,
and `_detect_available_configs()` if it can list its channels:

```python
import can

from pycangui.core.components import register_interface


@register_interface("myusb", "My USB adapter, through the maker's DLL")
class MyUsbBus(can.BusABC):
    def __init__(self, channel, bitrate=500_000, **kwargs): ...
    def send(self, msg, timeout=None): ...
    def _recv_internal(self, timeout): ...
    def shutdown(self): ...
```

It appears in the Connect bar's interface list, and everything in pycangui
works on top of it unchanged -- trace, recording, CANopen, UDS, XCP, J1939 --
because they only ever see a python-can bus. CAN FD and channel detection are
read from the class itself, so an `fd` argument on `__init__` is enough to
offer FD.

It is registered in python-can's own table as well as pycangui's, because
python-can is what opens every bus and looks the name up there. A name
python-can already has is replaced, which is how a patched driver for an
adapter it does support would be used; *List components* says so.

## When one fails

A file that fails to import is reported in the Event Log and skipped, and the
built-in components keep working. A component chosen in a pane but not
registered this run -- one of yours that failed to load -- falls back to the
first one that is, and the log says which. CANopen and J1939 still call their
libraries directly.
