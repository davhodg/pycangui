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

import isotp

from pycangui.core.bus import BusManager
from pycangui.core.components import register_component
from pycangui.uds import UdsConfig


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

    def open(self) -> None:
        if not self.stack.started:
            self.stack.start()

    def close(self) -> None:
        if self.stack.started:
            self.stack.stop()

    def send(self, payload: bytes) -> None:
        self.stack.send(payload)

    def recv(self, timeout: float) -> bytes | None:
        data = self.stack.recv(block=True, timeout=timeout)
        return None if data is None else bytes(data)

    @property
    def empty(self) -> bool:
        return not self.stack.available()
