# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""The CiA 402 drive profile: the state machine, and the objects around it.

CiA 402 is the one CANopen profile where reading and writing objects is not
enough. Everything else pycangui does is *this object holds that value*, which
a custom pane can already show and change. A drive is different in one
specific way: **it will not do anything until it has been walked through a
state machine**, and the walk is a sequence of writes to one object whose
meaning depends on what the drive answered to the last one.

    Switch on disabled --0x06--> Ready to switch on --0x07--> Switched on
                                                                  |
                                                                0x0F
                                                                  v
                                                          Operation enabled

That is why this is code and not a pane full of fields. The state is not a
value either: it is decoded from overlapping masks of the statusword, where
"Ready to switch on" and "Switched on" differ in one bit while "Fault" is a
different mask altogether. A field showing 0x0637 tells nobody anything.

What is *not* here is anything a custom pane can already do. The targets, the
actual values and the modes are ordinary objects, listed below only so that the
pane can put a unit and a type against them without an EDS -- the profile
defines both, so a drive that has never been given an EDS can still be driven.

Nothing here touches Qt or the bus. It is handed something that can read and
write objects, which is what makes all of it testable without either.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

# --- the objects, and what they are -----------------------------------------------------


@dataclass(frozen=True)
class Object:
    """One object of the profile, with the type CiA 402 says it has.

    Carried here rather than looked up in an EDS on purpose: these indices and
    these types are the profile. A drive with no EDS loaded is still a drive,
    and refusing to talk to one until somebody finds the file would be refusing
    over a thing we already know.
    """

    index: int
    sub: int
    name: str
    fmt: str  # struct format, little endian: CANopen is
    unit: str = ""

    @property
    def where(self) -> tuple[int, int]:
        return (self.index, self.sub)


CONTROLWORD = Object(0x6040, 0, "Controlword", "<H")
STATUSWORD = Object(0x6041, 0, "Statusword", "<H")
MODE = Object(0x6060, 0, "Mode of operation", "<b")
MODE_DISPLAY = Object(0x6061, 0, "Mode in use", "<b")
ERROR_CODE = Object(0x603F, 0, "Error code", "<H")

POSITION_ACTUAL = Object(0x6064, 0, "Position", "<i", "counts")
VELOCITY_ACTUAL = Object(0x606C, 0, "Velocity", "<i", "counts/s")
TORQUE_ACTUAL = Object(0x6077, 0, "Torque", "<h", "per mille of rated")

TARGET_POSITION = Object(0x607A, 0, "Target position", "<i", "counts")
TARGET_VELOCITY = Object(0x60FF, 0, "Target velocity", "<i", "counts/s")
TARGET_TORQUE = Object(0x6071, 0, "Target torque", "<h", "per mille of rated")
PROFILE_VELOCITY = Object(0x6081, 0, "Profile velocity", "<I", "counts/s")

#: Limits. These are not mode specific in the way a target is: a drive in
#: any mode is held to its maximum torque, and the velocity limits cap what
#: a profile or a controller may ask for. They are written once for a set-up
#: rather than moment to moment, which is why they sit apart from the target.
MAX_TORQUE = Object(0x6072, 0, "Max torque", "<H", "per mille of rated")
MAX_PROFILE_VELOCITY = Object(0x607F, 0, "Max profile velocity", "<I", "counts/s")
#: In rpm by the standard, unlike the velocity objects above: this one is
#: about the motor rather than about the profile, and CiA 402 gives it
#: revolutions per minute directly.
MAX_MOTOR_SPEED = Object(0x6080, 0, "Max motor speed", "<I", "rpm")

#: Which modes this drive has, as a bit per mode. Bit 0 is profile position,
#: so the bit number is the mode number minus one, and the top sixteen bits
#: are the manufacturer's own.
SUPPORTED_MODES = Object(0x6502, 0, "Supported drive modes", "<I")

#: Mode number -> the bit in 0x6502 that says the drive has it. Velocity
#: mode is bit 1 and mode 2, homing is bit 5 and mode 6: the numbering is
#: off by one and not worth deriving in the head each time.
MODE_BITS = {1: 0, 2: 1, 3: 2, 4: 3, 6: 5, 7: 6, 8: 7, 9: 8, 10: 9}


def modes_in(supported: int) -> set[int]:
    """The modes a drive says it has, out of 0x6502."""
    return {mode for mode, bit in MODE_BITS.items() if supported & (1 << bit)}


#: Read every round. Deliberately short: an SDO round is a round trip per
#: object, and a screen that reads twenty things at 1 Hz is worse than one that
#: reads six at 5 Hz.
WATCHED = (STATUSWORD, MODE_DISPLAY, POSITION_ACTUAL, VELOCITY_ACTUAL, TORQUE_ACTUAL)

