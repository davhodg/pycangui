"""A J1939 engine, broadcasting at the frame level.

The counterpart to ``canopen_device.py``, and deliberately the opposite
approach: that one takes a whole protocol server from ``node.canopen()``,
this one builds its frames by hand.  Most protocols are this one -- pycangui
has no server to hand you for J1939 or for a maker's own scheme, so what a
node does is compose an id, pack some bytes and send them.

This engine broadcasts EEC1 (engine speed) and CCVS1 (wheel speed) at their
usual rates, with the speed wandering so a plot has something to show.  Load
a J1939 DBC and the trace will name them.

To make it yours: change ``SOURCE_ADDRESS``, and change ``poll`` to broadcast
the PGNs your ECU broadcasts.
"""

from __future__ import annotations

import math
import struct

NAME = "J1939 engine"
DESCRIPTION = "Broadcasts engine and wheel speed, composed frame by frame."
RATE_HZ = 20  # 50 ms, which is the fastest of the PGNs below

#: Who this ECU claims to be.  0x00 is the engine's conventional address.
#: A real ECU claims it first and defends it; this one simply uses it, which
#: is enough to be talked to and not enough to be a good citizen.
SOURCE_ADDRESS = 0x00

#: Parameter group numbers, from J1939-71.  A broadcast id is the priority,
#: the PGN and the sender, packed into 29 bits.
EEC1 = 0xF004  # electronic engine controller 1: engine speed
CCVS1 = 0xFEF1  # cruise control / vehicle speed: wheel-based speed
PRIORITY = 3

#: Every N polls.  EEC1 is a 50 ms message and CCVS1 a 100 ms one, and a node
#: sending both at the faster rate is a node that fills somebody's trace.
EVERY_CCVS1 = 2


def can_id(pgn: int, source: int, priority: int = PRIORITY) -> int:
    """A 29-bit J1939 identifier for a broadcast PGN."""
    return (priority << 26) | (pgn << 8) | source


def start(node, *, ctx):
    node.state.rpm = 800.0
    node.state.kph = 0.0
    node.state.polls = 0


def poll(node, *, ctx):
    """Wander the engine speed, and broadcast what a real one would.

    A value that only sits still tells you nothing about whether the tool is
    decoding it: this one breathes between idle and about 2000 rpm so a plot
    and a signal list both have something to be right or wrong about.
    """
    node.state.polls += 1
    phase = node.state.polls / RATE_HZ
    node.state.rpm = 1400.0 + 600.0 * math.sin(phase / 3.0)
    node.state.kph = max(0.0, (node.state.rpm - 800.0) / 30.0)

    node.send(can_id(EEC1, SOURCE_ADDRESS), _eec1(node.state.rpm), extended=True)
    if node.state.polls % EVERY_CCVS1 == 0:
        node.send(can_id(CCVS1, SOURCE_ADDRESS), _ccvs1(node.state.kph), extended=True)


def _eec1(rpm: float) -> bytes:
    """EEC1, with engine speed in bytes 4-5 at 0.125 rpm per bit.

    Everything this ECU does not model is 0xFF, which in J1939 means "not
    available" rather than zero -- a tool showing 0% torque is being told
    something quite different from a tool showing nothing.
    """
    raw = min(0xFAFF, int(rpm / 0.125))
    return b"\xff\xff\xff" + struct.pack("<H", raw) + b"\xff\xff\xff"


def _ccvs1(kph: float) -> bytes:
    """CCVS1, with wheel-based speed in bytes 2-3 at 1/256 km/h per bit."""
    raw = min(0xFAFF, int(kph * 256))
    return b"\xff" + struct.pack("<H", raw) + b"\xff\xff\xff\xff\xff"
