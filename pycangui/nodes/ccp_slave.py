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

It has the same flash from 0x4000 up: CLEAR_MEMORY erases it to FF, and
PROGRAM can only turn ones into zeros. CCP leaves the checksum algorithm to
the ECU; this one's BUILD_CHKSUM is a 16-bit sum of the bytes, XCP's ADD_12.

To make it yours: change the identifiers and the station address, point
pycangui at your A2L, and lay the memory out where that A2L says.
"""

from __future__ import annotations

import math
import struct

NAME = "CCP slave"
DESCRIPTION = "A calibratable memory with flash, over CCP: upload, unlock, download, program."
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
BUILD_CHKSUM = 0x0E
SHORT_UP = 0x0F
CLEAR_MEMORY = 0x10
GET_SEED = 0x12
UNLOCK = 0x13
PROGRAM = 0x18

#: Resources, as CCP numbers them: calibration and programming.
RESOURCE_CAL = 0x01
RESOURCE_PGM = 0x40

#: Every answer is a command return message: this, a return code, then the
#: counter the command carried.
RETURN = 0xFF
ACKNOWLEDGE = 0x00
ERR_SYNTAX = 0x31
ERR_OUT_OF_RANGE = 0x32
ERR_ACCESS_DENIED = 0x33
ERR_ACCESS_LOCKED = 0x35
ERR_NOT_AVAILABLE = 0x36

MEMORY_BYTES = 0x8000
FLASH_START = 0x4000
FLASH_END = 0x8000
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

#: The software's identity, where resources/demo.a2l's MOD_PAR says it is
#: (EPK and ADDR_EPK). The XCP pane reads it on connecting and says if the
#: A2L was made for something else.
EPK_ADDRESS = 0x0F00
EPK = b"pycangui demo 1.0"


def start(node, *, ctx):
    memory = bytearray(MEMORY_BYTES)
    memory[FLASH_START:FLASH_END] = b"\xff" * (FLASH_END - FLASH_START)  # erased
    memory[EPK_ADDRESS : EPK_ADDRESS + len(EPK)] = EPK
    struct.pack_into(f"{ORDER}H", memory, BATTERY_MV, 1320)  # 13.20 V
    struct.pack_into(f"{ORDER}h", memory, COOLANT_C, 90)
    struct.pack_into(f"{ORDER}H", memory, SPEED_LIMIT, 6500)
    struct.pack_into(f"{ORDER}H", memory, IDLE_TARGET, 850)
    node.state.memory = memory
    node.state.connected = False
    node.state.unlocked = 0  # the resources unlocked, as bits
    node.state.asked = 0  # the resource the last seed was for
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
            node.state.unlocked = 0
        node.state.connected = False
        return _answer(ACKNOWLEDGE, counter)

    if command == GET_SEED:
        node.state.asked = (body[0] if body else 0) or RESOURCE_CAL
        protected = 0 if node.state.unlocked & node.state.asked else 1
        return _answer(ACKNOWLEDGE, counter, bytes([protected, *SEED]))

    if command == UNLOCK:
        if body[:4] != bytes(b ^ 0xFF for b in SEED):
            return _answer(ERR_ACCESS_DENIED, counter)
        node.state.unlocked |= node.state.asked
        return _answer(ACKNOWLEDGE, counter, bytes([node.state.unlocked]))

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
        if not node.state.unlocked & RESOURCE_CAL:
            # The reason the seed exists: writing constants into a running
            # controller is not the same as reading one.
            return _answer(ERR_ACCESS_LOCKED, counter)
        size = body[0]
        node.state.memory[node.state.mta : node.state.mta + size] = body[1 : 1 + size]
        node.state.mta += size
        return _answer(ACKNOWLEDGE, counter, struct.pack(">I", node.state.mta)[:4])

    if command == BUILD_CHKSUM:
        size, start = struct.unpack(">I", body[:4])[0], node.state.mta
        if start + size > len(node.state.memory):
            return _answer(ERR_OUT_OF_RANGE, counter)
        total = sum(node.state.memory[start : start + size]) & 0xFFFF
        return _answer(ACKNOWLEDGE, counter, bytes([2]) + struct.pack(">H", total))

    if command in (CLEAR_MEMORY, PROGRAM):
        return _flash(node, command, counter, body)

    return _answer(ERR_SYNTAX, counter)


def _flash(node, command: int, counter: int, body: bytes) -> bytes:
    """CLEAR_MEMORY and PROGRAM, both once PGM is unlocked."""
    if not node.state.unlocked & RESOURCE_PGM:
        return _answer(ERR_ACCESS_LOCKED, counter)
    start = node.state.mta
    if command == CLEAR_MEMORY:
        size = struct.unpack(">I", body[:4])[0]
        if start < FLASH_START or start + size > FLASH_END:
            return _answer(ERR_OUT_OF_RANGE, counter)
        node.state.memory[start : start + size] = b"\xff" * size
        return _answer(ACKNOWLEDGE, counter)
    size = body[0]
    if start < FLASH_START or start + size > FLASH_END:
        return _answer(ERR_OUT_OF_RANGE, counter)
    # Flash only turns ones into zeros: what was not erased stays mixed.
    for at, byte in enumerate(body[1 : 1 + size]):
        node.state.memory[start + at] &= byte
    node.state.mta += size
    return _answer(ACKNOWLEDGE, counter, struct.pack(">I", node.state.mta)[:4])
