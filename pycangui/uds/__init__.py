# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""UDS (ISO 14229) diagnostics on ISO-TP, built on udsoncan + can-isotp."""

from __future__ import annotations

from dataclasses import dataclass

#: The frame lengths CAN FD has. There is nothing in between: a message
#: shorter than the next one up is padded to reach it.
CAN_DL = (8, 12, 16, 20, 24, 32, 48, 64)


@dataclass
class UdsConfig:
    """Addressing and timing for one ECU. Saved in settings "uds.config"."""

    tx_id: int = 0x7E0  # tester -> ECU
    rx_id: int = 0x7E8  # ECU -> tester
    functional_id: int = 0x7DF
    extended_id: bool = False  # 29-bit CAN ids (normal addressing)
    padding: int | None = 0xCC  # None = no padding
    #: ISO 15765-2 over CAN FD. CAN_DL is how many bytes go in one frame:
    #: 8 as it always was, or one of the FD lengths up to 64, which is what
    #: makes a transfer over FD worth having.
    can_fd: bool = False
    tx_data_length: int = 8
    #: Whether the data phase of an FD frame switches to the faster rate.
    #: Without it an FD frame runs end to end at the arbitration bitrate, so
    #: the data rate chosen on the toolbar never gets used.
    bitrate_switch: bool = False
    p2_timeout_s: float = 1.0
    p2_star_timeout_s: float = 5.0
    tester_present_s: float = 2.0

    def to_dict(self) -> dict:
        return dict(vars(self))

    @classmethod
    def from_dict(cls, d: dict) -> UdsConfig:
        cfg = cls()
        for k, v in d.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
        return cfg
