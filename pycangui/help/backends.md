[&larr; Contents](manual.md)

# Replaceable protocol back ends

Each protocol is split into a *manager* (Qt signals, threading, A2L / EDS /
DBC handling, plotting -- the part that never changes) and a small *engine*
that actually talks the protocol. Engines are picked at run time, so you can
drop in your own implementation -- a C or Rust library through `ctypes`, or a
different Python package -- without touching pycangui:

| Kind | Interface | Built in |
|------|-----------|----------|
| `xcp` | `pycangui.xcp.engine.XcpEngine` (connect, seed/unlock, read, write) | `native` -- XCP on CAN in pycangui |
| `isotp` | `pycangui.uds.transport.IsoTpTransport` (open, send, recv) -- everything UDS needs from the link | `can-isotp` |

Put a module in the `backends` folder -- `%APPDATA%\pycangui\backends\` on
Windows; [Your files](files.md) says where it is on Linux and macOS, and
*Tools > Open backends folder* opens it:

```python
from pycangui.core.backends import register_backend
from pycangui.uds.transport import IsoTpTransport


@register_backend("isotp", "my-c-lib", "ISO 15765-2 from my C library")
class MyIsoTp(IsoTpTransport):
    def open(self): ...
    def close(self): ...
    def send(self, payload): ...
    def recv(self, timeout): ...
```

It then appears in the pane's engine/transport selector and the choice is
remembered. A backend module that fails to import is reported in the Event Log and
skipped; the built-in keeps working. CANopen and J1939 still call their
libraries directly and will get the same treatment.
