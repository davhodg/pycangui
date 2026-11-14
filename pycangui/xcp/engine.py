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
import time
from abc import ABC, abstractmethod
from dataclasses import replace

import can
from PySide6.QtCore import QObject

from pycangui.core.bus import BusManager, Frame
from pycangui.core.components import register_component
from pycangui.xcp import (
    CMD_BUILD_CHECKSUM,
    CMD_CONNECT,
    CMD_DISCONNECT,
    CMD_DOWNLOAD,
    CMD_GET_ID,
    CMD_GET_SEED,
    CMD_GET_STATUS,
    CMD_PROGRAM,
    CMD_PROGRAM_CLEAR,
    CMD_PROGRAM_RESET,
    CMD_PROGRAM_START,
    CMD_SET_MTA,
    CMD_SHORT_UPLOAD,
    CMD_SYNCH,
    CMD_UNLOCK,
    CMD_UPLOAD,
    ERR_CMD_BUSY,
    ERROR_CODES,
    EV_CMD_PENDING,
    PID_ERR,
    PID_EV,
    PID_RES,
    RESOURCE_PGM,
    ConnectInfo,
)

#: How long a classic CAN frame is: what a padded command is filled out to.
CAN_FRAME = 8
#: CONNECT's address granularity bits, as bytes in one element.
GRANULARITIES = {0: 1, 1: 2, 2: 4}


def _name(pid: int) -> str:
    return _NAMES.get(pid, f"command 0x{pid:02X}")


