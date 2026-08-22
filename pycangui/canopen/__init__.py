"""CANopen protocol layer (built on the `canopen` package).  Types that user
hooks see live here so they are importable from user code."""

from __future__ import annotations

import configparser
from dataclasses import dataclass
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
