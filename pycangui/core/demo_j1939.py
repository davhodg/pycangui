"""A simulated J1939 engine controller for the demo device: claims address
0x00, broadcasts EEC1 / CCVS1 / DM1, and answers a Request for ComponentID
(65259) with a multi-packet BAM transfer."""

from __future__ import annotations

import math
import struct

import can
import j1939 as j1939lib
from PySide6.QtCore import QObject, QTimer

from pycangui.j1939 import (
    Dtc,
    _compat,  # noqa: F401 - patches pythoncom typo in can-j1939
    encode_dm1,
)

ENGINE_NAME = j1939lib.Name(
    arbitrary_address_capable=False,
    industry_group=j1939lib.Name.IndustryGroup.OnHighway,
    vehicle_system_instance=0,
    vehicle_system=0,
    function=0,  # engine
    function_instance=0,
    ecu_instance=0,
    manufacturer_code=66,
    identity_number=0x1234,
)


class DemoJ1939Node(QObject):
    def __init__(
        self, bus: can.BusABC, notifier: can.Notifier, parent: QObject | None = None
    ) -> None:
        super().__init__(parent)
        self._bus = bus
        self.ecu = j1939lib.ElectronicControlUnit(send_message=self._send)
        self._listeners = list(self.ecu._listeners)
        for listener in self._listeners:
            notifier.add_listener(listener)
        self._notifier = notifier
        self.ca = self.ecu.add_ca(name=ENGINE_NAME, device_address=0x00)
        self.ca.subscribe_request(self._on_request)
        self.ca.start()
        self.rpm = 800.0
        self.speed_kph = 0.0
        self._i = 0
        self._timer = QTimer(self, interval=100, timeout=self._tick)
        self._timer.start()

    def _send(self, can_id: int, extended_id: bool, data, fd_format: bool = False) -> None:
        self._bus.send(
            can.Message(arbitration_id=can_id, is_extended_id=extended_id, data=bytes(data))
        )

    def _claimed(self) -> bool:
        return self.ca.state == j1939lib.ControllerApplication.State.NORMAL

    def _tick(self) -> None:
        if not self._claimed():
            return
        self._i += 1
        self.rpm = 800 + 600 * (1 + math.sin(self._i / 20))
        self.speed_kph = max(0.0, (self.rpm - 800) / 12)
        # EEC1 (61444): engine speed SPN 190 at bytes 4-5, 0.125 rpm/bit
        eec1 = (
            bytes([0xFF, 0xFF, 0xFF]) + struct.pack("<H", int(self.rpm / 0.125)) + bytes([0xFF] * 3)
        )
        self.ca.send_pgn(0, 0xF0, 0x04, 3, list(eec1))
        if self._i % 2 == 0:  # CCVS1 (65265): wheel speed SPN 84 at bytes 2-3, 1/256 km/h per bit
            ccvs = bytes([0xFF]) + struct.pack("<H", int(self.speed_kph * 256)) + bytes([0xFF] * 5)
            self.ca.send_pgn(0, 0xFE, 0xF1, 6, list(ccvs))
        if self._i % 10 == 0:  # DM1 every second: one active fault
            dm1 = encode_dm1(
                [Dtc(spn=110, fmi=3, occurrence=self._i // 10 % 127, conversion_method=0)], awl=1
            )
            self.ca.send_pgn(0, 0xFE, 0xCA, 6, list(dm1))

    def _on_request(self, src_address: int, dest_address: int, pgn: int) -> None:
        if pgn == 65259:  # ComponentID: make*model*serial*unit* -> BAM multi-packet
            payload = b"PYCANGUI*DEMO ENGINE*SN0001*UNIT1*"
            self.ca.send_pgn(0, 0xFE, 0xEB, 6, list(payload))

    def stop(self) -> None:
        self._timer.stop()
        try:
            self.ca.stop()
        except Exception:
            pass
        for listener in self._listeners:
            if listener in self._notifier.listeners:
                self._notifier.remove_listener(listener)
        self.ecu.stop()
