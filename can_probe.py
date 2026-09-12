# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Read an IXXAT (or any) channel with python-can alone, no pycangui involved.

Isolates the adapter and the bus from the application: if this is silent too,
the problem is outside pycangui.

    .venv\\Scripts\\python.exe can_probe.py                 # list adapters
    .venv\\Scripts\\python.exe can_probe.py 0               # read channel 0
    .venv\\Scripts\\python.exe can_probe.py 1 250000        # channel 1 at 250k
"""

import sys
import time

import can

interface = "ixxat"
configs = can.detect_available_configs(interface)
print(f"{interface} reports {len(configs)} channel(s):")
for config in configs:
    print("   ", config)

if len(sys.argv) < 2:
    print("\nPass a channel number to read it, e.g.  can_probe.py 0")
    raise SystemExit(0)

channel = int(sys.argv[1])
bitrate = int(sys.argv[2]) if len(sys.argv) > 2 else 500000

# Use the detected configuration for that channel, so a second dongle is
# addressed properly rather than leaving the driver to choose.
extra = next(
    (
        {k: v for k, v in c.items() if k not in ("interface", "channel")}
        for c in configs
        if c.get("channel") == channel
    ),
    {},
)
print(f"\nOpening {interface} channel {channel} at {bitrate} bit/s {extra or ''}")
bus = can.Bus(interface=interface, channel=channel, bitrate=bitrate, **extra)
print(f"state: {bus.state}")
print("Reading for 10 s...  (Ctrl+C to stop)\n")

count = errors = 0
deadline = time.time() + 10
try:
    while time.time() < deadline:
        msg = bus.recv(0.5)
        if msg is None:
            continue
        if msg.is_error_frame:
            errors += 1
            continue
        count += 1
        if count <= 20:
            print(f"  {msg.arbitration_id:08X}  {bytes(msg.data).hex(' ')}")
finally:
    print(f"\n{count} message(s), {errors} error frame(s), final state {bus.state}")
    bus.shutdown()
