# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""A flash as a sequence: the steps before and after a download, in order."""

import time

import pytest
from PySide6.QtCore import QCoreApplication, QObject, Signal

from pycangui.uds import sequence
from pycangui.uds.sequence import Sequence, Values

EVERYTHING = {step.key for step in sequence.STEPS}


def wait_until(pred, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not pred():
        QCoreApplication.processEvents()
        if time.monotonic() > deadline and not pred():
            raise AssertionError("timed out")
        time.sleep(0.002)


class Manager(QObject):
    """Stands in for the UDS manager: notes what it is asked, and says it is done."""

    completed = Signal(bool, object)

    def __init__(self) -> None:
        super().__init__()
        self.asked: list[tuple] = []
        self.fails: set[str] = set()
        self._tag = None
        self.cancelled = False

    def tagged(self, tag) -> None:
        self._tag = tag

    def _asked(self, what: str, *with_what) -> None:
        self.asked.append((what, *with_what))
        tag, self._tag = self._tag, None
        self.completed.emit(what not in self.fails, tag)

    def change_session(self, session):
        self._asked("session", session)

    def unlock(self, level):
        self._asked("unlock", level)

    def set_dtc_setting(self, on):
        self._asked("dtc", on)

    def communication_control(self, control, messages):
        self._asked("comm", control, messages)

    def change_bitrate(self, bitrate):
        self._asked("bitrate", bitrate)

    def write_did_bytes(self, did, data):
        self._asked("fingerprint", did, data)

    def ecu_reset(self, kind):
        self._asked("reset", kind)

    def back_to_own_rate(self):
        self._asked("rate_back")

    def channel_home(self):
        self._asked("rate_home")

    def transfer(self):
        self._asked("transfer")

    def cancel_transfer(self):
        self.cancelled = True


@pytest.fixture
def run(app):
    manager = Manager()
    runner = Sequence(manager)
    ended: list[bool] = []
    runner.finished.connect(ended.append)

    def go(chosen, values=None):
        runner.run(chosen, values or Values(reset_wait_s=0), manager.transfer)
        wait_until(lambda: ended)
        return [what[0] for what in manager.asked], ended[-1]

    go.manager, go.runner, go.ended = manager, runner, ended
    return go


def test_every_step_is_done_in_the_order_a_flash_needs(run):
    names, worked = run(EVERYTHING)
    assert worked
    assert names == [
        "session",  # extended
        "unlock",
        "dtc",
        "comm",
        "session",  # programming
        "unlock",
        "bitrate",
        "fingerprint",
        "transfer",
        "reset",
        "rate_home",
        "session",  # extended again, after the reset
        "unlock",
        "comm",
        "dtc",
    ]


def test_the_values_reach_the_steps_that_need_them(run):
    values = Values(
        unlock_extended=1,
        unlock_programming=3,
        unlock_after=5,
        comm_control=3,
        comm_messages=2,
        bitrate=250_000,
        fingerprint_did=0xF15A,
        fingerprint_data="01 02 03",
        reset_type=3,
        reset_wait_s=0,
    )
    run(EVERYTHING, values)
    asked = run.manager.asked
    assert [a[1] for a in asked if a[0] == "unlock"] == [1, 3, 5]
    assert [a[1] for a in asked if a[0] == "session"] == [3, 2, 3]
    assert ("comm", 3, 2) in asked and ("comm", 0, 2) in asked, "off as chosen, then on"
    assert ("bitrate", 250_000) in asked
    assert ("fingerprint", 0xF15A, bytes([1, 2, 3])) in asked
    assert ("reset", 3) in asked
    assert [a[1] for a in asked if a[0] == "dtc"] == [False, True]


def test_only_what_is_chosen_is_done(run):
    names, worked = run({"programming", "unlock_programming", "erase", "check", "reset"})
    assert worked
    assert names == ["session", "unlock", "transfer", "reset", "rate_home"]


def test_nothing_chosen_is_the_transfer_alone(run):
    names, worked = run(set())
    assert worked and names == ["transfer"]


def test_it_stops_at_the_first_failure(run):
    run.manager.fails = {"unlock"}
    names, worked = run({"programming", "unlock_programming", "reset"})
    assert not worked
    assert names == ["session", "unlock"], "no transfer, and no reset, after an unlock that failed"


def test_a_failure_still_puts_the_bus_back(run):
    run.manager.fails = {"transfer"}
    names, worked = run({"dtc_off", "comm_off", "bitrate", "reset", "comm_on", "dtc_on"})
    assert not worked
    assert names == ["dtc", "comm", "bitrate", "transfer", "rate_back", "comm", "dtc"]
    asked = run.manager.asked
    assert asked[-2][:2] == ("comm", 0) and asked[-1] == ("dtc", True)
    assert "reset" not in names, "what was not reached is not done"


def test_putting_back_carries_on_past_a_step_that_fails(run):
    run.manager.fails = {"transfer", "rate_back"}
    names, _worked = run({"dtc_off", "comm_off", "bitrate"})
    assert names[-3:] == ["rate_back", "comm", "dtc"]


def test_nothing_is_put_back_that_was_not_taken(run):
    run.manager.fails = {"session"}
    names, worked = run({"extended", "dtc_off", "comm_off"})
    assert not worked and names == ["session"]


def test_a_request_made_by_hand_is_not_taken_for_the_steps_end(app):
    """The step the sequence is waiting on is the one it tagged, and no other."""

    class Slow(Manager):
        def change_session(self, session):
            self.asked.append(("session", session))  # asked, and not over yet
            self.waiting = self._tag
            self._tag = None

    manager = Slow()
    runner = Sequence(manager)
    runner.run({"programming", "unlock_programming"}, Values(), manager.transfer)
    manager.completed.emit(True, None)  # somebody's own read, finishing
    assert [a[0] for a in manager.asked] == ["session"], "still waiting for the session"
    manager.completed.emit(True, manager.waiting)
    assert [a[0] for a in manager.asked] == ["session", "unlock", "transfer"]


def test_cancelling_stops_after_the_step_going_on_and_puts_back(app):
    class Slow(Manager):
        def transfer(self):
            self.asked.append(("transfer",))
            self.waiting = self._tag
            self._tag = None

    manager = Slow()
    runner = Sequence(manager)
    ended = []
    runner.finished.connect(ended.append)
    runner.run({"comm_off", "reset", "comm_on"}, Values(reset_wait_s=0), manager.transfer)
    runner.cancel()
    assert manager.cancelled, "the transfer is told to stop"
    manager.completed.emit(False, manager.waiting)
    assert ended == [False]
    assert [a[0] for a in manager.asked] == ["comm", "transfer", "comm"]


def test_the_values_come_back_from_what_was_saved():
    values = Values(unlock_programming=3, fingerprint_data="AA BB", reset_wait_s=1.5)
    again = Values.from_saved(values.to_save())
    assert again == values
    assert Values.from_saved({"bitrate": "fast", "unlock_after": 4}) == Values(unlock_after=4)
    assert Values.from_saved(None) == Values()
