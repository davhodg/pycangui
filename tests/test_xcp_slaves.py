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
from pycangui.xcp.engine import DEFAULT_TIMEOUTS, NativeCanEngine

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
        #: Commands whose next answer goes missing, and those answered busy once.
        self.lose: list[int] = []
        self.busy: list[int] = []

    def undo(self) -> None:
        """Put back what the last command did, for a slave that said it was busy."""
        self.memory[:] = self._before
        self.mta = self._mta_before

    def answer(self, data: bytes) -> bytes:
        g = self.granularity
        command = data[0]
        self._before, self._mta_before = bytearray(self.memory), self.mta
        if command == 0xFC:  # SYNCH: answered with ERR_CMD_SYNCH, which means it worked
            return bytes([0xFE, 0x00])
        if command == 0xFA:  # GET_ID: the A2L's name, uploaded from the MTA
            self.mta = 0xF00
            self.memory[0xF00:0xF07] = b"fake.a2"
            self.memory[0xF07] = ord("l")
            return bytes([0xFF, 0, 0, 0]) + struct.pack("<I", 8)
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
        answer = self.slave.answer(bytes(data))
        if data[0] in self.slave.lose:
            self.slave.lose.remove(data[0])  # done, and its answer lost on the way
            return
        if data[0] in self.slave.busy:
            self.slave.busy.remove(data[0])
            self.engine._responses.put(bytes([0xFE, 0x10]))  # ERR_CMD_BUSY, and nothing done
            self.slave.undo()
            return
        self.engine._responses.put(answer)


def engine_for(slave: FakeSlave) -> NativeCanEngine:
    engine = NativeCanEngine.__new__(NativeCanEngine)
    engine._responses = queue.Queue()
    engine._bus = FakeBus(slave, engine)
    engine.cmd_id, engine.res_id, engine.extended = 0x7A0, 0x7A1, False
    engine.info = None
    engine._max_cto_pgm = 8
    engine.timeouts = {**DEFAULT_TIMEOUTS, "T1": 0.05, "T7": 0.01}
    engine.said = []
    engine.on_recover = engine.said.append
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


# --- recovering ------------------------------------------------------------------------------
def test_a_lost_answer_is_followed_by_synch_and_the_command_again():
    slave = FakeSlave()
    engine = engine_for(slave)
    engine.connect()
    slave.memory[0x100:0x104] = b"abcd"
    slave.lose.append(0xF4)  # SHORT_UPLOAD
    assert engine.read(0x100, 4) == b"abcd"
    assert [frame[0] for frame in slave.sent[-3:]] == [0xF4, 0xFC, 0xF4], "again, after SYNCH"
    assert len(engine.said) == 1, "and the log says so"


def test_an_upload_whose_answer_is_lost_does_not_skip_bytes():
    slave = FakeSlave()
    engine = engine_for(slave)
    engine.connect()
    slave.memory[0x100:0x120] = bytes(range(32))
    slave.lose.append(0xF5)  # the first UPLOAD's answer: the slave has moved its MTA on
    assert engine.read(0x100, 32) == bytes(range(32))
    sent = [frame[0] for frame in slave.sent]
    assert sent[sent.index(0xFC) + 1] == 0xF6, "the MTA put back before the UPLOAD again"


def test_a_write_whose_answer_is_lost_is_written_once_where_it_belongs():
    slave = FakeSlave()
    engine = engine_for(slave)
    engine.connect()
    slave.lose.append(0xF0)  # DOWNLOAD
    engine.write(0x200, bytes(range(1, 15)))
    assert bytes(slave.memory[0x200:0x20E]) == bytes(range(1, 15))
    assert slave.memory[0x20E] == 0, "nothing written beyond it"


def test_a_slave_with_no_answer_at_all_is_given_up_on():
    slave = FakeSlave()
    engine = engine_for(slave)
    engine.connect()
    slave.lose.extend([0xF4] * 3)
    with pytest.raises(TimeoutError):
        engine.read(0x100, 4)
    assert len(engine.said) == 2, "sent again twice, as the standard says, and then given up"


def test_a_busy_slave_is_asked_again():
    slave = FakeSlave()
    engine = engine_for(slave)
    engine.connect()
    slave.busy.append(0xF0)
    engine.write(0x300, b"xy")
    assert bytes(slave.memory[0x300:0x302]) == b"xy"


def test_the_seed_is_not_sent_again_but_starts_over():
    slave = FakeSlave()
    engine = engine_for(slave)
    engine.connect()
    slave.lose.append(0xF8)
    with pytest.raises(TimeoutError):
        engine.get_seed(RESOURCE_CAL)


# --- GET_ID and PROTOCOL_LAYER ------------------------------------------------------------------
def test_get_id_uploads_the_a2ls_name():
    slave = FakeSlave()
    engine = engine_for(slave)
    engine.connect()
    assert engine.get_id() == "fake.a2l"


def test_the_a2l_gives_the_timeouts():
    a2l = A2l.parse(
        """
        /begin MODULE m ""
        /begin IF_DATA XCP
          /begin PROTOCOL_LAYER 0x0104 25 2000 3000 4000 5000 60 7 8 8
            BYTE_ORDER_MSB_LAST ADDRESS_GRANULARITY_BYTE
          /end PROTOCOL_LAYER
        /end IF_DATA
        /end MODULE
        """
    )
    assert a2l.timeouts["T1"] == pytest.approx(0.025)
    assert a2l.timeouts["T4"] == pytest.approx(4.0) and a2l.timeouts["T7"] == pytest.approx(0.007)
