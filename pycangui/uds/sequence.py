# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""A flash as a sequence: what is asked of the ECU before a download, and after it.

Writing a controller is rarely the download alone. The bus is quietened, the
ECU is taken into its programming session and unlocked, its memory is erased;
afterwards it is reset and the bus is given back. Each of those is a request
the pane can already make, on three different tabs, in an order that has to be
right -- and doing it by hand is how the one that gets forgotten is found out.

So the order is written down here once, as a list of steps, and what somebody
chooses is which of them their ECU wants. The order is the usual one of ISO
14229-1's programming sequence and is not theirs to change: a list that could
be put in any order is an editor, a file format and a way to be wrong, and the
steps here are a list so that one can be built on them when an ECU turns up
that needs it.

Two rules, whatever is chosen. It stops at the first step that fails. And
having stopped, it still puts back what it took away from the rest of the bus
-- the bitrate, communication, the recording of faults -- because a failed
flash that leaves every other ECU silent is two problems.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, fields

from PySide6.QtCore import QObject, QTimer, Signal

BEFORE, AFTER = "before", "after"

EXTENDED_SESSION, PROGRAMMING_SESSION = 0x03, 0x02
COMM_ON = 0  #: CommunicationControl: enable Rx and Tx


@dataclass(frozen=True)
class Step:
    key: str
    title: str
    phase: str
    tip: str


#: In the order they are done. The download itself goes between the two phases.
STEPS = (
    Step(
        "extended",
        "Extended session (0x10 03)",
        BEFORE,
        "The session the steps that quieten the bus are allowed in.",
    ),
    Step(
        "unlock_extended",
        "Unlock (0x27), in the extended session",
        BEFORE,
        "SecurityAccess at the level given in Values, for an ECU that wants it\n"
        "before it will take what follows.",
    ),
    Step(
        "dtc_off",
        "DTC setting off (0x85 02)",
        BEFORE,
        "So that the ECUs do not record a fault for the messages that are\nabout to stop arriving.",
    ),
    Step(
        "comm_off",
        "Communication off (0x28)",
        BEFORE,
        "Quieten the bus for the flash, in the way chosen in Values.",
    ),
    Step(
        "programming",
        "Programming session (0x10 02)",
        BEFORE,
        "Where an ECU hands over to its bootloader.",
    ),
    Step(
        "unlock_programming",
        "Unlock (0x27), in the programming session",
        BEFORE,
        "SecurityAccess at the level given in Values: nearly every bootloader\n"
        "wants it before an erase or a download.",
    ),
    Step(
        "bitrate",
        "Change bitrate (0x87)",
        BEFORE,
        "LinkControl to the rate given in Values, and this channel follows.",
    ),
    Step(
        "fingerprint",
        "Write fingerprint (0x2E)",
        BEFORE,
        "Write the identifier and bytes given in Values: who is flashing and\n"
        "when, which some bootloaders want written before they will erase.",
    ),
    Step(
        "erase",
        "Erase memory (0x31 FF00)",
        BEFORE,
        "RoutineControl start FF00 over every segment, before the first is written.",
    ),
    Step(
        "check",
        "Check memory (0x31)",
        AFTER,
        "Run the check routine once each segment has been sent.",
    ),
    Step(
        "reset",
        "ECU reset (0x11)",
        AFTER,
        "Restart the ECU into what was just written, then wait for it, for as\n"
        "long as Values says.",
    ),
    Step(
        "rate_back",
        "Back to own bitrate",
        AFTER,
        "Take the channel back to the rate it had. After a reset the ECUs are\n"
        "there already; without one, the session is ended to take them back.",
    ),
    Step(
        "extended_after",
        "Extended session (0x10 03)",
        AFTER,
        "Back into the extended session, for what is put right after a reset.",
    ),
    Step(
        "unlock_after",
        "Unlock (0x27), in the extended session",
        AFTER,
        "SecurityAccess again at the level given in Values: a reset locks it.",
    ),
    Step(
        "comm_on",
        "Communication on (0x28 00)",
        AFTER,
        "Give the bus back: enable Rx and Tx.",
    ),
    Step(
        "dtc_on",
        "DTC setting on (0x85 01)",
        AFTER,
        "Let the ECUs record faults again.",
    ),
)
BY_KEY = {step.key: step for step in STEPS}

