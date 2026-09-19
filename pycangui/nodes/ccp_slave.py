# SPDX-License-Identifier: MIT-0
#
# A starting point, copied into your workspace for you to change. It is
# yours to edit, keep private or give away: pycangui claims nothing in it
# and asks for no credit, so what you write here needs nobody's permission.
"""A CCP slave: the same calibratable memory, in the protocol XCP replaced.

Worth having beside ``xcp_slave`` for two reasons. Plenty of controllers in
service speak CCP and nothing else, so this is what somebody testing against
one can practise on; and it is what proves the calibration pane really is
protocol-agnostic, since the same pane reads the same A2L through a different
engine.

Three things differ from the XCP slave, and they are the three things that
differ in the protocol: a station address, so several controllers can share
one pair of identifiers; a counter in every command, echoed in the answer;
and addresses in Motorola order. Everything else -- a block of memory, a
seed and key before anything may be written -- is the same job.

To make it yours: change the identifiers and the station address, point
pycangui at your A2L, and lay the memory out where that A2L says.
"""

from __future__ import annotations

import math
import struct

NAME = "CCP slave"
DESCRIPTION = "A calibratable memory over CCP: connect, upload, unlock, download."
RATE_HZ = 20

#: CCP fixes no identifiers either, so these are pycangui's own, and chosen
#: not to collide with the XCP slave's so both can run at once.
COMMAND_ID = 0x7B0
RESPONSE_ID = 0x7B1
#: Which controller these identifiers are talking to. A master that asks for
#: another station is answered by that one, not this.
STATION = 0x0001

#: Command codes, from the ASAM CCP standard.
CONNECT = 0x01
SET_MTA = 0x02
DNLOAD = 0x03
UPLOAD = 0x04
DISCONNECT = 0x07
SHORT_UP = 0x0F
GET_SEED = 0x12
UNLOCK = 0x13

#: Every answer is a command return message: this, a return code, then the
#: counter the command carried.
RETURN = 0xFF
ACKNOWLEDGE = 0x00
ERR_SYNTAX = 0x31
ERR_OUT_OF_RANGE = 0x32
ERR_ACCESS_DENIED = 0x33
ERR_ACCESS_LOCKED = 0x35
ERR_NOT_AVAILABLE = 0x36

MEMORY_BYTES = 0x3000
ENGINE_SPEED = 0x1000  # a measurement: poll() moves it
BATTERY_MV = 0x1002
COOLANT_C = 0x1004
SPEED_LIMIT = 0x2000  # characteristics: the master writes these
IDLE_TARGET = 0x2002

#: The seed handed out, and the key is every byte inverted -- the same
#: arrangement as the XCP slave, so one hook answers for both.
SEED = bytes([0xA5, 0x5A, 0x12, 0x34])

#: CCP is a Motorola-order protocol, so the values in memory are written
#: big-endian: an A2L for a CCP slave says so, and reading them the other
#: way round is the classic first mistake.
ORDER = ">"


def start(node, *, ctx):
    memory = bytearray(MEMORY_BYTES)
    struct.pack_into(f"{ORDER}H", memory, BATTERY_MV, 1320)  # 13.20 V
    struct.pack_into(f"{ORDER}h", memory, COOLANT_C, 90)
    struct.pack_into(f"{ORDER}H", memory, SPEED_LIMIT, 6500)
    struct.pack_into(f"{ORDER}H", memory, IDLE_TARGET, 850)
    node.state.memory = memory
    node.state.connected = False
    node.state.unlocked = False
    node.state.mta = 0
    node.state.polls = 0


def poll(node, *, ctx):
    """Move the measurements, so there is something that changes to read."""
    node.state.polls += 1
    rpm = 800 + 3000 * (1 + math.sin(node.state.polls / 40)) / 2
    struct.pack_into(f"{ORDER}H", node.state.memory, ENGINE_SPEED, int(rpm))


def on_frame(node, frame, *, ctx):
    if frame.arbitration_id != COMMAND_ID or frame.is_extended_id:
        return
    if (answer := _handle(node, bytes(frame.data))) is not None:
        node.send(RESPONSE_ID, answer)


def _answer(code: int, counter: int, payload: bytes = b"") -> bytes:
    """A command return message: the code, the counter back, then the data."""
    return (bytes([RETURN, code, counter]) + payload).ljust(8, b"\x00")[:8]


def _handle(node, data: bytes) -> bytes | None:
    if len(data) < 2:
        return None
    command, counter = data[0], data[1]
    body = data[2:]

    if command == CONNECT:
        # Little-endian, the one place CCP is not Motorola order. A master
        # asking for another station is talking to a different controller,
        # and this one says nothing at all rather than answering for it.
        if len(body) < 2 or struct.unpack("<H", body[:2])[0] != STATION:
            return None
        node.state.connected = True
        return _answer(ACKNOWLEDGE, counter)

    if not node.state.connected:
        return _answer(ERR_NOT_AVAILABLE, counter)

    if command == DISCONNECT:
        # Byte 0 says whether this ends the session or is a pause. A pause
        # keeps the unlock, which is what a master reconnecting expects.
        if body and body[0]:
            node.state.unlocked = False
        node.state.connected = False
        return _answer(ACKNOWLEDGE, counter)

    if command == GET_SEED:
        return _answer(ACKNOWLEDGE, counter, bytes([0x01, *SEED]))

    if command == UNLOCK:
        if body[:4] != bytes(b ^ 0xFF for b in SEED):
            return _answer(ERR_ACCESS_DENIED, counter)
        node.state.unlocked = True
        return _answer(ACKNOWLEDGE, counter, bytes([0x01]))

    if command == SET_MTA:
        if len(body) < 6:
            return _answer(ERR_SYNTAX, counter)
        node.state.mta = struct.unpack(">I", body[2:6])[0]
        return _answer(ACKNOWLEDGE, counter)

    if command == SHORT_UP:
        size, address = body[0], struct.unpack(">I", body[2:6])[0]
        if address + size > len(node.state.memory):
            return _answer(ERR_OUT_OF_RANGE, counter)
        return _answer(ACKNOWLEDGE, counter, bytes(node.state.memory[address : address + size]))

    if command == UPLOAD:
        size = body[0]
        chunk = bytes(node.state.memory[node.state.mta : node.state.mta + size])
        node.state.mta += size
        return _answer(ACKNOWLEDGE, counter, chunk)

    if command == DNLOAD:
        if not node.state.unlocked:
            # The reason the seed exists: writing constants into a running
            # controller is not the same as reading one.
            return _answer(ERR_ACCESS_LOCKED, counter)
        size = body[0]
        node.state.memory[node.state.mta : node.state.mta + size] = body[1 : 1 + size]
        node.state.mta += size
        return _answer(ACKNOWLEDGE, counter, struct.pack(">I", node.state.mta)[:4])

    return _answer(ERR_SYNTAX, counter)
