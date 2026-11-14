# SPDX-License-Identifier: MIT-0
#
# A starting point, copied into your workspace for you to change. It is
# yours to edit, keep private or give away: pycangui claims nothing in it
# and asks for no credit, so what you write here needs nobody's permission.
"""An XCP slave: a block of memory the XCP pane can read and calibrate.

The example of a node that does *both* halves -- it answers commands in
``on_frame`` like the UDS server, and moves its measurements in ``poll`` like
the CANopen device -- which is what a real calibration slave does.

XCP is a memory protocol: the master says "give me four bytes from this
address", and the A2L says which address a named measurement lives at. So
this node is a ``bytearray`` and a command handler, and the addresses below
are the ones in ``resources/demo.a2l``.

Calibration is locked until a seed and key exchange, which is the usual
arrangement: reading a slave is one thing and writing new constants into a
running controller is quite another. Flashing is locked the same way, by its
own resource, PGM.

From 0x4000 up the memory behaves like flash: PROGRAM_CLEAR erases it to
FF, and PROGRAM can only turn ones into zeros, so writing over what was not
erased first leaves a mixture -- which a read back or a checksum then shows.
BUILD_CHECKSUM answers with CRC-32.

XCP on CAN has no standard identifiers -- every project picks a pair -- so
these are pycangui's own.

To make it yours: point pycangui at your A2L, change the ids, and lay your
measurements out where your A2L says they are.
"""

from __future__ import annotations

import math
import struct
import zlib

NAME = "XCP slave"
DESCRIPTION = "A calibratable memory with flash: connect, upload, unlock, download, program."
RATE_HZ = 20

COMMAND_ID = 0x7A0
RESPONSE_ID = 0x7A1

#: Command codes, from the ASAM XCP standard.
CONNECT = 0xFF
DISCONNECT = 0xFE
GET_STATUS = 0xFD
GET_SEED = 0xF8
UNLOCK = 0xF7
SET_MTA = 0xF6
UPLOAD = 0xF5
SHORT_UPLOAD = 0xF4
BUILD_CHECKSUM = 0xF3
DOWNLOAD = 0xF0
PROGRAM_START = 0xD2
PROGRAM_CLEAR = 0xD1
PROGRAM = 0xD0
PROGRAM_RESET = 0xCF

POSITIVE = 0xFF
ERROR = 0xFE
ERR_CMD_UNKNOWN = 0x20
ERR_OUT_OF_RANGE = 0x22
ERR_ACCESS_LOCKED = 0x25
ERR_SEQUENCE = 0x29
ERR_ACCESS_DENIED = 0x35

#: Which resources are protected. Bit 0 is calibration, so a master has to
#: unlock before it may write anything; bit 4 is programming.
RESOURCE_CAL = 0x01
RESOURCE_PGM = 0x10
#: The checksum BUILD_CHECKSUM makes, by its XCP number: CRC-32.
CHECKSUM_CRC_32 = 0x09

MEMORY_BYTES = 0x8000
#: Flash: erased by PROGRAM_CLEAR, written by PROGRAM. resources/demo.a2l
#: lists it, in two sectors.
FLASH_START = 0x4000
FLASH_END = 0x8000
ENGINE_SPEED = 0x1000  # a measurement: poll() moves it
BATTERY_MV = 0x1002
COOLANT_C = 0x1004
SPEED_LIMIT = 0x2000  # characteristics: the master writes these
IDLE_TARGET = 0x2002

#: The seed handed out, and the key is every byte inverted. A real slave
#: keeps a secret and an algorithm; this shows that the exchange happens.
SEED = bytes([0xA5, 0x5A, 0x12, 0x34])

#: The software's identity, where resources/demo.a2l's MOD_PAR says it is
#: (EPK and ADDR_EPK). The XCP pane reads it on connecting and says if the
#: A2L was made for something else.
EPK_ADDRESS = 0x0F00
EPK = b"pycangui demo 1.0"