#: Done inside the download itself, over each segment, so chosen here and
#: carried out there: the erase before the first block, the check after the last.
IN_TRANSFER = frozenset({"erase", "check"})

#: What a failed sequence puts back, and the step that says it was taken away.
PUT_BACK = (("bitrate", "rate_back"), ("comm_off", "comm_on"), ("dtc_off", "dtc_on"))


@dataclass
class Values:
    """The numbers the steps need, where the pane has no box of its own for them."""

    unlock_extended: int = 1
    unlock_programming: int = 1
    unlock_after: int = 1
    #: CommunicationControl for "off": 1 is enable Rx and disable Tx.
    comm_control: int = 1
    comm_messages: int = 1
    bitrate: int = 500_000
    fingerprint_did: int = 0xF15A
    fingerprint_data: str = ""
    #: The routine run after each segment where Check memory is ticked. The
    #: HIS/AUTOSAR bootloaders' number, and not a standard: yours may differ.
    check_routine: int = 0x0202
    reset_type: int = 1
    reset_wait_s: float = 2.0

    @classmethod
    def from_saved(cls, saved) -> Values:
        """From what settings.json holds, taking only what is of the right kind."""
        values = cls()
        if isinstance(saved, dict):
            for field in fields(cls):
                given = saved.get(field.name)
                if isinstance(given, type(getattr(values, field.name))) or (
                    field.name == "reset_wait_s" and isinstance(given, int | float)
                ):
                    setattr(values, field.name, type(getattr(values, field.name))(given))
        return values

    def to_save(self) -> dict:
        return asdict(self)


def described(chosen) -> tuple[list[str], list[str]]:
    """The chosen steps' titles, before and after, in the order they are done."""
    before = [s.title for s in STEPS if s.key in chosen and s.phase == BEFORE]
    after = [s.title for s in STEPS if s.key in chosen and s.phase == AFTER]
    return before, after


