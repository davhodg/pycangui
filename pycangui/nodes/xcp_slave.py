"""An XCP slave: a block of memory the XCP pane can read and calibrate.

The example of a node that does *both* halves -- it answers commands in
``on_frame`` like the UDS server, and moves its measurements in ``poll`` like
the CANopen device -- which is what a real calibration slave does.

XCP is a memory protocol: the master says "give me four bytes from this
address", and the A2L says which address a named measurement lives at.  So
this node is a ``bytearray`` and a command handler, and the addresses below
are the ones in ``resources/demo.a2l``.

Calibration is locked until a seed and key exchange, which is the usual
arrangement: reading a slave is one thing and writing new constants into a
running controller is quite another.

XCP on CAN has no standard identifiers -- every project picks a pair -- so
these are pycangui's own.

To make it yours: point pycangui at your A2L, change the ids, and lay your
measurements out where your A2L says they are.
"""

from __future__ import annotations

import math
import struct

NAME = "XCP slave"
DESCRIPTION = "A calibratable memory: connect, upload, unlock, download."
RATE_HZ = 20

COMMAND_ID = 0x7A0
RESPONSE_ID = 0x7A1

#: Command codes, from the ASAM XCP standard.
CONNECT = 0xFF
DISCONNECT = 0xFE
GET_SEED = 0xF8
UNLOCK = 0xF7
SET_MTA = 0xF6
UPLOAD = 0xF5
SHORT_UPLOAD = 0xF4
DOWNLOAD = 0xF0

POSITIVE = 0xFF
ERROR = 0xFE
ERR_CMD_UNKNOWN = 0x20
ERR_OUT_OF_RANGE = 0x22
ERR_ACCESS_LOCKED = 0x25
ERR_ACCESS_DENIED = 0x35

#: Which resources are protected.  Bit 0 is calibration, so a master has to
#: unlock before it may write anything.
RESOURCE_CAL = 0x01

MEMORY_BYTES = 0x3000
ENGINE_SPEED = 0x1000  # a measurement: poll() moves it
BATTERY_MV = 0x1002
COOLANT_C = 0x1004
SPEED_LIMIT = 0x2000  # characteristics: the master writes these
IDLE_TARGET = 0x2002

#: The seed handed out, and the key is every byte inverted.  A real slave
#: keeps a secret and an algorithm; this shows that the exchange happens.
SEED = bytes([0xA5, 0x5A, 0x12, 0x34])


def start(node, *, ctx):
    memory = bytearray(MEMORY_BYTES)
    struct.pack_into("<H", memory, BATTERY_MV, 1320)  # 13.20 V
    struct.pack_into("<h", memory, COOLANT_C, 90)
    struct.pack_into("<H", memory, SPEED_LIMIT, 6500)
    struct.pack_into("<H", memory, IDLE_TARGET, 850)
    node.state.memory = memory
    node.state.connected = False
    node.state.unlocked = False
    node.state.mta = 0
    node.state.polls = 0


def poll(node, *, ctx):
    """Move the measurements, so the XCP pane has something that changes.

    A slave whose values never move cannot tell you whether the tool is
    reading the right address or simply reading zero.
    """
    node.state.polls += 1
    rpm = 800 + 3000 * (1 + math.sin(node.state.polls / 40)) / 2
    struct.pack_into("<H", node.state.memory, ENGINE_SPEED, int(rpm))


def on_frame(node, frame, *, ctx):
    if frame.arbitration_id != COMMAND_ID or frame.is_extended_id:
        return
    if (answer := _handle(node, bytes(frame.data))) is not None:
        node.send(RESPONSE_ID, answer)


def _handle(node, data: bytes) -> bytes | None:
    if not data:
        return None
    command = data[0]

    if command == CONNECT:
        node.state.connected = True
        # What the master reads its whole world out of: which resources are
        # protected, the byte order and granularity, then the biggest
        # command and data packets it may use, then the protocol version.
        return bytes([POSITIVE, RESOURCE_CAL, 0x00, 8]) + struct.pack("<H", 8) + bytes([0x01, 0x01])

    if not node.state.connected:
        # Everything else is meaningless before CONNECT, and answering
        # anyway would let a broken master look like a working one.
        return bytes([ERROR, ERR_OUT_OF_RANGE])

    if command == DISCONNECT:
        node.state.connected = False
        return bytes([POSITIVE])

    if command == GET_SEED:
        return bytes([POSITIVE, len(SEED), *SEED])

    if command == UNLOCK:
        key = data[2 : 2 + data[1]]
        if key != bytes(b ^ 0xFF for b in SEED):
            return bytes([ERROR, ERR_ACCESS_DENIED])
        node.state.unlocked = True
        return bytes([POSITIVE, 0x00])  # nothing left protected

    if command == SET_MTA:
        node.state.mta = struct.unpack("<I", data[4:8])[0]
        return bytes([POSITIVE])

    if command == SHORT_UPLOAD:
        count = data[1]
        address = struct.unpack("<I", data[4:8])[0]
        return bytes([POSITIVE]) + bytes(node.state.memory[address : address + count])

    if command == UPLOAD:
        count = data[1]
        chunk = bytes(node.state.memory[node.state.mta : node.state.mta + count])
        node.state.mta += count
        return bytes([POSITIVE]) + chunk

    if command == DOWNLOAD:
        if not node.state.unlocked:
            # The reason the seed exists: writing constants into a running
            # controller is not the same as reading one.
            return bytes([ERROR, ERR_ACCESS_LOCKED])
        count = data[1]
        node.state.memory[node.state.mta : node.state.mta + count] = data[2 : 2 + count]
        node.state.mta += count
        return bytes([POSITIVE])

    return bytes([ERROR, ERR_CMD_UNKNOWN])