#: Written rather than watched: read once when a drive is chosen, and
#: again only when somebody asks.
LIMITS = (MAX_TORQUE, MAX_PROFILE_VELOCITY, MAX_MOTOR_SPEED)


def encode(obj: Object, value: int) -> bytes:
    """The bytes to write for a value, or a reason it will not fit.

    Refused rather than truncated. ``struct`` would happily wrap 70000 into a
    16-bit target and the drive would do exactly what the wrapped number says,
    which is the sort of mistake that moves machinery.
    """
    try:
        return struct.pack(obj.fmt, int(value))
    except struct.error as exc:
        raise ValueError(f"{value} will not fit in {obj.name}: {exc}") from exc


def decode(obj: Object, data: bytes) -> int:
    """The value in a reply, whatever length the drive chose to send.

    A drive answering a 16-bit object with two bytes and one answering with
    four are both being reasonable, and neither is worth an error.
    """
    size = struct.calcsize(obj.fmt)
    if isinstance(data, int):
        return data  # already decoded, by an EDS-aware read
    data = bytes(data)
    if len(data) < size:
        data = data.ljust(size, b"\x00")
    return int(struct.unpack(obj.fmt, data[:size])[0])


# --- the modes ----------------------------------------------------------------------------

#: What CiA 402 numbers mean. Negative numbers are the maker's own, and are
#: shown as themselves rather than guessed at.
MODES = {
    0: "No mode",
    1: "Profile position",
    2: "Velocity",
    3: "Profile velocity",
    4: "Profile torque",
    6: "Homing",
    7: "Interpolated position",
    8: "Cyclic sync position",
    9: "Cyclic sync velocity",
    10: "Cyclic sync torque",
}

#: Which target each mode actually uses. A mode not in here has no target that
#: can be set over SDO, which is a thing to say rather than a box to offer.
TARGET_FOR = {
    1: TARGET_POSITION,
    2: TARGET_VELOCITY,
    3: TARGET_VELOCITY,
    4: TARGET_TORQUE,
    8: TARGET_POSITION,
    9: TARGET_VELOCITY,
    10: TARGET_TORQUE,
}

#: The modes whose target has to arrive every cycle, over a PDO, from something
#: keeping time. Writing one over SDO is not slow, it is meaningless -- the
#: drive expects the next one before the SDO would even have finished.
CYCLIC = (8, 9, 10)


def mode_name(mode: int) -> str:
    if mode in MODES:
        return MODES[mode]
    return f"Manufacturer specific ({mode})" if mode < 0 else f"Reserved ({mode})"


# --- the state machine ----------------------------------------------------------------------

#: Statusword mask, value and name, in the order they are tested. The masks
#: overlap and that is not a mistake: "Ready to switch on" and "Switched on"
#: differ in one bit, while "Fault" ignores bits the others test. This is the
#: table from the standard, and the reason the state cannot be a field on a
#: form.
STATES = (
    (0x4F, 0x00, "Not ready to switch on"),
    (0x4F, 0x40, "Switch on disabled"),
    (0x6F, 0x21, "Ready to switch on"),
    (0x6F, 0x23, "Switched on"),
    (0x6F, 0x27, "Operation enabled"),
    (0x6F, 0x07, "Quick stop active"),
    (0x4F, 0x0F, "Fault reaction active"),
    (0x4F, 0x08, "Fault"),
)

UNKNOWN = "Unknown"

#: Controlword commands. Bit 7 is the fault reset, and it acts on the *rising*
#: edge, which is why clearing one is two writes rather than one.
SHUTDOWN = 0x0006
SWITCH_ON = 0x0007
ENABLE_OPERATION = 0x000F
DISABLE_VOLTAGE = 0x0000
QUICK_STOP = 0x0002
FAULT_RESET = 0x0080

#: Bit 4 in profile position mode: the drive takes the target it has been given
#: on the rising edge of this and not before.
NEW_SETPOINT = 0x0010

#: Bit 8, and the one that matters most here. Halt is defined in every profiled
#: mode as "come to a standstill, the way 0x605D says to", which is what makes
#: it the one command that stops a drive without needing to know what mode it is
#: in or what its target means.
HALT = 0x0100

#: The modes whose target is a *rate*, where writing zero is a command to stop.
#:
#: A position target is not in here, and that is the whole point of the list.
#: Zero is a *place*: writing it to 0x607A does not stop a drive, it sends it to
#: position zero, which on a machine part way through a move is the opposite of
#: stopping and may be the longest move it has been asked for all day.
RATE_TARGETS = {
    2: TARGET_VELOCITY,
    3: TARGET_VELOCITY,
    4: TARGET_TORQUE,
    9: TARGET_VELOCITY,
    10: TARGET_TORQUE,
}

