# pycangui

A user-friendly CAN bus tool: live trace, transmit, CANopen, UDS, J1939, XCP
and Python scripting, on any adapter supported by python-can.  Apache-2.0.

## Development

```
python -m venv .venv
.venv\Scripts\pip install -e .[dev,j1939,uds,xcp]
.venv\Scripts\python -m pycangui
```

Without hardware: pick interface `virtual`, channel `vcan0`, press Connect,
then enable Tools > Demo traffic.