class XcpError(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(ERROR_CODES.get(code, f"0x{code:02X}"))
        self.code = code


#: Stands for "nobody has said yet", and cannot collide with a real
#: identifier because no CAN id is negative.
NO_ID = -1

#: How long a command that erases or sums a whole area may take before the
#: slave is given up on. Erasing flash is seconds, not milliseconds, and a
#: slave that says so with EV_CMD_PENDING is waited for again each time.
LONG_TIMEOUT = 30.0

#: XCP's timeouts, in seconds, until an A2L's PROTOCOL_LAYER gives its own:
#: T1 for most commands, T2 for BUILD_CHECKSUM, T3 for PROGRAM_START, T4 for
#: PROGRAM_CLEAR, T5 for PROGRAM and PROGRAM_RESET, T6 for CONNECT, and T7
#: how long to wait before asking a busy slave again.
DEFAULT_TIMEOUTS = {
    "T1": 1.0,
    "T2": LONG_TIMEOUT,
    "T3": LONG_TIMEOUT,
    "T4": LONG_TIMEOUT,
    "T5": LONG_TIMEOUT,
    "T6": 1.0,
    "T7": 0.2,
}
#: Which timeout each command waits for, where it is not T1.
TIMEOUT_OF = {
    CMD_BUILD_CHECKSUM: "T2",
    CMD_PROGRAM_START: "T3",
    CMD_PROGRAM_CLEAR: "T4",
    CMD_PROGRAM: "T5",
    CMD_PROGRAM_RESET: "T5",
    CMD_CONNECT: "T6",
}
#: How many times a command whose answer did not come is sent again, after a
#: SYNCH, as the standard has it.
REPEATS = 2
#: How many times a slave that says ERR_CMD_BUSY is asked again, T7 apart,
#: before it is taken to be stuck.
BUSY_REPEATS = 10
#: The commands sent again when their answer does not come: those it does
#: no harm to do twice, given the MTA put back first where they move it.
#: Not the seed and key, which start again from GET_SEED; not CONNECT,
#: which is how talking starts; not PROGRAM_RESET, which a slave that
#: resets at once never answers; and not GET_ID, which a slave without it
#: may simply not answer, and connecting should not wait three times for.
REPEATED = {
    CMD_DISCONNECT,
    CMD_GET_STATUS,
    CMD_SET_MTA,
    CMD_UPLOAD,
    CMD_SHORT_UPLOAD,
    CMD_BUILD_CHECKSUM,
    CMD_DOWNLOAD,
    CMD_PROGRAM_START,
    CMD_PROGRAM_CLEAR,
    CMD_PROGRAM,
}
#: Commands by number, for saying which one went unanswered.
_NAMES = {
    CMD_CONNECT: "CONNECT",
    CMD_DISCONNECT: "DISCONNECT",
    CMD_GET_STATUS: "GET_STATUS",
    CMD_GET_ID: "GET_ID",
    CMD_SET_MTA: "SET_MTA",
    CMD_UPLOAD: "UPLOAD",
    CMD_SHORT_UPLOAD: "SHORT_UPLOAD",
    CMD_BUILD_CHECKSUM: "BUILD_CHECKSUM",
    CMD_DOWNLOAD: "DOWNLOAD",
    CMD_PROGRAM_START: "PROGRAM_START",
    CMD_PROGRAM_CLEAR: "PROGRAM_CLEAR",
    CMD_PROGRAM: "PROGRAM",
    CMD_PROGRAM_RESET: "PROGRAM_RESET",
}
#: GET_ID's request types: text, and the A2L's name without its path.
ID_TEXT = 0
ID_A2L_NAME = 1


class UnsupportedError(Exception):
    """This engine has no way to do what was asked: an engine written before
    the Transfer tab existed, or a protocol without the command."""


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

    #: The resource that unlocks flashing, as this protocol numbers it.
    pgm_resource = RESOURCE_PGM
    #: Whether program_reset() means anything: CCP has no such command.
    can_reset = False
    #: Whether every command goes out as a full 8-byte frame. An A2L's
    #: MAX_DLC_REQUIRED says the slave ignores a shorter one.
    pad = False
    #: Told, in words, when a command is sent again because its answer did
    #: not come or the slave was busy. None to say nothing.
    on_recover = None

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
    def read(self, address: int, size: int, extension: int = 0) -> bytes:
        """Read *size* bytes from *address* (SHORT_UPLOAD or equivalent).

        *extension* is the address extension, which of the slave's address
        spaces is meant. It is only passed when it is not 0, so an engine
        written before it existed goes on working for every slave that has
        one address space, which is most of them.
        """

    @abstractmethod
    def write(self, address: int, data: bytes, extension: int = 0) -> None:
        """Write *data* at *address* (SET_MTA + DOWNLOAD or equivalent)."""

    # --- flashing: optional, for the Transfer tab ------------------------------------
    # Not abstract, so an engine written before them goes on loading: what it
    # cannot do is said when it is asked, not by refusing to start.
    def program_start(self) -> None:
        """Begin a programming session (XCP PROGRAM_START). Optional."""
        return None

    def program_clear(self, address: int, size: int, extension: int = 0) -> None:
        """Erase *size* bytes from *address*, before they are programmed."""
        raise UnsupportedError(f"this {self.protocol} engine cannot erase memory")

    def program(self, address: int, data: bytes, extension: int = 0) -> None:
        """Program *data* at *address* (XCP PROGRAM, CCP PROGRAM)."""
        raise UnsupportedError(f"this {self.protocol} engine cannot program memory")

    def program_end(self) -> None:
        """Say that one memory segment is finished. Optional."""
        return None

    def program_reset(self) -> None:
        """End the programming session and reset the slave."""
        raise UnsupportedError(f"{self.protocol} has no reset after programming")

    def get_id(self, kind: int = ID_A2L_NAME) -> str:
        """GET_ID: what the slave calls itself, or its A2L. Optional."""
        raise UnsupportedError(f"{self.protocol} has no GET_ID")

    def checksum(self, address: int, size: int, extension: int = 0) -> tuple[int | None, int, int]:
        """BUILD_CHECKSUM over *size* bytes from *address*.

        Returns which algorithm the slave used (an XCP checksum type, or None
        where the protocol does not say), the checksum, and its size in bytes.
        """
        raise UnsupportedError(f"this {self.protocol} engine cannot build a checksum")

    def owns_frame(self, frame: Frame) -> str | None:
        """Optional: label a frame for the trace ("XCP cmd" / "XCP resp")."""
        return None

    def close(self) -> None:
        """Release any resources (called when the pane or app shuts down)."""
        return None


class AnswerTap(can.Listener):
    """Hands an engine its slave's answers on the thread that reads the bus.

    They used to come round by the window: collected by the reader, handed
    over every few milliseconds on the window's thread, and only then put
    where the command was waiting. That is a second's timeout resting on the
    window not being busy for a second -- redrawing a long trace, loading a
    large A2L -- when the answer had been in for most of it. CANopen and UDS
    have always listened here; now these do.

    ``engine`` is anything with ``res_id``, ``extended`` and a ``_responses``
    queue. The listener follows the bus through a disconnect, a reconnect and
    a change of channel, since each of those is a new reader.
    """

    def __init__(self, bus, engine) -> None:
        self._bus = bus
        self._engine = engine
        # Kept to disconnect by: this is not a QObject, and a signal cannot
        # be disconnected from a plain method by naming the method again.
        self._links = [
            bus.connected.connect(self._attach),
            bus.disconnected.connect(self._detach),
        ]
        self._attach()

    def on_message_received(self, msg: can.Message) -> None:
        engine = self._engine
        if (
            msg.is_rx
            and not msg.is_error_frame
            and msg.arbitration_id == engine.res_id
            and bool(msg.is_extended_id) == engine.extended
        ):
            engine._responses.put(bytes(msg.data))

    def on_error(self, exc: Exception) -> None:
        pass  # the bus reports its own

    def _attach(self, *_said) -> None:
        self._detach()  # never twice on one reader
        self._bus.add_listener(self)

    def _detach(self) -> None:
        self._bus.remove_listener(self)

    def close(self) -> None:
        for link in self._links:
            QObject.disconnect(link)
        self._links = []
        self._detach()


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
        #: The largest command packet while programming, from PROGRAM_START.
        self._max_cto_pgm = 8
        #: T1 to T7, in seconds: the A2L's PROTOCOL_LAYER, where the manager has one.
        self.timeouts = dict(DEFAULT_TIMEOUTS)
        self._answers = AnswerTap(bus, self)

    can_reset = True

    # --- plumbing -----------------------------------------------------------
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

    def command(
        self,
        pid: int,
        payload: bytes = b"",
        timeout: float | None = None,
        mta: tuple[int, int] | None = None,
        repeat: bool = True,
    ) -> bytes:
        """Send a command and wait for its answer, recovering as XCP says to.

        An answer that does not come in time is followed by a SYNCH, which puts
        the slave back to waiting for a command, and the command is sent
        again, up to REPEATS times -- with the MTA put back to ``mta`` first,
        where the command moves it, so that a lost answer does not make a read
        or a write skip or repeat bytes. A slave that says it is busy is asked
        again after T7. ``repeat`` False sends it once, as a command whose MTA
        is not known has to be.
        """
        if self.cmd_id == NO_ID:
            raise XcpError(0x31)  # nothing has said where the slave is
        wait = timeout if timeout is not None else self.timeouts[TIMEOUT_OF.get(pid, "T1")]
        lost = busy = 0
        while True:
            try:
                return self._exchange(pid, payload, wait)
            except TimeoutError:
                if not repeat or pid not in REPEATED or lost >= REPEATS:
                    raise
                lost += 1
                self._say(f"no answer to {_name(pid)}: SYNCH, and again ({lost} of {REPEATS})")
                self._synch()
                if mta is not None:
                    self._exchange(CMD_SET_MTA, self._mta_payload(*mta), self.timeouts["T1"])
            except XcpError as exc:
                if exc.code != ERR_CMD_BUSY or busy >= BUSY_REPEATS:
                    raise
                busy += 1
                time.sleep(self.timeouts["T7"])

    def _say(self, text: str) -> None:
        if self.on_recover is not None:
            self.on_recover(text)

    def _synch(self) -> None:
        """SYNCH: the slave stops whatever it was in the middle of. It answers
        with an error, ERR_CMD_SYNCH, which is how it says it worked."""
        try:
            self._exchange(CMD_SYNCH, b"", self.timeouts["T1"])
        except (XcpError, TimeoutError):
            pass

    def _exchange(self, pid: int, payload: bytes, timeout: float) -> bytes:
        """One command and its answer, nothing more."""
        while not self._responses.empty():  # drop stale responses
            self._responses.get_nowait()
        frame = bytes([pid]) + payload
        if self.pad:
            frame = frame.ljust(CAN_FRAME, b"\x00")
        self._bus.send(self.cmd_id, frame, extended=self.extended)
        while True:
            try:
                data = self._responses.get(timeout=timeout)
            except queue.Empty as exc:
                raise TimeoutError from exc
            if not data:
                raise TimeoutError
            # A slave busy erasing says so, and is waited for again.
            if not (data[0] == PID_EV and len(data) > 1 and data[1] == EV_CMD_PENDING):
                break
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
        # Bits 1 and 2: the address granularity, as BYTE, WORD or DWORD.
        granularity = GRANULARITIES.get((r[1] >> 1) & 0x03, 1)
        max_dto = struct.unpack(">H" if big_endian else "<H", r[3:5])[0]
        self.info = ConnectInfo(r[0], 0, big_endian, r[2], max_dto, granularity)
        # What is still locked. GET_STATUS is one every slave must answer, but
        # a slave that does not is still worth talking to.
        try:
            status = self.command(CMD_GET_STATUS)
        except (XcpError, TimeoutError):
            return self.info
        if len(status) > 1:
            self.info = replace(self.info, protection=status[1])
        return self.info

    def disconnect(self) -> None:
        try:
            self.command(CMD_DISCONNECT)
        finally:
            self.info = None

    def get_seed(self, resource: int) -> bytes:
        # The first answer says how long the whole seed is; one longer than a
        # frame holds comes in pieces, each asked for with mode 1, and each
        # saying how much is still to come.
        answer = self.command(CMD_GET_SEED, bytes([0x00, resource]))
        total = answer[0]
        seed = bytearray(answer[1 : 1 + total])
        while len(seed) < total:
            answer = self.command(CMD_GET_SEED, bytes([0x01, resource]))
            left = answer[0]
            if not left:
                break
            seed += answer[1 : 1 + left]
        return bytes(seed[:total])

    def unlock(self, key: bytes) -> None:
        # Likewise in pieces, each saying how much of the key is left,
        # counting itself.
        most = self._max_cto - 2  # the PID and the length
        at = 0
        while True:
            piece = key[at : at + most]
            answer = self.command(CMD_UNLOCK, bytes([len(key) - at, *piece]))
            at += len(piece)
            if at >= len(key):
                break
        if answer and self.info is not None:
            self.info = replace(self.info, protection=answer[0])

    @property
    def _max_cto(self) -> int:
        return self.info.max_cto if self.info and self.info.max_cto >= 8 else 8

    @property
    def _granularity(self) -> int:
        return self.info.granularity if self.info else 1

    def read(self, address: int, size: int, extension: int = 0) -> bytes:
        # Counted in elements, and an answer's elements start on an element's
        # boundary: after the PID come granularity - 1 bytes of padding.
        g = self._granularity
        elements = -(-size // g)
        most = (self._max_cto - g) // g
        if elements <= most:
            where = bytes([elements, 0x00, extension & 0xFF]) + self._pack_address(address)
            return self.command(CMD_SHORT_UPLOAD, where)[g - 1 : g - 1 + size]
        # More than one answer holds: point the MTA at it and UPLOAD a frame
        # at a time, which every slave can do, rather than in block mode,
        # which only some can.
        self._set_mta(address, extension)
        return self._upload(size, address, extension)

    def _upload(self, size: int, address: int | None = None, extension: int = 0) -> bytes:
        """UPLOAD from where the MTA is, a frame at a time. With ``address``, each
        piece is sent again from where it should start if its answer is lost;
        without it, where the slave put the MTA itself, it is sent once."""
        g = self._granularity
        elements = -(-size // g)
        most = (self._max_cto - g) // g
        out = bytearray()
        while len(out) < elements * g:
            want = min(most, elements - len(out) // g)
            at = (address + len(out), extension) if address is not None else None
            answer = self.command(CMD_UPLOAD, bytes([want]), mta=at, repeat=at is not None)
            out += answer[g - 1 : g - 1 + want * g]
        return bytes(out[:size])

    def _whole(self, data: bytes) -> int:
        """The elements ``data`` makes, refusing what is not a whole number."""
        g = self._granularity
        if len(data) % g:
            raise ValueError(
                f"{len(data)} bytes is not a whole number of the slave's "
                f"{g}-byte elements, so it cannot be written"
            )
        return len(data) // g

    def _send_elements(
        self, pid: int, data: bytes, max_cto: int, address: int, extension: int
    ) -> None:
        """DOWNLOAD or PROGRAM: the count in elements, the data on an element's
        boundary -- after two bytes of padding when elements are four bytes."""
        self._whole(data)
        g = self._granularity
        align = -2 % g
        most = (max_cto - 2 - align) // g * g
        for at in range(0, len(data), most):
            chunk = data[at : at + most]
            self.command(
                pid,
                bytes([len(chunk) // g]) + bytes(align) + chunk,
                mta=(address + at, extension),
            )

    def write(self, address: int, data: bytes, extension: int = 0) -> None:
        self._set_mta(address, extension)
        self._send_elements(CMD_DOWNLOAD, data, self._max_cto, address, extension)

    def _mta_payload(self, address: int, extension: int = 0) -> bytes:
        return bytes([0x00, 0x00, extension & 0xFF]) + self._pack_address(address)

    def _set_mta(self, address: int, extension: int = 0) -> None:
        self.command(CMD_SET_MTA, self._mta_payload(address, extension))

    def get_id(self, kind: int = ID_A2L_NAME) -> str:
        """GET_ID: the answer says how long the identification is and whether it
        is in the answer itself; on CAN it hardly ever fits, and is uploaded
        from where the slave has put the MTA."""
        r = self.command(CMD_GET_ID, bytes([kind]))
        order = ">I" if self._big_endian else "<I"
        length = struct.unpack(order, r[3:7])[0] if len(r) >= 7 else 0
        if not length:
            return ""
        data = r[7 : 7 + length] if r[0] & 0x01 else self._upload(length)
        return data.split(b"\x00", 1)[0].decode("latin-1")

    # --- flashing -------------------------------------------------------------
    def program_start(self) -> None:
        # reserved, COMM_MODE_PGM, MAX_CTO_PGM, MAX_BS_PGM, MIN_ST_PGM, QUEUE_SIZE_PGM
        r = self.command(CMD_PROGRAM_START)
        self._max_cto_pgm = r[2] if len(r) > 2 and r[2] >= 8 else self._max_cto

    def program_clear(self, address: int, size: int, extension: int = 0) -> None:
        self._set_mta(address, extension)
        # Mode 0, absolute: the range is a number of bytes from the MTA.
        payload = bytes([0x00, 0x00, 0x00]) + self._pack_address(size)
        self.command(CMD_PROGRAM_CLEAR, payload, mta=(address, extension))

    def program(self, address: int, data: bytes, extension: int = 0) -> None:
        self._set_mta(address, extension)
        self._send_elements(CMD_PROGRAM, data, self._max_cto_pgm, address, extension)

    def program_end(self) -> None:
        # A PROGRAM of nothing is how XCP says a memory segment is complete.
        self.command(CMD_PROGRAM, bytes([0x00]))

    def program_reset(self) -> None:
        try:
            self.command(CMD_PROGRAM_RESET)
        except TimeoutError:
            pass  # a slave that resets at once has nobody left to answer
        self.info = None

    def checksum(self, address: int, size: int, extension: int = 0) -> tuple[int | None, int, int]:
        self._set_mta(address, extension)
        # The block size is in elements.
        payload = bytes([0x00, 0x00, 0x00]) + self._pack_address(-(-size // self._granularity))
        r = self.command(CMD_BUILD_CHECKSUM, payload, mta=(address, extension))
        # The checksum type, two reserved bytes, then the checksum as a DWORD.
        order = ">I" if self._big_endian else "<I"
        return r[0], struct.unpack(order, r[3:7])[0], 4

    def close(self) -> None:
        self._answers.close()
