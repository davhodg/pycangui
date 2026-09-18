# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Classify CAN ids into protocol message kinds.

CANopen's predefined connection set (CiA 301) fixes the function code in the
upper 4 bits of an 11-bit id and the node id in the lower 7, so most frames
can be named from the id alone. The result is a short *kind* ("TPDO1 n5",
"SDO-T n5", "HB n5") and a coarse *group* used for filtering.
"""

from __future__ import annotations

#: Error frames get a group of their own so the trace Filter menu can hide
#: them: they are the controller reporting a fault, not traffic, and a bus in
#: trouble produces far more of them than of anything else.
ERROR_GROUP = "Bus errors"

GROUPS = (
    "NMT",
    "SYNC/TIME",
    "EMCY",
    "PDO",
    "SDO",
    "Heartbeat",
    "LSS",
    "UDS",
    "J1939",
    "XCP",
    ERROR_GROUP,
    "Other",
)

# function code (id >> 7) -> (kind prefix, group, has node id)
_FUNCTION_CODES: dict[int, tuple[str, str, bool]] = {
    0x1: ("EMCY", "EMCY", True),
    0x3: ("TxPDO1", "PDO", True),
    0x4: ("RxPDO1", "PDO", True),
    0x5: ("TxPDO2", "PDO", True),
    0x6: ("RxPDO2", "PDO", True),
    0x7: ("TxPDO3", "PDO", True),
    0x8: ("RxPDO3", "PDO", True),
    0x9: ("TxPDO4", "PDO", True),
    0xA: ("RxPDO4", "PDO", True),
    0xB: ("SDO-T", "SDO", True),  # server -> client (0x580 + node)
    0xC: ("SDO-R", "SDO", True),  # client -> server (0x600 + node)
    0xE: ("Heartbeat", "Heartbeat", True),  # heartbeat / boot-up / node guarding
}


def predefined_labels(node_id: int) -> dict[int, str]:
    """Where CiA 301 says one node's frames are, and what to call each.

    Used for a node that is known to be there. The same table read the
    other way round -- id to node -- would claim most of the 11-bit range
    for CANopen on a bus that has none, which is why nothing calls it that
    way any more.
    """
    labels = {}
    for code, (prefix, _group, has_node) in _FUNCTION_CODES.items():
        if has_node:
            labels[(code << 7) | node_id] = f"{prefix} n{node_id}"
    return labels


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
        ("TXPDO", "PDO"),
        ("RXPDO", "PDO"),
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
