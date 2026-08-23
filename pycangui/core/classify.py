"""Classify CAN ids into protocol message kinds.

CANopen's predefined connection set (CiA 301) fixes the function code in the
upper 4 bits of an 11-bit id and the node id in the lower 7, so most frames
can be named from the id alone.  The result is a short *kind* ("TPDO1 n5",
"SDO-T n5", "HB n5") and a coarse *group* used for filtering.
"""

from __future__ import annotations

GROUPS = ("NMT", "SYNC/TIME", "EMCY", "PDO", "SDO", "Heartbeat", "LSS", "UDS", "J1939", "Other")

# function code (id >> 7) -> (kind prefix, group, has node id)
_FUNCTION_CODES: dict[int, tuple[str, str, bool]] = {
    0x1: ("EMCY", "EMCY", True),
    0x3: ("TPDO1", "PDO", True),
    0x4: ("RPDO1", "PDO", True),
    0x5: ("TPDO2", "PDO", True),
    0x6: ("RPDO2", "PDO", True),
    0x7: ("TPDO3", "PDO", True),
    0x8: ("RPDO3", "PDO", True),
    0x9: ("TPDO4", "PDO", True),
    0xA: ("RPDO4", "PDO", True),
    0xB: ("SDO-T", "SDO", True),  # server -> client (0x580 + node)
    0xC: ("SDO-R", "SDO", True),  # client -> server (0x600 + node)
    0xE: ("HB", "Heartbeat", True),  # heartbeat / boot-up / node guarding
}


def classify(can_id: int, extended: bool) -> tuple[str, str]:
    """Return (kind, group) for a CAN id."""
    if extended:
        return ("", "J1939")  # label comes from the J1939 manager (PGN name + SA)
    if can_id == 0x000:
        return ("NMT", "NMT")
    if can_id == 0x080:
        return ("SYNC", "SYNC/TIME")
    if can_id == 0x100:
        return ("TIME", "SYNC/TIME")
    if can_id in (0x7E4, 0x7E5):
        return ("LSS", "LSS")
    if can_id == 0x7DF:
        return ("UDS func", "UDS")
    if 0x7E0 <= can_id <= 0x7E7:
        return (f"UDS req {can_id - 0x7E0}", "UDS")
    if 0x7E8 <= can_id <= 0x7EF:
        return (f"UDS resp {can_id - 0x7E8}", "UDS")
    entry = _FUNCTION_CODES.get(can_id >> 7)
    if entry is None:
        return ("", "Other")
    prefix, group, has_node = entry
    node = can_id & 0x7F
    if has_node and node == 0:
        return ("", "Other")  # function code with node 0 is not a valid CANopen id
    return (f"{prefix} n{node}", group)


def group_of(kind: str) -> str:
    """Group for a kind string, including user-provided kinds from the hook."""
    head = kind.split(" ", 1)[0].upper()
    if head == "":
        return "Other"
    for prefix, group in (
        ("NMT", "NMT"),
        ("SYNC", "SYNC/TIME"),
        ("TIME", "SYNC/TIME"),
        ("EMCY", "EMCY"),
        ("TPDO", "PDO"),
        ("RPDO", "PDO"),
        ("PDO", "PDO"),
        ("SDO", "SDO"),
        ("HB", "Heartbeat"),
        ("HEARTBEAT", "Heartbeat"),
        ("LSS", "LSS"),
        ("UDS", "UDS"),
    ):
        if head.startswith(prefix):
            return group
    return "Other"
