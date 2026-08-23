"""A simulated CANopen device on the virtual bus, so everything can be tried
without hardware.  python-can's ``virtual`` interface only connects buses in
the same process, which is why this lives in-app.

It also answers UDS on 0x7E0/0x7E8 (see demo_uds.py).  It is a real
``canopen.LocalNode`` built from ``resources/demo.eds``: it
answers SDO reads/writes, sends a heartbeat, obeys NMT commands and transmits
TPDO1 every 100 ms with a moving "motor speed" that follows whatever you write
to *Speed demand* (0x2001).
"""

from __future__ import annotations

import struct

import can
import canopen
from PySide6.QtCore import QObject, QTimer

from pycangui import resources
from pycangui.core.demo_uds import DemoUdsServer

NODE_ID = 5


class DemoDevice(QObject):
    def __init__(self, channel: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._bus = can.Bus(interface="virtual", channel=channel)
        self._network = canopen.Network(bus=self._bus)
        self._network.notifier = can.Notifier(self._bus, self._network.listeners, 0.01)
        self.node = self._network.create_node(NODE_ID, str(resources.path("demo.eds")))
        self.node.nmt.state = "PRE-OPERATIONAL"
        self.node.nmt.start_heartbeat(500)
        self._tpdo = self.node.tpdo[1]
        self._tpdo.read(from_od=True)
        self._tpdo.start(0.1)
        self._speed = 0
        self._odometer = 0
        self._timer = QTimer(self, interval=100, timeout=self._tick)
        self._timer.start()
        self.uds = DemoUdsServer(self._bus, self._network.notifier, self)

    def _tick(self) -> None:
        demand = struct.unpack("<h", self.node.get_data(0x2001, 0))[0]
        self._speed += max(-50, min(50, demand - self._speed))  # slew towards demand
        self._odometer += abs(self._speed)
        self.node.set_data(0x2000, 1, struct.pack("<h", self._speed))
        self.node.set_data(0x2000, 2, struct.pack("<I", self._odometer))
        self._tpdo["Statusword"].raw = 0x0237 if self._speed else 0x0233
        self._tpdo["Measurements.Motor speed"].raw = self._speed
        self._tpdo["Measurements.Odometer"].raw = self._odometer
        if self.node.nmt.state == "OPERATIONAL":
            self._tpdo.update()

    def stop(self) -> None:
        self._timer.stop()
        self.uds.stop()
        self._tpdo.stop()
        self.node.nmt.stop_heartbeat()
        self._network.notifier.stop()
        self._bus.shutdown()
