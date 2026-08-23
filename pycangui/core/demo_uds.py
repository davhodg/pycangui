"""A tiny simulated UDS ECU for the demo device (0x7E0 / 0x7E8 on the virtual
bus).  Enough of ISO 14229 to exercise the UDS pane: sessions, security with a
byte-invert key, tester present, a few DIDs, DTCs with clear, a routine, ECU
reset, and the usual negative responses."""

from __future__ import annotations

import can
import isotp
from PySide6.QtCore import QObject, QTimer

NRC_SUBFUNC_NOT_SUPPORTED = 0x12
NRC_INCORRECT_LENGTH = 0x13
NRC_CONDITIONS_NOT_CORRECT = 0x22
NRC_REQUEST_OUT_OF_RANGE = 0x31
NRC_SECURITY_ACCESS_DENIED = 0x33
NRC_INVALID_KEY = 0x35
NRC_SERVICE_NOT_SUPPORTED = 0x11
NRC_SERVICE_NOT_SUPPORTED_IN_SESSION = 0x7F


class DemoUdsServer(QObject):
    def __init__(
        self, bus: can.BusABC, notifier: can.Notifier, parent: QObject | None = None
    ) -> None:
        super().__init__(parent)
        address = isotp.Address(isotp.AddressingMode.Normal_11bits, txid=0x7E8, rxid=0x7E0)
        self._stack = isotp.NotifierBasedCanStack(
            bus, notifier, address=address, params={"tx_padding": 0xAA}
        )
        self._stack.start()
        self.session = 1
        self.unlocked = False
        self._seed: bytes | None = None
        self.dids: dict[int, bytes] = {
            0xF190: b"PYCANGUI0DEMO0001",  # VIN
            0xF187: b"DEMO-ECU-1",  # spare part number
            0xF195: b"1.2.3",  # software version
            0x0101: (132).to_bytes(2, "big"),  # battery voltage 0.1 V -> 13.2 V
            0x0102: (0).to_bytes(2, "big"),  # writable setpoint
        }
        self.dtcs: dict[int, int] = {0x012345: 0x09, 0x9A0100: 0x2F}  # dtc -> status
        self.routine_running = False
        self._timer = QTimer(self, interval=5, timeout=self._poll)
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        self._stack.stop()

    # --- dispatch ------------------------------------------------------------
    def _poll(self) -> None:
        while (req := self._stack.recv()) is not None:
            resp = self.handle(bytes(req))
            if resp:
                self._stack.send(resp)

    def handle(self, req: bytes) -> bytes:
        sid = req[0]
        handler = getattr(self, f"_sid_{sid:02x}", None)
        if handler is None:
            return nrc(sid, NRC_SERVICE_NOT_SUPPORTED)
        return handler(req)

    # --- services ------------------------------------------------------------
    def _sid_10(self, req: bytes) -> bytes:  # DiagnosticSessionControl
        if len(req) != 2:
            return nrc(0x10, NRC_INCORRECT_LENGTH)
        sub = req[1] & 0x7F
        if sub not in (1, 2, 3):
            return nrc(0x10, NRC_SUBFUNC_NOT_SUPPORTED)
        self.session = sub
        if sub == 1:
            self.unlocked = False
        return bytes([0x50, sub]) + (50).to_bytes(2, "big") + (500).to_bytes(2, "big")

    def _sid_3e(self, req: bytes) -> bytes:  # TesterPresent
        return b"" if req[1] & 0x80 else bytes([0x7E, 0x00])

    def _sid_11(self, req: bytes) -> bytes:  # ECUReset
        if self.session == 1:
            return nrc(0x11, NRC_SERVICE_NOT_SUPPORTED_IN_SESSION)
        self.session = 1
        self.unlocked = False
        return bytes([0x51, req[1] & 0x7F])

    def _sid_27(self, req: bytes) -> bytes:  # SecurityAccess
        if self.session == 1:
            return nrc(0x27, NRC_SERVICE_NOT_SUPPORTED_IN_SESSION)
        sub = req[1]
        if sub == 1:  # request seed
            self._seed = bytes([0x12, 0x34, 0x56, 0x78]) if not self.unlocked else bytes(4)
            return bytes([0x67, 1]) + self._seed
        if sub == 2:  # send key
            if self._seed is None:
                return nrc(0x27, NRC_CONDITIONS_NOT_CORRECT)
            expected = bytes(b ^ 0xFF for b in self._seed)
            if req[2:] != expected:
                self._seed = None
                return nrc(0x27, NRC_INVALID_KEY)
            self.unlocked = True
            return bytes([0x67, 2])
        return nrc(0x27, NRC_SUBFUNC_NOT_SUPPORTED)

    def _sid_22(self, req: bytes) -> bytes:  # ReadDataByIdentifier
        if len(req) != 3:
            return nrc(0x22, NRC_INCORRECT_LENGTH)
        did = int.from_bytes(req[1:3], "big")
        if did not in self.dids:
            return nrc(0x22, NRC_REQUEST_OUT_OF_RANGE)
        return bytes([0x62]) + req[1:3] + self.dids[did]

    def _sid_2e(self, req: bytes) -> bytes:  # WriteDataByIdentifier
        did = int.from_bytes(req[1:3], "big")
        if did != 0x0102:
            return nrc(0x2E, NRC_REQUEST_OUT_OF_RANGE)
        if not self.unlocked:
            return nrc(0x2E, NRC_SECURITY_ACCESS_DENIED)
        self.dids[did] = bytes(req[3:])
        return bytes([0x6E]) + req[1:3]

    def _sid_19(self, req: bytes) -> bytes:  # ReadDTCInformation
        if req[1] != 0x02:
            return nrc(0x19, NRC_SUBFUNC_NOT_SUPPORTED)
        mask = req[2]
        out = bytes([0x59, 0x02, 0xFF])  # availability mask
        for dtc, status in self.dtcs.items():
            if status & mask:
                out += dtc.to_bytes(3, "big") + bytes([status])
        return out

    def _sid_14(self, req: bytes) -> bytes:  # ClearDiagnosticInformation
        self.dtcs.clear()
        return bytes([0x54])

    def _sid_31(self, req: bytes) -> bytes:  # RoutineControl
        sub = req[1]
        rid = int.from_bytes(req[2:4], "big")
        if rid != 0x0203:
            return nrc(0x31, NRC_REQUEST_OUT_OF_RANGE)
        if sub == 1:
            self.routine_running = True
            return bytes([0x71, 1]) + req[2:4]
        if sub == 2:
            self.routine_running = False
            return bytes([0x71, 2]) + req[2:4]
        if sub == 3:
            return bytes([0x71, 3]) + req[2:4] + (b"\x01" if self.routine_running else b"\x00")
        return nrc(0x31, NRC_SUBFUNC_NOT_SUPPORTED)


def nrc(sid: int, code: int) -> bytes:
    return bytes([0x7F, sid, code])
