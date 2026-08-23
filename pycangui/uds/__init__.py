"""UDS (ISO 14229) diagnostics on ISO-TP, built on udsoncan + can-isotp."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class UdsConfig:
    """Addressing and timing for one ECU.  Saved in settings "uds.config"."""

    tx_id: int = 0x7E0  # tester -> ECU
    rx_id: int = 0x7E8  # ECU -> tester
    functional_id: int = 0x7DF
    extended_id: bool = False  # 29-bit CAN ids (normal addressing)
    padding: int | None = 0xCC  # None = no padding
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
