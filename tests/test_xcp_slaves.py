# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Slaves that are not the demo's: long seeds, word-addressed memory, padded
frames, GET_STATUS and the A2L's EPK."""

from __future__ import annotations

import queue
import struct
import zlib

import pytest

from pycangui.xcp import RESOURCE_CAL, RESOURCE_PGM
from pycangui.xcp.a2l import A2l
from pycangui.xcp.engine import NativeCanEngine

SEED = bytes(range(0x10, 0x1A))  # ten bytes: more than one frame holds
KEY = bytes(b ^ 0xFF for b in SEED)


class FakeSlave:
    """An XCP slave behind a fake bus: answers what the engine sends."""

    def __init__(self, granularity: int = 1) -> None:
        self.granularity = granularity
        self.memory = bytearray(0x1000)
        self.mta = 0
        self.locked = RESOURCE_CAL | RESOURCE_PGM
        self.sent: list[bytes] = []
        self._seed_at = 0
        self._key = bytearray()
        self._asked = 0

    def answer(self, data: bytes) -> bytes:
        g = self.granularity
        command = data[0]
        if command == 0xFF:  # CONNECT
            basic = {1: 0, 2: 1, 4: 2}[g] << 1
            return bytes([0xFF, RESOURCE_CAL | RESOURCE_PGM, basic, 8, 8, 0, 1, 1])
        if command == 0xFD:  # GET_STATUS
            return bytes([0xFF, 0, self.locked, 0, 0, 0])
        if command == 0xF8:  # GET_SEED
            if data[1] == 0:
                self._seed_at, self._asked = 0, data[2]
            left = len(SEED) - self._seed_at
            piece = SEED[self._seed_at : self._seed_at + 6]
            self._seed_at += len(piece)
            return bytes([0xFF, left, *piece])
        if command == 0xF7:  # UNLOCK
            left = data[1]
            piece = data[2 : 2 + min(left, 6)]
            self._key += piece
            if left > len(piece):
                return bytes([0xFF, self.locked])
            if bytes(self._key) == KEY:
                self.locked &= ~self._asked
            self._key = bytearray()
            return bytes([0xFF, self.locked])
        if command == 0xF6:  # SET_MTA
            self.mta = struct.unpack("<I", data[4:8])[0]
            return bytes([0xFF])
        if command in (0xF4, 0xF5):  # SHORT_UPLOAD, UPLOAD
            if command == 0xF4:
                self.mta = struct.unpack("<I", data[4:8])[0]
            count = data[1] * g
            chunk = bytes(self.memory[self.mta : self.mta + count])
            self.mta += count
            return bytes([0xFF]) + bytes(g - 1) + chunk
        if command == 0xF0:  # DOWNLOAD
            count = data[1] * g
            at = 2 + (-2 % g)
            self.memory[self.mta : self.mta + count] = data[at : at + count]
            self.mta += count
            return bytes([0xFF])
        if command == 0xF3:  # BUILD_CHECKSUM
            count = struct.unpack("<I", data[4:8])[0] * g
            crc = zlib.crc32(bytes(self.memory[self.mta : self.mta + count]))
            return bytes([0xFF, 0x09, 0, 0]) + struct.pack("<I", crc)
        return bytes([0xFE, 0x20])


class FakeBus:
    def __init__(self, slave: FakeSlave, engine: NativeCanEngine) -> None:
        self.slave = slave
        self.engine = engine

    def send(self, can_id, data, extended=False) -> None:
        self.slave.sent.append(bytes(data))
        self.engine._responses.put(self.slave.answer(bytes(data)))


def engine_for(slave: FakeSlave) -> NativeCanEngine:
    engine = NativeCanEngine.__new__(NativeCanEngine)
    engine._responses = queue.Queue()
    engine._bus = FakeBus(slave, engine)
    engine.cmd_id, engine.res_id, engine.extended = 0x7A0, 0x7A1, False
    engine.info = None
    engine._max_cto_pgm = 8
    return engine


