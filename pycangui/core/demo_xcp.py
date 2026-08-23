"""A simulated XCP-on-CAN slave for the demo device (0x7A0 command, 0x7A1
response on the virtual bus; XCP has no standard ids and 0x7E0 is taken here
by the demo UDS server).  Implements CONNECT, DISCONNECT, GET_SEED /
UNLOCK (byte-invert key, CAL locked until unlocked), SET_MTA, SHORT_UPLOAD,
UPLOAD and DOWNLOAD against a small byte-addressed memory that matches
resources/demo.a2l.  Engine speed at 0x1000 moves so plots have something."""

from __future__ import annotations

import math
import struct

import can
from PySide6.QtCore import QObject, QTimer

CMD_ID = 0x7A0
RES_ID = 0x7A1
RESOURCE_CAL = 0x01


class DemoXcpSlave(QObject):
    def __init__(self, bus: can.BusABC, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._bus = bus
        self.memory = bytearray(0x3000)
        self._connected = False
        self._cal_unlocked = False
        self._seed = bytes([0xA5, 0x5A, 0x12, 0x34])
        self._mta = 0
        self._i = 0
        struct.pack_into("<H", self.memory, 0x1002, 1320)  # battery 13.20 V
        struct.pack_into("<h", self.memory, 0x1004, 90)  # coolant 90 C
        struct.pack_into("<H", self.memory, 0x2000, 6500)  # speed limit
        struct.pack_into("<H", self.memory, 0x2002, 850)  # idle target
        self._timer = QTimer(self, interval=50, timeout=self._tick)
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _tick(self) -> None:
        self._i += 1
        rpm = 800 + 3000 * (1 + math.sin(self._i / 40)) / 2
        struct.pack_into("<H", self.memory, 0x1000, int(rpm))

    # --- called by DemoDevice's shared collector ------------------------------
    def on_frame(self, msg: can.Message) -> None:
        if msg.is_rx and msg.arbitration_id == CMD_ID and not msg.is_extended_id:
            resp = self._handle(bytes(msg.data))
            if resp is not None:
                self._bus.send(can.Message(arbitration_id=RES_ID, data=resp, is_extended_id=False))

    def _handle(self, data: bytes) -> bytes | None:
        if not data:
            return None
        pid = data[0]
        if pid == 0xFF:  # CONNECT
            self._connected = True
            # resource, commModeBasic(little-endian, byteGranularity 1), maxCTO, maxDTO
            return bytes([0xFF, RESOURCE_CAL, 0x00, 8]) + struct.pack("<H", 8) + bytes([0x01, 0x01])
        if not self._connected:
            return bytes([0xFE, 0x22])  # ERR_OUT_OF_RANGE-ish: not connected
        if pid == 0xFE:  # DISCONNECT
            self._connected = False
            return bytes([0xFF])
        if pid == 0xF8:  # GET_SEED
            return bytes([0xFF, len(self._seed), *self._seed])
        if pid == 0xF7:  # UNLOCK
            key = data[2 : 2 + data[1]]
            if key == bytes(b ^ 0xFF for b in self._seed):
                self._cal_unlocked = True
                return bytes([0xFF, 0x00])  # remaining resource protection = none
            return bytes([0xFE, 0x35])  # ERR_ACCESS_LOCKED-ish (invalid key)
        if pid == 0xF6:  # SET_MTA
            self._mta = struct.unpack("<I", data[4:8])[0]
            return bytes([0xFF])
        if pid == 0xF4:  # SHORT_UPLOAD
            n = data[1]
            addr = struct.unpack("<I", data[4:8])[0]
            return bytes([0xFF]) + bytes(self.memory[addr : addr + n])
        if pid == 0xF5:  # UPLOAD
            n = data[1]
            chunk = bytes(self.memory[self._mta : self._mta + n])
            self._mta += n
            return bytes([0xFF]) + chunk
        if pid == 0xF0:  # DOWNLOAD
            if not self._cal_unlocked:
                return bytes([0xFE, 0x25])  # ERR_ACCESS_LOCKED
            n = data[1]
            self.memory[self._mta : self._mta + n] = data[2 : 2 + n]
            self._mta += n
            return bytes([0xFF])
        return bytes([0xFE, 0x20])  # ERR_CMD_UNKNOWN
