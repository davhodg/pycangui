# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""XCP engine interface and the built-in implementation.

An engine only has to move XCP commands and responses; everything above it
(A2L, conversions, polling, plotting, GUI) lives in ``XcpManager``. Replace it
by registering another component of kind ``"xcp"`` -- for example one calling
into a Rust or C library through ctypes.

All methods are called from a worker thread and may block. Raise ``XcpError``
for a slave-reported error code and ``TimeoutError`` if the slave says nothing.
"""

from __future__ import annotations

import queue
import struct
from abc import ABC, abstractmethod

from pycangui.core.bus import BusManager, Frame
from pycangui.core.components import register_component
from pycangui.xcp import (
    CMD_CONNECT,
    CMD_DISCONNECT,
    CMD_DOWNLOAD,
    CMD_GET_SEED,
    CMD_SET_MTA,
    CMD_SHORT_UPLOAD,
    CMD_UNLOCK,
    ERROR_CODES,
    PID_ERR,
    PID_RES,
    ConnectInfo,
)


class XcpError(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(ERROR_CODES.get(code, f"0x{code:02X}"))
        self.code = code


#: Stands for "nobody has said yet", and cannot collide with a real
#: identifier because no CAN id is negative.
NO_ID = -1


class XcpEngine(ABC):
    """What the calibration pane needs from an implementation.

    Named for XCP because that is what it was written for, and kept that
    way because CCP fits it unchanged -- which says more for the interface
    than renaming it would. What an engine speaks is its own business; the
    pane asks for an address and gets bytes back.
    """

    #: What to call this in the log, since a pane that says XCP while
    #: speaking CCP is a pane that will waste somebody's morning.
    protocol = "XCP"
    #: True for an engine that needs a station address as well as a pair of
    #: identifiers, which the pane then shows a box for.
    needs_station = False

    #: Filled in by connect(); the manager reads byte order and limits from it.
    info: ConnectInfo | None = None

    def set_ids(self, cmd_id: int, res_id: int, extended: bool) -> None:
        """Addressing for XCP on CAN. Engines on another link may ignore it."""
        return None

    @abstractmethod
    def connect(self) -> ConnectInfo:
        """CONNECT; return what the slave reports."""

    @abstractmethod
    def disconnect(self) -> None:
        """DISCONNECT (best effort)."""

    @abstractmethod
    def get_seed(self, resource: int) -> bytes:
        """GET_SEED for a resource."""

    @abstractmethod
    def unlock(self, key: bytes) -> None:
        """UNLOCK with the key computed from the seed."""

    @abstractmethod
    def read(self, address: int, size: int) -> bytes:
        """Read *size* bytes from *address* (SHORT_UPLOAD or equivalent)."""

    @abstractmethod
    def write(self, address: int, data: bytes) -> None:
        """Write *data* at *address* (SET_MTA + DOWNLOAD or equivalent)."""

    def owns_frame(self, frame: Frame) -> str | None:
        """Optional: label a frame for the trace ("XCP cmd" / "XCP resp")."""
        return None

    def close(self) -> None:
        """Release any resources (called when the pane or app shuts down)."""
        return None


@register_component("xcp", "xcp-builtin", "XCP on CAN, implemented in pycangui")
class NativeCanEngine(XcpEngine):
    """XCP on CAN over the shared python-can bus."""

    def __init__(self, bus: BusManager, ctx=None) -> None:
        self._bus = bus
        self._responses: queue.Queue = queue.Queue()
        # No identifiers until somebody gives them. XCP on CAN standardises
        # none, so a default pair would send commands to whatever happened
        # to answer on it, and would label frames in the trace as XCP that
        # have nothing to do with XCP.
        self.cmd_id = NO_ID
        self.res_id = NO_ID
        self.extended = False
        self.info: ConnectInfo | None = None
        bus.frames.connect(self._on_frames)

    # --- plumbing -----------------------------------------------------------
    def _on_frames(self, frames: list[Frame]) -> None:
        for f in frames:
            if f.rx and f.can_id == self.res_id and f.extended == self.extended:
                self._responses.put(f.data)

    def set_ids(self, cmd_id: int, res_id: int, extended: bool) -> None:
        self.cmd_id, self.res_id, self.extended = cmd_id, res_id, extended

    def owns_frame(self, frame: Frame) -> str | None:
        if self.cmd_id == NO_ID or frame.extended != self.extended:
            return None
        if frame.can_id == self.cmd_id:
            return "XCP cmd"
        if frame.can_id == self.res_id:
            return "XCP resp"
        return None

    def command(self, pid: int, payload: bytes = b"", timeout: float = 1.0) -> bytes:
        if self.cmd_id == NO_ID:
            raise XcpError(0x31)  # nothing has said where the slave is
        while not self._responses.empty():  # drop stale responses
            self._responses.get_nowait()
        self._bus.send(self.cmd_id, bytes([pid]) + payload, extended=self.extended)
        try:
            data = self._responses.get(timeout=timeout)
        except queue.Empty as exc:
            raise TimeoutError from exc
        if not data:
            raise TimeoutError
        if data[0] == PID_ERR:
            raise XcpError(data[1] if len(data) > 1 else 0x31)
        if data[0] != PID_RES:
            raise XcpError(0x31)
        return bytes(data[1:])

    @property
    def _big_endian(self) -> bool:
        return bool(self.info and self.info.big_endian)

    def _pack_address(self, address: int) -> bytes:
        return struct.pack(">I" if self._big_endian else "<I", address)

    # --- XcpEngine ------------------------------------------------------------
    def connect(self) -> ConnectInfo:
        r = self.command(CMD_CONNECT, bytes([0x00]))
        # resource, commModeBasic, maxCTO, maxDTO(2), protocol version, transport version
        big_endian = bool(r[1] & 0x01)
        max_dto = struct.unpack(">H" if big_endian else "<H", r[3:5])[0]
        self.info = ConnectInfo(r[0], 0, big_endian, r[2], max_dto)
        return self.info

    def disconnect(self) -> None:
        try:
            self.command(CMD_DISCONNECT)
        finally:
            self.info = None

    def get_seed(self, resource: int) -> bytes:
        seed = self.command(CMD_GET_SEED, bytes([0x00, resource]))
        return bytes(seed[1 : 1 + seed[0]])

    def unlock(self, key: bytes) -> None:
        self.command(CMD_UNLOCK, bytes([len(key), *key]))

    def read(self, address: int, size: int) -> bytes:
        return self.command(
            CMD_SHORT_UPLOAD, bytes([size, 0x00, 0x00]) + self._pack_address(address)
        )

    def write(self, address: int, data: bytes) -> None:
        self.command(CMD_SET_MTA, bytes([0x00, 0x00, 0x00]) + self._pack_address(address))
        self.command(CMD_DOWNLOAD, bytes([len(data)]) + data)

    def close(self) -> None:
        self._bus.frames.disconnect(self._on_frames)
