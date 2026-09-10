"""An XCP slave: a block of memory the XCP pane can read and write.

The example of a node that does *both* halves -- it answers commands in
``on_frame`` like the UDS server, and moves its measurements in ``poll`` like
the CANopen device -- which is what a real calibration slave does.

XCP is a memory protocol: the master says "give me four bytes from this
address" and the A2L says which address a named measurement lives at.  So
this node is a ``bytearray`` and a small command handler, and the addresses
below match ``resources/demo.a2l``.

XCP on CAN has no standard identifiers -- every project picks a pair -- so
these are pycangui's own for the demo.

To make it yours: point ``pycangui`` at your A2L, change the ids, and lay
your measurements out at the addresses your A2L declares.
"""

from __future__ import annotations

import math
import struct

NAME = "XCP slave"
DESCRIPTION = "A calibratable memory: CONNECT, UPLOAD, DOWNLOAD, and moving values."
RATE_HZ = 20

COMMAND_ID = 0x7A0
RESPONSE_ID = 0x7A1

#: XCP commands, from the ASAM standard.  Only the ones a tool needs before
#: it can show anything are here.
CONNECT = 0xFF
DISCONNECT = 0xFE
GET_STATUS = 0xFD
SET_MTA = 0xF6
UPLOAD = 0xF5
SHORT_UPLOAD = 0xF4
DOWNLOAD = 0xF0

POSITIVE = 0xFF
ERROR = 0xFE
ERR_CMD_UNKNOWN = 0x20
ERR_OUT_OF_RANGE = 0x22

MEMORY_BYTES = 0x3000
ENGINE_SPEED = 0x1000  # a measurement, moved by poll()
BATTERY_MV = 0x1002
COOLANT_C = 0x1004
SPEED_LIMIT = 0x2000  # a characteristic, written by the master


def start(node, *, ctx):
    memory = bytearray(MEMORY_BYTES)
    struct.pack_into("<H", memory, BATTERY_MV, 1320)  # 13.20 V
    struct.pack_into("<h", memory, COOLANT_C, 90)
    struct.pack_into("<H", memory, SPEED_LIMIT, 6500)
    node.state.memory = memory
    node.state.connected = False
    node.state.mta = 0
    node.state.polls = 0


def poll(node, *, ctx):
    """Move the measurements, so the XCP pane has something that changes.

    A slave whose values never move cannot tell you whether the tool is
    reading the right address or simply reading zero.
    """
    node.state.polls += 1
    rpm = 800 + 1500 * (1 + math.sin(node.state.polls / 40))
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
        # The master reads its whole world out of this one response: byte
        # order, the biggest packet it may ask for, and the protocol version.
        return bytes([POSITIVE, 0x00, 0x00, 0x08, 0x08, 0x00, 0x01, 0x01])

    if not node.state.connected:
        # Everything else is meaningless before CONNECT, and answering
        # anyway would let a broken master look like a working one.
        return bytes([ERROR, ERR_CMD_UNKNOWN])

    if command == DISCONNECT:
        node.state.connected = False
        return bytes([POSITIVE])

    if command == GET_STATUS:
        return bytes([POSITIVE, 0x00, 0x00, 0x00, 0x00, 0x00])

    if command == SET_MTA and len(data) >= 8:
        node.state.mta = struct.unpack_from(">I", data, 4)[0]
        return bytes([POSITIVE])

    if command == UPLOAD and len(data) >= 2:
        return _read(node, node.state.mta, data[1], advance=True)

    if command == SHORT_UPLOAD and len(data) >= 8:
        return _read(node, struct.unpack_from(">I", data, 4)[0], data[1])

    if command == DOWNLOAD and len(data) >= 2:
        count = data[1]
        payload = data[2 : 2 + count]
        end = node.state.mta + len(payload)
        if end > len(node.state.memory):
            return bytes([ERROR, ERR_OUT_OF_RANGE])
        node.state.memory[node.state.mta : end] = payload
        node.state.mta = end
        return bytes([POSITIVE])

    return bytes([ERROR, ERR_CMD_UNKNOWN])


def _read(node, address: int, count: int, advance: bool = False) -> bytes:
    """Bytes out of the slave's memory, as an XCP response.

    A response is eight bytes whatever was asked for, so a short read is
    padded -- the master knows how many of them it wanted.
    """
    if count > 7 or address + count > len(node.state.memory):
        return bytes([ERROR, ERR_OUT_OF_RANGE])
    chunk = bytes(node.state.memory[address : address + count])
    if advance:
        node.state.mta = address + count
    return bytes([POSITIVE]) + chunk + b"\x00" * (7 - len(chunk))
