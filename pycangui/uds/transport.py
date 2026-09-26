# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""ISO-TP transport interface and the built-in implementation.

UDS needs one thing from the link: send a whole message, receive a whole
message. That is the seam, so an ISO-TP implementation in C or Rust (via
ctypes) can replace ``can-isotp`` by registering a component of kind ``"isotp"``:

    from pycangui.core.components import register_component
    from pycangui.uds.transport import IsoTpTransport

    @register_component("isotp", "c-lib", "ISO-TP from my C library")
    class MyIsoTp(IsoTpTransport):
        def open(self): ...
        def send(self, payload): ...
        def recv(self, timeout): ...

``send`` / ``recv`` are called from a worker thread and may block.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import can
import isotp

from pycangui.core.bus import BusManager
from pycangui.core.components import register_component
from pycangui.uds import UdsConfig


class FrameRefusedError(Exception):
    """The adapter would not put a frame of the request on the bus."""


class IsoTpTransport(ABC):
    """Whole-message transport under UDS."""

    def __init__(self, bus: BusManager, config: UdsConfig, ctx=None) -> None:
        self.bus = bus
        self.config = config

    @abstractmethod
    def open(self) -> None:
        """Start the transport (register filters, start timers, ...)."""

    @abstractmethod
    def close(self) -> None:
        """Stop the transport."""

    @abstractmethod
    def send(self, payload: bytes) -> None:
        """Send one complete UDS message (segmenting as needed)."""

    @abstractmethod
    def recv(self, timeout: float) -> bytes | None:
        """Return the next complete message, or None on timeout."""

    @property
    def empty(self) -> bool:
        """True when nothing is waiting to be read (used to flush stale data)."""
        return True


@register_component("isotp", "can-isotp", "ISO 15765-2 from the can-isotp package")
class CanIsoTpTransport(IsoTpTransport):
    def __init__(self, bus: BusManager, config: UdsConfig, ctx=None) -> None:
        super().__init__(bus, config, ctx)
        mode = (
            isotp.AddressingMode.Normal_29bits
            if config.extended_id
            else isotp.AddressingMode.Normal_11bits
        )
        address = isotp.Address(mode, txid=config.tx_id, rxid=config.rx_id)
        self.stack = isotp.NotifierBasedCanStack(
            bus.bus,
            bus.notifier,
            address=address,
            params={
                "tx_padding": config.padding,
                "rx_flowcontrol_timeout": 1000,
                "rx_consecutive_frame_timeout": 1000,
                "can_fd": config.can_fd,
                # CAN_DL: 8 on a classic bus, and one of the FD lengths -- 12,
                # 16, 20, 24, 32, 48, 64 -- on one that opened as FD. A long
                # frame is the whole point of running UDS over FD: 64 bytes a
                # frame is eight times fewer flow control rounds.
                "tx_data_length": config.tx_data_length if config.can_fd else 8,
                "bitrate_switch": config.bitrate_switch and config.can_fd,
            },
        )
        # can-isotp sends frames from a thread of its own. An adapter that
        # refuses one -- its transmit queue full, because nothing on the bus
        # is acknowledging -- raised there, which ended the thread and UDS
        # with it until the channel was reconnected. The frame is dropped
        # instead, and the reason goes to the request waiting for an answer,
        # which would otherwise say only that none came.
        self._refused: can.CanError | None = None
        send_frame = self.stack.txfn

        def txfn(msg) -> None:
            try:
                send_frame(msg)
            except can.CanError as exc:
                self._refused = exc

        self.stack.txfn = txfn

    def open(self) -> None:
        if not self.stack.started:
            self.stack.start()

    def close(self) -> None:
        if self.stack.started:
            self.stack.stop()

    def send(self, payload: bytes) -> None:
        self._refused = None  # an earlier request's, and nothing to do with this one
        self.stack.send(payload)

    def recv(self, timeout: float) -> bytes | None:
        data = self.stack.recv(block=True, timeout=timeout)
        if data is None and (refused := self._refused) is not None:
            self._refused = None
            raise FrameRefusedError(f"the adapter would not send the request: {refused}")
        return None if data is None else bytes(data)

    @property
    def empty(self) -> bool:
        return not self.stack.available()