#: Statusword bits worth showing beside the state, since none of them is part
#: of it. Bit 10 means different things in different modes, so it is named for
#: what the standard calls it rather than for what it implies.
#: Bit 5 is not here: it is the quick stop, and it reads the other way up --
#: set means *not* active -- which the state table already says plainly.
FLAGS = (
    (1 << 4, "Voltage enabled"),
    (1 << 7, "Warning"),
    (1 << 9, "Remote"),
    (1 << 10, "Target reached"),
    (1 << 11, "Internal limit active"),
)

#: The bits the profile hands to the maker, in both words.
#:
#: What they mean is not in CiA 402 and cannot be: one drive's bit 15 is a
#: brake release and another's is a spindle orientation request. So they are
#: shown and set by number and left unnamed, which is all that can be said
#: honestly without the manual in front of you.
#:
#: Statusword bit 8 is the maker's too, and bits 12 and 13 are not: those are
#: defined per mode by the standard, so naming them "manufacturer" would be
#: wrong in a way that matters when somebody is comparing this with a manual.
MANUFACTURER_STATUS_BITS = (8, 14, 15)
MANUFACTURER_CONTROL_BITS = (11, 12, 13, 14, 15)


def bits_set(word: int, bits=MANUFACTURER_STATUS_BITS) -> list[int]:
    """Which of ``bits`` are set in ``word``, lowest first."""
    return [bit for bit in sorted(bits) if word & (1 << bit)]


def mask_of(bits) -> int:
    """The bits as one word, ready to be held in a controlword."""
    value = 0
    for bit in bits:
        value |= 1 << bit
    return value


def state_of(statusword: int) -> str:
    """Which of the eight states the drive is in, or that it is in none of them.

    ``Unknown`` rather than a guess. A statusword matching no row is a drive
    doing something this profile does not describe, and naming it anyway would
    put a confident word on screen with nothing behind it.
    """
    for mask, value, name in STATES:
        if statusword & mask == value:
            return name
    return UNKNOWN


def flags_of(statusword: int) -> list[str]:
    """The bits beside the state that are set, in the order the standard has them."""
    return [name for bit, name in FLAGS if statusword & bit]


def is_faulted(statusword: int) -> bool:
    return state_of(statusword) in ("Fault", "Fault reaction active")


def is_enabled(statusword: int) -> bool:
    return state_of(statusword) == "Operation enabled"


@dataclass(frozen=True)
class Step:
    """One controlword to write, and what to say while it is being written."""

    what: str
    controlword: int


#: States a drive leaves by itself, and which therefore cannot be commanded out
#: of. Writing a controlword at one of them is not refused by the drive; it is
#: ignored, which looks exactly like the tool having done nothing.
TRANSIENT = {
    "Not ready to switch on": (
        "The drive is still starting up. It leaves this state by itself, and a "
        "controlword written now is ignored rather than refused."
    ),
    "Fault reaction active": (
        "The drive is still reacting to a fault -- braking, most likely. It "
        "moves to Fault by itself, and can be reset from there."
    ),
    UNKNOWN: (
        "The statusword matches none of the states CiA 402 describes, so what "
        "the drive would make of a command cannot be worked out from here."
    ),
}


def steps_to_enable(statusword: int) -> list[Step]:
    """Everything that has to be written to get from here to Operation enabled.

    The whole reason this is a plugin rather than a pane full of fields: which
    writes are needed depends on the answer to the last one, and there is no
    arrangement of boxes on a form that expresses that.

    A drive in Quick stop active is taken out through Switch on disabled rather
    than straight back to Operation enabled. The direct route exists, but only
    for drives whose quick stop option code says so, and taking it on a drive
    whose code says otherwise leaves it sitting there while the tool claims to
    have enabled it. The long way round works everywhere.
    """
    state = state_of(statusword)
    if why := TRANSIENT.get(state):
        raise ValueError(f"{state}. {why}")
    if state == "Operation enabled":
        return []

    steps: list[Step] = []
    if state == "Fault":
        steps += clear_fault()
        state = "Switch on disabled"  # where a reset leaves it
    if state == "Quick stop active":
        steps.append(Step("Releasing the quick stop", DISABLE_VOLTAGE))
        state = "Switch on disabled"
    if state in ("Switch on disabled", "Ready to switch on", "Switched on"):
        if state == "Switch on disabled":
            steps.append(Step("Shutdown", SHUTDOWN))
            state = "Ready to switch on"
        if state == "Ready to switch on":
            steps.append(Step("Switching on", SWITCH_ON))
            state = "Switched on"
        steps.append(Step("Enabling operation", ENABLE_OPERATION))
    return steps


