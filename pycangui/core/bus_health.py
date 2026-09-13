# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""How well a CAN controller is taking part in the bus.

A controller counts the errors it makes.  Past 96 it is in *warning*, past
127 *error passive* -- it keeps working but may no longer flag errors it sees
-- and past 255 on transmit it goes **bus off** and stops altogether, until
something restarts it.  Bus off is the one that matters most, and the one
that is easiest to miss, since a bus-off adapter stays connected and hears
nothing, exactly as if the bus were idle.

python-can has no common way to ask for any of this, so each adapter that can
say is read its own way:

* **socketcan** puts it in the error frames: the identifier carries flags, and
  a controller problem has its detail in the second data byte.
* **PCAN** answers a status query with counter-limit and bus-off bits.
* **IXXAT** reports bus off through python-can's ``state`` as ``ERROR``.

Everything else -- Kvaser, Vector, the virtual bus -- reports nothing, and is
judged on error frames alone: seeing them means amber, but a silent bus-off
cannot be told from a quiet bus there, and the status tooltip says so.

The functions here are pure, so every mapping can be tested without an adapter.
"""

from __future__ import annotations

DOWN = "down"
OK = "ok"
WARNING = "warning"
PASSIVE = "error passive"
BUS_OFF = "bus off"

#: Worst first, for picking between what the adapter says and what is seen.
SEVERITY = {DOWN: 0, OK: 1, WARNING: 2, PASSIVE: 3, BUS_OFF: 4}

# linux/can/error.h
CAN_ERR_CRTL = 0x004  # controller problem, detail in data[1]
CAN_ERR_BUSOFF = 0x040
CAN_ERR_RESTARTED = 0x100
CAN_ERR_CRTL_RX_WARNING = 0x04
CAN_ERR_CRTL_TX_WARNING = 0x08
CAN_ERR_CRTL_RX_PASSIVE = 0x10
CAN_ERR_CRTL_TX_PASSIVE = 0x20
CAN_ERR_CRTL_ACTIVE = 0x40

# PCANBasic.py
PCAN_ERROR_BUSLIGHT = 0x00004
PCAN_ERROR_BUSHEAVY = 0x00008
PCAN_ERROR_BUSOFF = 0x00010
PCAN_ERROR_BUSPASSIVE = 0x40000


def from_socketcan_error(can_id: int, data: bytes) -> str | None:
    """What a socketcan error frame says about the controller, if anything.

    Most error frames are about the wire -- a bit error, a missing ACK -- and
    say nothing about the controller's state; those return None and leave the
    last known state standing.
    """
    if can_id & CAN_ERR_BUSOFF:
        return BUS_OFF
    if can_id & CAN_ERR_RESTARTED:
        return OK
    if can_id & CAN_ERR_CRTL and len(data) > 1:
        detail = data[1]
        if detail & (CAN_ERR_CRTL_RX_PASSIVE | CAN_ERR_CRTL_TX_PASSIVE):
            return PASSIVE
        if detail & (CAN_ERR_CRTL_RX_WARNING | CAN_ERR_CRTL_TX_WARNING):
            return WARNING
        if detail & CAN_ERR_CRTL_ACTIVE:
            return OK
    return None


def from_pcan_status(code: int) -> str:
    """PCAN's status code.  BUSHEAVY is error passive to older drivers and the
    warning limit to newer ones, which added BUSPASSIVE; amber either way."""
    if code & PCAN_ERROR_BUSOFF:
        return BUS_OFF
    if code & PCAN_ERROR_BUSPASSIVE:
        return PASSIVE
    if code & (PCAN_ERROR_BUSHEAVY | PCAN_ERROR_BUSLIGHT):
        return WARNING
    return OK


def from_state(interface: str, state: str) -> str | None:
    """python-can's ``state``, where it means the controller's state.

    ``ERROR`` is bus off wherever a backend reports it.  ``PASSIVE`` usually
    is not error passive at all: PCAN, IXXAT and SYS TEC use it for
    listen-only mode, which is a setting rather than a fault, so it counts
    only where the backend means the controller.
    """
    if state == "ERROR":
        return BUS_OFF
    if state == "PASSIVE" and interface == "etas":
        return PASSIVE
    return None


def worst(*healths: str | None) -> str:
    known = [h for h in healths if h]
    return max(known, key=SEVERITY.__getitem__) if known else OK


#: What each state means, for tooltips and the log.
MEANING = {
    DOWN: "Not connected.",
    OK: "Error active: taking part in the bus normally.",
    WARNING: (
        "Errors on the bus.  The controller is still taking part, but something "
        "is wrong -- usually the bitrate, the wiring or the termination."
    ),
    PASSIVE: (
        "Error passive: the controller has counted enough errors that it no longer "
        "flags the ones it sees, and is close to going bus off."
    ),
    BUS_OFF: (
        "Bus off: the controller has stopped taking part in the bus altogether, "
        "and stays stopped until it is restarted."
    ),
}

#: How each adapter that says anything says it.
REPORTED = {
    "socketcan": "The adapter reports this, in its error frames.",
    "pcan": "The adapter reports this.",
    "ixxat": "The adapter reports bus off; warning is judged from error frames.",
    "etas": "The adapter reports error passive; warning is judged from error frames.",
}


def explain(health: str, interface: str) -> str:
    """A state in words, with how it is known on this adapter."""
    text = MEANING[health]
    if health == DOWN:
        return text
    if interface == "virtual":
        how = (
            "A virtual channel has no controller, so it cannot go bus off; only "
            "error frames put on it change this."
        )
    else:
        how = REPORTED.get(
            interface,
            "This adapter does not report its controller state through python-can, "
            "so this is judged from error frames alone -- and a controller that has "
            "gone bus off quietly looks the same as an idle bus.",
        )
    return f"{text}\n\n{how}"