def start(node, *, ctx):
    memory = bytearray(MEMORY_BYTES)
    memory[FLASH_START:FLASH_END] = b"\xff" * (FLASH_END - FLASH_START)  # erased
    memory[EPK_ADDRESS : EPK_ADDRESS + len(EPK)] = EPK
    struct.pack_into("<H", memory, BATTERY_MV, 1320)  # 13.20 V
    struct.pack_into("<h", memory, COOLANT_C, 90)
    struct.pack_into("<H", memory, SPEED_LIMIT, 6500)
    struct.pack_into("<H", memory, IDLE_TARGET, 850)
    node.state.memory = memory
    node.state.connected = False
    node.state.unlocked = 0  # the resources unlocked, as bits
    node.state.asked = 0  # the resource the last seed was for
    node.state.programming = False
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
        resources = RESOURCE_CAL | RESOURCE_PGM
        return bytes([POSITIVE, resources, 0x00, 8]) + struct.pack("<H", 8) + bytes([0x01, 0x01])

    if not node.state.connected:
        # Everything else is meaningless before CONNECT, and answering
        # anyway would let a broken master look like a working one.
        return bytes([ERROR, ERR_OUT_OF_RANGE])

    if command == DISCONNECT:
        node.state.connected = False
        return bytes([POSITIVE])

    if command == GET_STATUS:
        # The session, then which resources are still protected.
        locked = (RESOURCE_CAL | RESOURCE_PGM) & ~node.state.unlocked
        return bytes([POSITIVE, 0x00, locked, 0x00, 0x00, 0x00])

    if command == GET_SEED:
        # A master that names no resource is taken to mean calibration,
        # which is what this slave unlocked for anybody before it had PGM.
        node.state.asked = (data[2] if len(data) > 2 else 0) or RESOURCE_CAL
        if node.state.unlocked & node.state.asked:
            return bytes([POSITIVE, 0])  # no seed: it is not locked
        return bytes([POSITIVE, len(SEED), *SEED])

    if command == UNLOCK:
        key = data[2 : 2 + data[1]]
        if key != bytes(b ^ 0xFF for b in SEED):
            return bytes([ERROR, ERR_ACCESS_DENIED])
        node.state.unlocked |= node.state.asked
        # What is still protected.
        return bytes([POSITIVE, (RESOURCE_CAL | RESOURCE_PGM) & ~node.state.unlocked])

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
        if not node.state.unlocked & RESOURCE_CAL:
            # The reason the seed exists: writing constants into a running
            # controller is not the same as reading one.
            return bytes([ERROR, ERR_ACCESS_LOCKED])
        count = data[1]
        node.state.memory[node.state.mta : node.state.mta + count] = data[2 : 2 + count]
        node.state.mta += count
        return bytes([POSITIVE])

    if command == BUILD_CHECKSUM:
        size = struct.unpack("<I", data[4:8])[0]
        start = node.state.mta
        if start + size > len(node.state.memory):
            return bytes([ERROR, ERR_OUT_OF_RANGE])
        crc = zlib.crc32(bytes(node.state.memory[start : start + size])) & 0xFFFFFFFF
        node.state.mta += size
        return bytes([POSITIVE, CHECKSUM_CRC_32, 0, 0]) + struct.pack("<I", crc)

    return _programming(node, command, data)


def _programming(node, command: int, data: bytes) -> bytes:
    """PROGRAM_START, then clear, program and reset: flashing.

    Unlocking PGM comes first, and PROGRAM_START before anything else, as a
    real bootloader insists.
    """
    if command == PROGRAM_START:
        if not node.state.unlocked & RESOURCE_PGM:
            return bytes([ERROR, ERR_ACCESS_LOCKED])
        node.state.programming = True
        # reserved, COMM_MODE_PGM, MAX_CTO_PGM, MAX_BS_PGM, MIN_ST_PGM, QUEUE_SIZE_PGM
        return bytes([POSITIVE, 0x00, 0x00, 8, 0x00, 0x00, 0x00])

    if command not in (PROGRAM_CLEAR, PROGRAM, PROGRAM_RESET):
        return bytes([ERROR, ERR_CMD_UNKNOWN])
    if not node.state.programming:
        return bytes([ERROR, ERR_SEQUENCE])

    if command == PROGRAM_CLEAR:
        size = struct.unpack("<I", data[4:8])[0]
        start = node.state.mta
        if start < FLASH_START or start + size > FLASH_END:
            return bytes([ERROR, ERR_OUT_OF_RANGE])
        node.state.memory[start : start + size] = b"\xff" * size
        return bytes([POSITIVE])

    if command == PROGRAM:
        count = data[1]
        if count == 0:
            return bytes([POSITIVE])  # the end of a memory segment
        start = node.state.mta
        if start < FLASH_START or start + count > FLASH_END:
            return bytes([ERROR, ERR_OUT_OF_RANGE])
        # Flash only turns ones into zeros: what was not erased stays mixed.
        for at, byte in enumerate(data[2 : 2 + count]):
            node.state.memory[start + at] &= byte
        node.state.mta += count
        return bytes([POSITIVE])

    # PROGRAM_RESET: the session ends, and with it every unlock.
    node.state.programming = False
    node.state.connected = False
    node.state.unlocked = 0
    return bytes([POSITIVE])