def test_a_seed_and_key_longer_than_a_frame_go_in_pieces():
    slave = FakeSlave()
    engine = engine_for(slave)
    engine.connect()
    assert engine.get_seed(RESOURCE_CAL) == SEED
    engine.unlock(KEY)
    unlocks = [frame for frame in slave.sent if frame[0] == 0xF7]
    assert len(unlocks) == 2, "ten bytes do not fit one frame"
    assert [frame[1] for frame in unlocks] == [10, 4], "each says how much is left"
    assert not slave.locked & RESOURCE_CAL
    assert engine.info.protection == RESOURCE_PGM, "what UNLOCK says is still locked"


def test_get_status_says_what_is_locked_on_connecting():
    slave = FakeSlave()
    slave.locked = RESOURCE_PGM
    info = engine_for(slave).connect()
    assert info.protection == RESOURCE_PGM


@pytest.mark.parametrize("granularity", [1, 2, 4])
def test_word_and_dword_addressed_slaves_are_read_and_written(granularity):
    slave = FakeSlave(granularity)
    engine = engine_for(slave)
    assert engine.connect().granularity == granularity
    data = bytes(range(1, 25))
    engine.write(0x100, data)
    assert bytes(slave.memory[0x100 : 0x100 + 24]) == data
    assert engine.read(0x100, 24) == data
    assert engine.read(0x104, 4) == data[4:8], "a short read, in one answer"
    for frame in slave.sent:
        if frame[0] == 0xF0:
            assert len(frame) <= 8
    _kind, value, _width = engine.checksum(0x100, 24)
    assert value == zlib.crc32(data)


def test_a_write_that_is_not_whole_elements_is_refused():
    engine = engine_for(FakeSlave(2))
    engine.connect()
    with pytest.raises(ValueError):
        engine.write(0x100, b"\x01")


def test_max_dlc_required_pads_every_command_to_eight_bytes():
    slave = FakeSlave()
    engine = engine_for(slave)
    engine.connect()
    assert len(slave.sent[0]) == 2, "short without it"
    engine.pad = True
    slave.sent.clear()
    engine.connect()
    engine.read(0x100, 2)
    assert slave.sent and all(len(frame) == 8 for frame in slave.sent)


A2L_WITH_EPK = """
/begin PROJECT p ""
/begin MODULE m ""
/begin MOD_PAR "x"
    EPK "build 42"
    ADDR_EPK 0x0F00
/end MOD_PAR
/begin IF_DATA XCP
    /begin XCP_ON_CAN 0x0100
        CAN_ID_MASTER 0x7A0
        CAN_ID_SLAVE 0x7A1
        MAX_DLC_REQUIRED
    /end XCP_ON_CAN
/end IF_DATA
/end MODULE
/end PROJECT
"""


def test_the_a2l_gives_its_epk_and_whether_frames_are_padded():
    a2l = A2l.parse(A2L_WITH_EPK)
    assert (a2l.epk, a2l.epk_address) == ("build 42", 0x0F00)
    assert a2l.xcp_on_can.padded


def test_the_epk_is_compared_on_connecting(app, tmp_path, monkeypatch):
    from pycangui.core.context import Context
    from pycangui.xcp.manager import XcpManager

    warned = []
    ctx = Context(log=print)
    monkeypatch.setattr(ctx, "warn", warned.append)
    slave = FakeSlave()
    slave.memory[0x0F00:0x0F08] = b"build 42"
    path = tmp_path / "epk.a2l"
    path.write_text(A2L_WITH_EPK, encoding="utf-8")

    manager = XcpManager.__new__(XcpManager)
    manager._ctx = ctx
    manager.a2l = A2l.load(str(path))
    manager.engine = engine_for(slave)
    manager.engine.connect()
    manager._apply_padding()
    assert manager.engine.pad, "MAX_DLC_REQUIRED"
    assert manager._check_epk() and warned == []

    slave.memory[0x0F00:0x0F08] = b"build 41"
    manager._check_epk()
    assert len(warned) == 1, "a different build is warned about"
