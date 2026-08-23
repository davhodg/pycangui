"""CANopen protocol layer (built on the `canopen` package).  Types that user
hooks see live here so they are importable from user code."""

from __future__ import annotations

import configparser
from dataclasses import dataclass, field
from pathlib import Path


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


def eds_identity(path: Path) -> tuple[int | None, int | None, int | None]:
    """(VendorNumber, ProductNumber, RevisionNumber) from an EDS [DeviceInfo] section."""
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        cp.read(path, encoding="utf-8-sig")
    except (configparser.Error, OSError):
        return (None, None, None)
    if not cp.has_section("DeviceInfo"):
        return (None, None, None)

    def num(key: str) -> int | None:
        text = cp.get("DeviceInfo", key, fallback="").strip()
        try:
            return int(text, 0) if text else None
        except ValueError:
            return None

    return (num("VendorNumber"), num("ProductNumber"), num("RevisionNumber"))


def find_eds(identity: NodeIdentity, folders: list[Path]) -> Path | None:
    """First EDS/DCF in the folders whose DeviceInfo matches the node.

    Vendor and product must match; revision is used to prefer an exact match
    when several files qualify.
    """
    if identity.vendor_id is None or identity.product_code is None:
        return None
    best: tuple[int, Path] | None = None
    for folder in folders:
        for path in sorted(folder.glob("*.[eEdD][dDcC][sSfF]")):
            vendor, product, revision = eds_identity(path)
            if vendor != identity.vendor_id or product != identity.product_code:
                continue
            score = 2 if revision == identity.revision else 1
            if best is None or score > best[0]:
                best = (score, path)
    return None if best is None else best[1]


@dataclass(frozen=True, slots=True)
class PdoEntry:
    """One object mapped into a PDO."""

    index: int
    subindex: int
    bits: int
    name: str = ""

    def __str__(self) -> str:
        return f"{self.index:04X}:{self.subindex:02X} {self.name} ({self.bits} bits)"


@dataclass
class PdoConfig:
    """A node's communication and mapping parameters for one PDO."""

    node_id: int
    direction: str  # "TPDO" (the node sends) or "RPDO" (the node receives)
    number: int
    name: str
    cob_id: int
    enabled: bool = True
    transmission_type: int | None = None
    inhibit_time_us: int | None = None
    event_timer_ms: int | None = None
    entries: list[PdoEntry] = field(default_factory=list)

    @property
    def bits(self) -> int:
        return sum(e.bits for e in self.entries)

    def transmission_text(self) -> str:
        t = self.transmission_type
        if t is None:
            return ""
        if t == 0:
            return "0 (synchronous, acyclic)"
        if 1 <= t <= 240:
            return f"{t} (every {t} SYNC)"
        if t in (252, 253):
            return f"{t} (synchronous RTR)"
        if t == 254:
            return f"{t} (event, manufacturer)"
        if t == 255:
            return f"{t} (event, profile)"
        return str(t)
