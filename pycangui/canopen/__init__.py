"""CANopen protocol layer (built on the `canopen` package).  Types that user
hooks see live here so they are importable from user code."""

from __future__ import annotations

import configparser
import re
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


#: The keys the ``canopen`` package reads out of an object section and keeps
#: on the parsed variable.  Everything else in the section is discarded, and
#: :func:`eds_extras` is what picks it up.  Lower case because an EDS is an
#: INI file and configparser folds keys.
PARSED_KEYS = frozenset(
    {
        "parametername",
        "objecttype",
        "datatype",
        "accesstype",
        "pdomapping",
        "lowlimit",
        "highlimit",
        "defaultvalue",
        "subnumber",
        "compactsubobj",
        "parametervalue",  # a DCF rather than an EDS
        "unit",
        "factor",
        "description",
        "storagelocation",
    }
)

#: ``[1018]`` or ``[1018sub2]``, which is how an EDS names an object.
_OBJECT_SECTION = re.compile(r"^([0-9A-Fa-f]{4})(?:sub([0-9A-Fa-f]+))?$")


def eds_extras(path: Path | str) -> dict[tuple[int, int], dict[str, str]]:
    """Whatever the EDS says about an object that the parser did not keep.

    Read as text rather than through configparser, because the interesting
    part is not a key the parser failed to recognise -- it is a *comment*.
    CiA 306 defines no key for a unit or for scaling, so a vendor with that to
    say has two choices: invent a key, or hide it in a comment where no
    conforming reader will trip over it.  The second is commoner than the
    first, and configparser discards those lines before it looks at anything.

    Both are collected.  An ordinary ``Key=value`` the ``canopen`` package
    does not read, and a comment of the form::

        ;VENDORTAG FIELD_NAME=value

    which becomes the key ``"VENDORTAG FIELD_NAME"``.  The tag is kept because
    it is part of what distinguishes one vendor's convention from another, and
    because pycangui is not the thing that should be deciding what any of it
    means -- ``hooks/canopen.py::object_display`` is.

    Keys keep the case the file wrote them in.  Keyed by (index, subindex),
    and an object with no sub-index is sub 0.
    """
    try:
        text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return {}

    extras: dict[tuple[int, int], dict[str, str]] = {}
    where: tuple[int, int] | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            match = _OBJECT_SECTION.match(line[1:-1])
            where = None
            if match is not None:
                sub = int(match.group(2), 16) if match.group(2) else 0
                where = (int(match.group(1), 16), sub)
            continue
        if where is None:
            continue  # [FileInfo], [DeviceInfo] and the object lists

        if line.startswith(";"):
            key, sep, value = line[1:].strip().partition("=")
        else:
            key, sep, value = line.partition("=")
            if key.strip().lower() in PARSED_KEYS:
                continue  # the parser kept this one; it is on the variable
        if not sep or not key.strip() or not value.strip():
            continue
        extras.setdefault(where, {})[key.strip()] = value.strip()
    return extras


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
