# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""CCP over CAN, as an engine the calibration pane can select.

It implements the same interface as the XCP engine -- connect, seed and key,
read an address, write an address -- so everything above it is unchanged: the
A2L, the conversions, the polling, the plot. That interface was written for a
different implementation of XCP; that CCP fits it without alteration is a
better test of it than another XCP engine would have been.

What the pane has to know about, because CCP asks for it and XCP does not, is
the station address: one number, in one box, and nothing else differs on
screen.
"""

from __future__ import annotations

import queue
import struct

from pycangui.ccp import (
    ACKNOWLEDGE,
    CMD_CONNECT,
    CMD_DISCONNECT,
    CMD_DNLOAD,
    CMD_GET_SEED,
    CMD_SET_MTA,
    CMD_SHORT_UP,
    CMD_UNLOCK,
    CMD_UPLOAD,
    COUNTER_WRAP,
    MOST_PER_FRAME,
    PID_RETURN,
    RETURN_CODES,
)
from pycangui.core.backends import register_backend
from pycangui.core.bus import BusManager, Frame
from pycangui.xcp import ConnectInfo
from pycangui.xcp.engine import NO_ID, XcpEngine


class CcpError(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(RETURN_CODES.get(code, f"0x{code:02X}"))
        self.code = code


@register_backend("xcp", "ccp-builtin", "CCP on CAN, implemented in pycangui")
class CanCcpEngine(XcpEngine):
    """CCP on CAN over the shared python-can bus.

    ``XcpEngine`` is the interface the pane speaks, not a statement about the
    protocol: the pane asks for an address and gets bytes back, and which of
    the two protocols carried them is this class's business.
    """

    protocol = "CCP"
    #: The pane shows a station address box for an engine that has this.
    needs_station = True

    def __init__(self, bus: BusManager, ctx=None) -> None:
        self._bus = bus
        self._responses: queue.Queue = queue.Queue()
        self.cmd_id = NO_ID
        self.res_id = NO_ID
        self.extended = False
        self.station = 0
        #: Sent with every command and echoed in the answer. Not checked
        #: against the answer: slaves in the field are careless with it, and
        #: refusing a good answer over a counter would be pedantry that costs
        #: somebody an afternoon.
        self._counter = 0
        self.info: ConnectInfo | None = None
        bus.frames.connect(self._on_frames)

    # --- plumbing -----------------------------------------------------------
    def _on_frames(self, frames: list[Frame]) -> None:
        for f in frames:
            if f.rx and f.can_id == self.res_id and f.extended == self.extended:
                self._responses.put(f.data)

    def set_ids(self, cmd_id: int, res_id: int, extended: bool) -> None:
        self.cmd_id, self.res_id, self.extended = cmd_id, res_id, extended

    def set_station(self, station: int) -> None:
        """Which controller on these identifiers is being talked to."""
        self.station = station

    def owns_frame(self, frame: Frame) -> str | None:
        if self.cmd_id == NO_ID or frame.extended != self.extended:
            return None
        if frame.can_id == self.cmd_id:
            return "CCP cmd"
        if frame.can_id == self.res_id:
            return "CCP resp"
        return None

    def command(self, code: int, payload: bytes = b"", timeout: float = 1.0) -> bytes:
        """Send one command and return the five data bytes of its answer."""
        if self.cmd_id == NO_ID:
            raise CcpError(0x36)  # nothing has said where the slave is
        while not self._responses.empty():  # drop stale answers
            self._responses.get_nowait()
        self._counter = (self._counter + 1) % COUNTER_WRAP
        frame = bytes([code, self._counter]) + payload
        self._bus.send(self.cmd_id, frame.ljust(8, b"\x00"), extended=self.extended)
        try:
            data = self._responses.get(timeout=timeout)
        except queue.Empty as exc:
            raise TimeoutError from exc
        if len(data) < 3 or data[0] != PID_RETURN:
            raise CcpError(0x31)  # not a command return message at all
        if data[1] != ACKNOWLEDGE:
            raise CcpError(data[1])
        return bytes(data[3:])

    # --- what the pane asks for ---------------------------------------------
    def connect(self) -> ConnectInfo:
        # The station address goes out little-endian: the one place in CCP
        # that is not Motorola order, and the standard says so explicitly.
        self.command(CMD_CONNECT, struct.pack("<H", self.station))
        # CCP reports no resources and no byte order, so there is nothing to
        # read them out of: big-endian is the protocol's own order, and what
        # is protected is discovered by being refused.
        self.info = ConnectInfo(resources=0, big_endian=True, max_cto=8, max_dto=8)
        return self.info

    def disconnect(self) -> None:
        # Temporary rather than end of session: the pane's Disconnect means
        # "stop talking to it", and ending the session drops the calibration
        # unlock, which somebody reconnecting has to do again for no reason.
        self.command(CMD_DISCONNECT, bytes([0x00, 0x00]) + struct.pack("<H", self.station))
        self.info = None

    def get_seed(self, resource: int) -> bytes:
        answer = self.command(CMD_GET_SEED, bytes([resource]))
        # The first byte says whether that resource is protected at all; the
        # rest is the seed.
        return answer[1:]

    def unlock(self, key: bytes) -> None:
        self.command(CMD_UNLOCK, key[:MOST_PER_FRAME])

    def read(self, address: int, size: int) -> bytes:
        if size <= MOST_PER_FRAME:
            # SHORT_UP carries the address with it, so one frame does it.
            return self.command(CMD_SHORT_UP, bytes([size, 0]) + struct.pack(">I", address))[:size]
        self._set_mta(address)
        out = b""
        while len(out) < size:
            want = min(MOST_PER_FRAME, size - len(out))
            out += self.command(CMD_UPLOAD, bytes([want]))[:want]
        return out

    def write(self, address: int, data: bytes) -> None:
        self._set_mta(address)
        for start in range(0, len(data), MOST_PER_FRAME):
            chunk = data[start : start + MOST_PER_FRAME]
            self.command(CMD_DNLOAD, bytes([len(chunk)]) + chunk)

    def _set_mta(self, address: int) -> None:
        """Point the slave's transfer address at somewhere, MTA 0 as usual."""
        self.command(CMD_SET_MTA, bytes([0, 0]) + struct.pack(">I", address))

    def close(self) -> None:
        try:
            self._bus.frames.disconnect(self._on_frames)
        except (RuntimeError, TypeError):  # already disconnected, or gone
            pass