class Sequence(QObject):
    """Runs the chosen steps round a transfer, one at a time.

    On the window's thread, and it does nothing itself: each step is a request
    the manager makes on its worker, and this waits to be told that one is
    over before starting the next.
    """

    #: A line for the pane's log: which step is starting, and how it ended.
    said = Signal(str)
    running = Signal(bool)
    #: Whether every step went through.
    finished = Signal(bool)

    def __init__(self, manager, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._manager = manager
        self._plan: list[tuple[str, str, Callable[[], None] | float]] = []
        self._done: list[str] = []
        self._token: object | None = None
        self._title = ""
        self._key = ""
        self._putting_back = False
        self._cancelled = False
        self._values = Values()
        self.is_running = False
        manager.completed.connect(self._on_completed)

    # --- what is to be done -------------------------------------------------------------
    def plan(self, chosen, values: Values, transfer: Callable[[], None]) -> list[tuple]:
        """Every step as (key, title, what starts it), in order.

        What starts it is a call, or a number of seconds to wait.
        """
        m = self._manager
        how = {
            "extended": lambda: m.change_session(EXTENDED_SESSION),
            "unlock_extended": lambda: m.unlock(values.unlock_extended),
            "dtc_off": lambda: m.set_dtc_setting(False),
            "comm_off": lambda: m.communication_control(values.comm_control, values.comm_messages),
            "programming": lambda: m.change_session(PROGRAMMING_SESSION),
            "unlock_programming": lambda: m.unlock(values.unlock_programming),
            "bitrate": lambda: m.change_bitrate(values.bitrate),
            "fingerprint": lambda: m.write_did_bytes(
                values.fingerprint_did, bytes.fromhex(values.fingerprint_data)
            ),
            "reset": lambda: m.ecu_reset(values.reset_type),
            "rate_back": m.back_to_own_rate,
            "extended_after": lambda: m.change_session(EXTENDED_SESSION),
            "unlock_after": lambda: m.unlock(values.unlock_after),
            "comm_on": lambda: m.communication_control(COMM_ON, values.comm_messages),
            "dtc_on": lambda: m.set_dtc_setting(True),
        }
        steps = [s for s in STEPS if s.key in chosen and s.key not in IN_TRANSFER]
        plan: list[tuple] = [(s.key, s.title, how[s.key]) for s in steps if s.phase == BEFORE]
        plan.append(("transfer", "Transfer", transfer))
        for step in steps:
            if step.phase != AFTER:
                continue
            if step.key == "rate_back" and "reset" in chosen:
                continue  # done with the reset, below
            plan.append((step.key, step.title, how[step.key]))
            if step.key == "reset":
                plan.append(
                    ("wait", f"Wait {values.reset_wait_s:g} s for the ECU", values.reset_wait_s)
                )
                # The ECUs restart at their own rate, so the channel goes
                # back to it whether or not that was ticked: at the other
                # rate there is nobody left to talk to.
                plan.append(("rate_home", "Channel back to its own bitrate", m.channel_home))
        return plan

    # --- doing it -----------------------------------------------------------------------
    def run(self, chosen, values: Values, transfer: Callable[[], None]) -> None:
        if self.is_running:
            return
        self._plan = self.plan(chosen, values, transfer)
        self._values = values
        self._done = []
        self._putting_back = False
        self._cancelled = False
        self.is_running = True
        self.running.emit(True)
        self._next()

    def cancel(self) -> None:
        """Stop after the step that is going on now, and put the bus back."""
        if self.is_running and not self._putting_back:
            self._cancelled = True
            self._manager.cancel_transfer()

    def _next(self) -> None:
        if not self._plan:
            self._end(not self._putting_back)
            return
        self._key, self._title, start = self._plan.pop(0)
        self.said.emit(f"Sequence: {self._title}")
        if isinstance(start, int | float):
            self._token = None
            QTimer.singleShot(round(start * 1000), self._waited)
            return
        self._token = object()
        self._manager.tagged(self._token)
        start()

    def _waited(self) -> None:
        if self.is_running:
            self._step_over(True)

    def _on_completed(self, worked: bool, tag: object) -> None:
        if self.is_running and tag is not None and tag is self._token:
            self._token = None
            self._step_over(worked)

    def _step_over(self, worked: bool) -> None:
        if self._putting_back:
            self._next()  # whatever came of it: the rest is still to put back
            return
        if worked and not self._cancelled:
            self._done.append(self._key)
            self._next()
            return
        why = "cancelled" if self._cancelled else "failed"
        self.said.emit(f"Sequence stopped: {self._title} {why}")
        self._put_back()

    def _put_back(self) -> None:
        """What was taken from the bus and not yet given back, given back."""
        m = self._manager
        messages = self._values.comm_messages
        how = {
            "rate_back": m.back_to_own_rate,
            "comm_on": lambda: m.communication_control(COMM_ON, messages),
            "dtc_on": lambda: m.set_dtc_setting(True),
        }
        self._putting_back = True
        self._plan = []
        for took, undo in PUT_BACK:
            if took not in self._done or undo in self._done:
                continue  # never taken away, or given back already
            if undo == "rate_back" and "rate_home" in self._done:
                continue  # the channel went home with the reset
            self._plan.append((undo, f"Putting back: {BY_KEY[undo].title}", how[undo]))
        self._next()

    def _end(self, worked: bool) -> None:
        self.is_running = False
        self._token = None
        self.said.emit("Sequence complete" if worked else "Sequence did not complete")
        self.running.emit(False)
        self.finished.emit(worked)