def clear_fault() -> list[Step]:
    """Reset a fault: two writes, because bit 7 acts on its rising edge.

    A single write of 0x0080 works on a drive whose controlword happened to
    have the bit clear, and does nothing at all on one where it did not. The
    zero first makes the edge rather than hoping for it.
    """
    return [
        Step("Clearing bit 7 to make an edge", DISABLE_VOLTAGE),
        Step("Resetting the fault", FAULT_RESET),
    ]


def steps_to_disable(statusword: int) -> list[Step]:
    """Back to Switch on disabled, from wherever it is now."""
    state = state_of(statusword)
    if why := TRANSIENT.get(state):
        raise ValueError(f"{state}. {why}")
    if state == "Switch on disabled":
        return []
    return [Step("Disabling the drive", DISABLE_VOLTAGE)]


def steps_to_quick_stop(statusword: int) -> list[Step]:
    """Stop it the way the drive was configured to stop.

    Only from Operation enabled: quick stop is a command about motion, and a
    drive that is not enabled is not moving under its own power. Sending it
    anyway would be a button that appears to work and does nothing.
    """
    state = state_of(statusword)
    if state != "Operation enabled":
        raise ValueError(
            f"Quick stop is a command to a drive that is running, and this is {state}."
        )
    return [Step("Quick stop", QUICK_STOP)]


def steps_to_apply_target(mode: int, statusword: int) -> list[Step]:
    """Make a profile position drive act on the target it has been given.

    Profile position is the mode where writing the target is not enough: the
    drive takes it on the rising edge of bit 4 and ignores it until then. Every
    other mode acts on the target as it is written, so asking for this in one of
    those would be two writes that mean nothing.

    Only from Operation enabled, and that is not fussiness. The edge is made
    by writing the *enable* controlword with bit 4 added and then without it, so
    doing this to a drive that is merely switched on would enable it on the way
    past -- which is the one thing here that gets asked about first. A button
    that quietly does the thing another button asks permission for is a hole in
    the permission.
    """
    if mode != 1:
        raise ValueError(
            f"{mode_name(mode)} acts on its target as it is written. "
            "Only profile position waits to be told."
        )
    if not is_enabled(statusword):
        raise ValueError(
            f"The drive has to be enabled before it will take a target, and this is "
            f"{state_of(statusword)}. Applying one is the enable controlword with bit 4 "
            "added, so doing it from here would enable the drive without asking."
        )
    return [
        Step("Clearing bit 4 to make an edge", ENABLE_OPERATION),
        Step("Applying the target", ENABLE_OPERATION | NEW_SETPOINT),
        Step("Clearing bit 4 again", ENABLE_OPERATION),
    ]


def can_set_target(mode: int) -> tuple[bool, str]:
    """Whether a target can usefully be written over SDO in this mode, and why not."""
    if mode in CYCLIC:
        return False, (
            f"{mode_name(mode)} expects a new target every cycle, over a PDO, from "
            "something keeping time. One written here would be stale before it "
            "arrived."
        )
    if mode not in TARGET_FOR:
        return False, f"{mode_name(mode)} has no target to set."
    return True, ""


def stop_writes(mode: int, statusword: int, disable: bool = False) -> list[tuple[Object, int]]:
    """What to write to leave a drive at a standstill, as a screen goes away.

    A demand sent over SDO does not stop when the window showing it does. The
    drive holds the last controlword and the last target it was given, and goes
    on doing exactly what it was told by somebody who can no longer see it. So
    a pane that has commanded motion has to take it back on its way out.

    Halt rather than a zero target, because halt is the one command that means
    "stop" in every mode. A zero is then written to the target as well, but
    *only where the target is a rate*: a zero position is a place rather than a
    stop, and writing one would send the machine there.

    ``disable`` is offered and is not the default. Removing power is not
    obviously safer than commanding a standstill -- on a vertical axis it is the
    load that decides, and whether a brake catches it is a fact about the
    machine that pycangui has no way of knowing.
    """
    if not is_enabled(statusword):
        return []  # not driving anything, so there is nothing to take back
    writes = [(CONTROLWORD, ENABLE_OPERATION | HALT)]
    # After the halt, so that switching it back on later does not start from
    # the demand it was left with.
    if (target := RATE_TARGETS.get(mode)) is not None:
        writes.append((target, 0))
    if disable:
        writes.append((CONTROLWORD, DISABLE_VOLTAGE))
    return writes


# --- what it is all written through -----------------------------------------------------------


class Drive:
    """Whatever can read and write this drive's objects.

    The same seam the firmware plugin uses, and for the same reason: the pane
    hands it a live node, a test hands it a dictionary, and everything above
    this line can be tested without a bus in the room.
    """

    def read(self, obj: Object) -> int:
        raise NotImplementedError

    def write(self, obj: Object, value: int) -> None:
        raise NotImplementedError
