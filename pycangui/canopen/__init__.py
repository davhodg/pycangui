"""CANopen protocol layer (built on the `canopen` package).  Types that user
hooks see live here so they are importable from user code."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NodeIdentity:
    """What pycangui knows about a node from its heartbeat and object 0x1018."""

    node_id: int
    vendor_id: int | None = None  # 0x1018 sub 1
    product_code: int | None = None  # 0x1018 sub 2
    revision: int | None = None  # 0x1018 sub 3
    serial: int | None = None  # 0x1018 sub 4
    device_type: int | None = None  # 0x1000

    @property
    def key(self) -> str:
        """Stable string used to remember choices for this kind of device."""
        parts = (self.vendor_id, self.product_code, self.revision)
        return ":".join("-" if p is None else f"{p:08X}" for p in parts)
