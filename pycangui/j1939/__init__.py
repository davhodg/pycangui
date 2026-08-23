"""SAE J1939 helpers that need no library: 29-bit id layout, PGN names,
DM1 parsing, NAME decoding.  The transport (TP.BAM / TP.CM) and address
claiming come from the `can-j1939` package in ``manager.py``."""

from __future__ import annotations

from dataclasses import dataclass

GLOBAL = 0xFF
NULL_ADDRESS = 0xFE

PGN_NAMES: dict[int, str] = {
    59392: "ACK",
    59904: "Request",
    60160: "TP.DT",
    60416: "TP.CM",
    60928: "AddressClaim",
    61184: "ProprietaryA",
    61440: "ERC1",
    61441: "EBC1",
    61442: "ETC1",
    61443: "EEC2",
    61444: "EEC1",
    61445: "ETC2",
    65132: "TCO1",
    65198: "AT1T1I",
    65217: "VDHR",
    65226: "DM1",
    65227: "DM2",
    65228: "DM3",
    65235: "DM11",
    65242: "SoftwareID",
    65247: "EEC3",
    65248: "VD",
    65253: "HOURS",
    65254: "TD",
    65257: "LFC",
    65259: "ComponentID",
    65260: "VI",
    65262: "ET1",
    65263: "EFL/P1",
    65265: "CCVS1",
    65266: "LFE1",
    65269: "AMB",
    65270: "IC1",
    65271: "VEP1",
    65272: "TRF1",
    65276: "DD",
}


@dataclass(frozen=True, slots=True)
class MessageId:
    priority: int
    pgn: int
    source: int
    destination: int  # GLOBAL for PDU2 / broadcast

    @property
    def pdu1(self) -> bool:
        return ((self.pgn >> 8) & 0xFF) < 240


def parse_id(can_id: int) -> MessageId:
    """Split a 29-bit id into priority, PGN, SA and DA (PDU1 PS byte)."""
    priority = (can_id >> 26) & 0x7
    pf = (can_id >> 16) & 0xFF
    ps = (can_id >> 8) & 0xFF
    dp = (can_id >> 24) & 0x3  # extended data page + data page
    sa = can_id & 0xFF
    if pf < 240:
        return MessageId(priority, (dp << 16) | (pf << 8), sa, ps)
    return MessageId(priority, (dp << 16) | (pf << 8) | ps, sa, GLOBAL)


def build_id(pgn: int, source: int, destination: int = GLOBAL, priority: int = 6) -> int:
    pf = (pgn >> 8) & 0xFF
    ps = destination if pf < 240 else pgn & 0xFF
    return (priority << 26) | ((pgn >> 16 & 0x3) << 24) | (pf << 16) | (ps << 8) | source


def pgn_mask(pgn: int) -> int:
    """Bits of a 29-bit id that identify this PGN (for DBC matching)."""
    return 0x03FF0000 if ((pgn >> 8) & 0xFF) < 240 else 0x03FFFF00


def pgn_label(pgn: int) -> str:
    name = PGN_NAMES.get(pgn)
    return f"{name} ({pgn})" if name else f"PGN {pgn} (0x{pgn:05X})"


# --- DM1 / DM2 -------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Dtc:
    spn: int
    fmi: int
    occurrence: int
    conversion_method: int


@dataclass(frozen=True, slots=True)
class Dm1:
    protect_lamp: int
    amber_warning_lamp: int
    red_stop_lamp: int
    malfunction_lamp: int
    dtcs: tuple[Dtc, ...]

    def lamps(self) -> str:
        names = []
        for value, short in (
            (self.malfunction_lamp, "MIL"),
            (self.red_stop_lamp, "RSL"),
            (self.amber_warning_lamp, "AWL"),
            (self.protect_lamp, "PL"),
        ):
            if value == 1:
                names.append(short)
        return " ".join(names)


def parse_dm1(data: bytes) -> Dm1:
    """DM1/DM2 payload: 2 lamp bytes then 4-byte DTCs (SPN conversion method 4)."""
    lamps = data[0] if data else 0xFF
    dtcs = []
    for i in range(2, len(data) - 3, 4):
        b0, b1, b2, b3 = data[i : i + 4]
        spn = b0 | (b1 << 8) | ((b2 >> 5) << 16)
        if spn == 0 and b2 == 0 and b3 == 0:
            continue  # padding / "no DTC" placeholder
        dtcs.append(Dtc(spn, b2 & 0x1F, b3 & 0x7F, b3 >> 7))
    return Dm1(
        protect_lamp=lamps & 0x3,
        amber_warning_lamp=(lamps >> 2) & 0x3,
        red_stop_lamp=(lamps >> 4) & 0x3,
        malfunction_lamp=(lamps >> 6) & 0x3,
        dtcs=tuple(dtcs),
    )


def encode_dm1(dtcs: list[Dtc], mil: int = 0, rsl: int = 0, awl: int = 0, pl: int = 0) -> bytes:
    out = bytearray([(mil << 6) | (rsl << 4) | (awl << 2) | pl, 0xFF])
    for d in dtcs:
        out += bytes(
            [
                d.spn & 0xFF,
                (d.spn >> 8) & 0xFF,
                ((d.spn >> 16) & 0x7) << 5 | (d.fmi & 0x1F),
                (d.conversion_method << 7) | (d.occurrence & 0x7F),
            ]
        )
    if not dtcs:
        out += bytes(4)
    return bytes(out)


# --- NAME ------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Name:
    value: int

    @property
    def identity_number(self) -> int:
        return self.value & 0x1FFFFF

    @property
    def manufacturer_code(self) -> int:
        return (self.value >> 21) & 0x7FF

    @property
    def ecu_instance(self) -> int:
        return (self.value >> 32) & 0x7

    @property
    def function_instance(self) -> int:
        return (self.value >> 35) & 0x1F

    @property
    def function(self) -> int:
        return (self.value >> 40) & 0xFF

    @property
    def vehicle_system(self) -> int:
        return (self.value >> 49) & 0x7F

    @property
    def industry_group(self) -> int:
        return (self.value >> 60) & 0x7

    @property
    def arbitrary_address_capable(self) -> bool:
        return bool(self.value >> 63)

    @classmethod
    def from_bytes(cls, data: bytes) -> Name:
        return cls(int.from_bytes(data[:8], "little"))

    def summary(self) -> str:
        return (
            f"mfr {self.manufacturer_code} fn {self.function} inst {self.function_instance}"
            f" ecu {self.ecu_instance} ig {self.industry_group} id {self.identity_number}"
        )
