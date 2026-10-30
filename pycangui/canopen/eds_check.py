# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""A few checks of an EDS or DCF as it is loaded.

The canopen package reads a file and checks almost nothing: one that loads
cleanly can still lack a mandatory object, or map objects into a PDO that do
not exist. These look at the file's own text, so they see what the package
throws away -- the object lists, the counts in [DeviceInfo], ``$NODEID`` in a
value -- and they only report: a device is often worked with in spite of its
file, so nothing here refuses one.

Not a conformance check (CiA 306 has far more to it): the things that, wrong,
make pycangui or the person using it go astray.
"""

from __future__ import annotations

import configparser
import re
from dataclasses import dataclass
from pathlib import Path

from pycangui.canopen import eds_text

#: CiA 301's mandatory objects: device type, error register, identity.
MANDATORY = (0x1000, 0x1001, 0x1018)
#: The object lists, each with a count and numbered entries.
LISTS = ("MandatoryObjects", "OptionalObjects", "ManufacturerObjects")

#: Bits in each fixed-size CiA 301 data type.
BITS = {
    0x0001: 1,  # BOOLEAN
    0x0002: 8,
    0x0003: 16,
    0x0004: 32,
    0x0005: 8,
    0x0006: 16,
    0x0007: 32,
    0x0008: 32,  # REAL32
    0x0010: 24,
    0x0011: 64,  # REAL64
    0x0012: 40,
    0x0013: 48,
    0x0014: 56,
    0x0015: 64,
    0x0016: 24,
    0x0018: 40,
    0x0019: 48,
    0x001A: 56,
    0x001B: 64,
}
SIGNED = {0x0002, 0x0003, 0x0004, 0x0010, 0x0012, 0x0013, 0x0014, 0x0015}
UNSIGNED = {0x0001, 0x0005, 0x0006, 0x0007, 0x0016, 0x0018, 0x0019, 0x001A, 0x001B}
#: Read by a TPDO, written by an RPDO.
READABLE = {"ro", "rw", "rwr", "rww", "const"}
WRITABLE = {"wo", "rw", "rww", "rwr"}
#: The data types' own indexes, which an RPDO may map as dummies to skip bits.
DUMMIES = range(0x0001, 0x0008)
#: The most a classic CAN frame carries.
PDO_BITS = 64
#: The PDO communication and mapping ranges.
RPDO_COMM, TPDO_COMM = range(0x1400, 0x1600), range(0x1800, 0x1A00)
RPDO_MAP, TPDO_MAP = range(0x1600, 0x1800), range(0x1A00, 0x1C00)

_SECTION = re.compile(r"^([0-9A-Fa-f]{4})(?:sub([0-9A-Fa-f]+))?$")
_NODEID = re.compile(r"\$NODEID", re.IGNORECASE)


@dataclass(frozen=True)
class Finding:
    where: str  # "0x1A00 sub 1", "[DeviceInfo]", ...
    what: str

    def __str__(self) -> str:
        return f"{self.where}: {self.what}"


def check(path: str | Path) -> list[Finding]:
    """What is wrong with the file, in the order it was found; [] when nothing is.

    An unreadable file is the loader's to report, not this: [] then too.
    """
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str.lower
    try:
        parser.read_string(eds_text(path))
    except (configparser.Error, OSError):
        return []
    return _Checker(parser).run()


def where(index: int, sub: int | None = None) -> str:
    return f"0x{index:04X}" + ("" if sub is None else f" sub {sub}")


def number(text: str, node_id: int = 1) -> int | None:
    """An integer value as an EDS writes one: decimal, 0x hex, 0 octal, or with
    ``$NODEID`` added. None for one that is none of those."""
    text = text.strip()
    if not text:
        return None
    if _NODEID.search(text):
        total = 0
        for sign, term in re.findall(r"([+-]?)\s*([^\s+-]+)", _NODEID.sub(str(node_id), text)):
            value = number(term)
            if value is None:
                return None
            total += -value if sign == "-" else value
        return total
    try:
        if len(text) > 1 and text[0] == "0" and text[1].isdigit():
            return int(text, 8)
        return int(text, 0)
    except ValueError:
        return None


class _Checker:
    def __init__(self, parser: configparser.ConfigParser) -> None:
        self.p = parser
        self.found: list[Finding] = []
        #: index -> its section name; (index, sub) -> its section name.
        self.objects: dict[int, str] = {}
        self.subs: dict[tuple[int, int], str] = {}
        for name in parser.sections():
            match = _SECTION.match(name)
            if match is None:
                continue
            index = int(match.group(1), 16)
            if match.group(2) is None:
                self.objects[index] = name
            else:
                self.subs[(index, int(match.group(2), 16))] = name

    def say(self, where_: str, what: str) -> None:
        self.found.append(Finding(where_, what))

    def run(self) -> list[Finding]:
        self.mandatory()
        self.lists()
        self.device_info()
        self.values()
        self.cob_ids()
        self.mappings()
        return self.found

    # --- what an entry says -----------------------------------------------------
    def get(self, section: str, key: str) -> str:
        return self.p.get(section, key, fallback="").strip()

    def entry(self, index: int, sub: int) -> str | None:
        """The section describing this entry: the sub's own, or the object's
        when it is a plain variable, which has no subs and is sub 0."""
        if (index, sub) in self.subs:
            return self.subs[(index, sub)]
        if sub == 0 and index in self.objects and not self.has_subs(index):
            return self.objects[index]
        return None

    def has_subs(self, index: int) -> bool:
        section = self.objects.get(index)
        kind = number(self.get(section, "objecttype")) if section else None
        return kind in (0x8, 0x9) or any(i == index for i, _ in self.subs)

    def compact(self, index: int) -> int:
        """How many subs CompactSubObj gives an object without sections of their own."""
        section = self.objects.get(index)
        return (number(self.get(section, "compactsubobj")) or 0) if section else 0

    def entries(self):
        """Every entry that holds a value: (index, sub or None, section)."""
        for index, section in sorted(self.objects.items()):
            if not self.has_subs(index):
                yield index, None, section
        for (index, sub), section in sorted(self.subs.items()):
            yield index, sub, section

    # --- the checks -------------------------------------------------------------
    def mandatory(self) -> None:
        for index in MANDATORY:
            if index not in self.objects:
                self.say(where(index), "a mandatory object, missing")
        if 0x1018 in self.objects and self.entry(0x1018, 1) is None and not self.compact(0x1018):
            self.say(where(0x1018, 1), "the vendor id, which identity must have, missing")

    def lists(self) -> None:
        listed: set[int] = set()
        for name in LISTS:
            if not self.p.has_section(name):
                continue
            count = number(self.get(name, "supportedobjects"))
            numbered = [k for k in self.p.options(name) if k.isdigit()]
            if count is None:
                self.say(f"[{name}]", "SupportedObjects missing or not a number")
            elif count != len(numbered):
                self.say(
                    f"[{name}]", f"SupportedObjects is {count}, and {len(numbered)} are listed"
                )
            for key in numbered:
                index = number(self.get(name, key))
                if index is None:
                    self.say(f"[{name}]", f"entry {key} is not an object index")
                    continue
                listed.add(index)
                if index not in self.objects:
                    self.say(f"[{name}]", f"lists {where(index)}, which has no section")
        if any(self.p.has_section(name) for name in LISTS):
            for index in sorted(set(self.objects) - listed):
                if index not in DUMMIES:
                    self.say(where(index), "described, but in no object list")

    def device_info(self) -> None:
        if not self.p.has_section("DeviceInfo"):
            self.say("[DeviceInfo]", "missing")
            return
        for key, comm, kind in (("nrofrxpdo", RPDO_COMM, "RPDO"), ("nroftxpdo", TPDO_COMM, "TPDO")):
            text = self.get("DeviceInfo", key)
            if not text:
                continue
            said = number(text)
            there = sum(1 for index in self.objects if index in comm)
            if said is None:
                self.say("[DeviceInfo]", f"{key} is not a number")
            elif said != there:
                self.say("[DeviceInfo]", f"says {said} {kind}s, and the file describes {there}")

    def values(self) -> None:
        """Limits the right way round, and the default inside them and its type."""
        for index, sub, section in self.entries():
            kind = number(self.get(section, "datatype"))
            if kind not in SIGNED and kind not in UNSIGNED:
                continue
            bits = BITS[kind]
            least, most = (
                (-(1 << (bits - 1)), (1 << (bits - 1)) - 1)
                if kind in SIGNED
                else (0, (1 << bits) - 1)
            )
            got = {}
            for key in ("lowlimit", "highlimit", "defaultvalue", "parametervalue"):
                text = self.get(section, key)
                if not text:
                    continue
                value = number(text)
                if value is None:
                    self.say(where(index, sub), f"{_KEY_NAMES[key]} {text!r} is not a number")
                    continue
                if kind in SIGNED and value > most and value < (1 << bits):
                    value -= 1 << bits  # written as its two's complement
                got[key] = value
                if not least <= value <= most and not _NODEID.search(text):
                    self.say(where(index, sub), f"{_KEY_NAMES[key]} {text} does not fit its type")
            low, high = got.get("lowlimit"), got.get("highlimit")
            if low is not None and high is not None and low > high:
                self.say(where(index, sub), f"LowLimit {low} is above HighLimit {high}")
                continue
            for key in ("defaultvalue", "parametervalue"):
                value = got.get(key)
                if value is None:
                    continue
                if (low is not None and value < low) or (high is not None and value > high):
                    self.say(where(index, sub), f"{_KEY_NAMES[key]} {value} is outside its limits")

    def cob_ids(self) -> None:
        """The PDO COB-IDs, and EMCY's, are identifiers a frame could carry."""
        for index in sorted(self.objects):
            if index in RPDO_COMM or index in TPDO_COMM or index == 0x1014:
                sub = None if index == 0x1014 else 1
                section = self.entry(index, sub or 0)
                if section is None:
                    continue
                text = self.get(section, "parametervalue") or self.get(section, "defaultvalue")
                if not text:
                    continue
                for node_id in (1, 127):
                    value = number(text, node_id)
                    if value is None:
                        break  # said by values()
                    ident = value & 0x1FFFFFFF
                    extended = bool(value & (1 << 29))
                    if value >> 32 or (not extended and ident > 0x7FF):
                        self.say(
                            where(index, sub),
                            f"COB-ID {text} is not an identifier a frame can carry",
                        )
                        break

    def mappings(self) -> None:
        for index in sorted(self.objects):
            if index in RPDO_MAP:
                self.mapping(index, rpdo=True)
            elif index in TPDO_MAP:
                self.mapping(index, rpdo=False)

    def mapping(self, index: int, rpdo: bool) -> None:
        count_at = self.entry(index, 0)
        if count_at is None:
            return
        count = number(self.get(count_at, "parametervalue") or self.get(count_at, "defaultvalue"))
        if not count:
            return
        total = 0
        for sub in range(1, count + 1):
            section = self.entry(index, sub)
            if section is None:
                self.say(where(index), f"maps {count} objects, and sub {sub} is not there")
                return
            value = number(self.get(section, "parametervalue") or self.get(section, "defaultvalue"))
            if value is None:
                continue
            target, target_sub, length = value >> 16, (value >> 8) & 0xFF, value & 0xFF
            total += length
            if rpdo and target in DUMMIES:
                continue
            mapped = self.entry(target, target_sub)
            plain = target_sub == 0 and not self.has_subs(target)
            name = where(target) if plain else where(target, target_sub)
            if mapped is None:
                if not (target in self.objects and target_sub <= self.compact(target)):
                    self.say(where(index, sub), f"maps {name}, which the file does not describe")
                continue
            if self.get(mapped, "pdomapping") not in ("1", "0x1", "0x01"):
                self.say(where(index, sub), f"maps {name}, which is not marked PDOMapping=1")
            access = self.get(mapped, "accesstype").lower()
            if access and rpdo and access not in WRITABLE:
                self.say(
                    where(index, sub),
                    f"maps {name} into an RPDO, and it cannot be written ({access})",
                )
            if access and not rpdo and access not in READABLE:
                self.say(
                    where(index, sub), f"maps {name} into a TPDO, and it cannot be read ({access})"
                )
            kind = number(self.get(mapped, "datatype"))
            if kind in BITS and length > BITS[kind]:
                self.say(where(index, sub), f"maps {length} bits of {name}, which has {BITS[kind]}")
        if total > PDO_BITS:
            self.say(where(index), f"maps {total} bits, more than the {PDO_BITS} a PDO carries")


_KEY_NAMES = {
    "lowlimit": "LowLimit",
    "highlimit": "HighLimit",
    "defaultvalue": "DefaultValue",
    "parametervalue": "ParameterValue",
}
