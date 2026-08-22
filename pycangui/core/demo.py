"""Demo traffic generator for the virtual bus (python-can's ``virtual``
interface only connects buses in the same process, so this has to live in-app).

A ``QTimer`` drives it from the GUI thread: 5 ms period is plenty to make the
trace feel alive without needing another thread.
"""

from __future__ import annotations

import struct

import can
from PySide6.QtCore import QObject, QTimer

NODE = 0x05


class DemoTraffic(QObject):
    def __init__(self, channel: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._bus = can.Bus(interface="virtual", channel=channel)
        self._i = 0
        self._timer = QTimer(self, interval=5, timeout=self._tick)
        self._timer.start()

    def _tick(self) -> None:
        i = self._i
        self._i += 1
        if i % 100 == 0:  # heartbeat: operational
            self._send(0x700 + NODE, bytes([0x05]))
        self._send(0x180 + NODE, struct.pack("<hhI", i % 3000, (i * 7) % 1000, i))  # TPDO1

    def _send(self, can_id: int, data: bytes) -> None:
        self._bus.send(can.Message(arbitration_id=can_id, data=data, is_extended_id=False))

    def stop(self) -> None:
        self._timer.stop()
        self._bus.shutdown()
