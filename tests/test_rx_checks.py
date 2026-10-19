# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Checking the counter and checksum of received messages, with the transmit
side's configuration and arithmetic."""

import pytest

from pycangui import resources
from pycangui.core import tx_fields as tx
from pycangui.core.bus import Frame
from pycangui.core.dbc import DbcDecoder
from pycangui.core.rx_checks import Check, Checker

COUNTER = tx.Counter(at=tx.Placement(0, tx.LOW))
CHECKSUM = tx.Checksum(at=tx.Placement(7), algorithm="crc8_j1850")


def frame(data: bytes, can_id=0x100, rx=True, channel="CAN1") -> Frame:
    return Frame(0.0, channel, can_id, False, False, rx, bytes(data))


def sent(n: int, counter=COUNTER, checksum=CHECKSUM, payload=b"\x00\x11\x22\x33\x44\x55\x66\x00"):
    """The n-th frame as CAN Transmit would make it."""
    return tx.apply(payload, counter, checksum, sent=n)


@pytest.fixture
def checker():
    made = Checker()
    made.set(Check(0x100, counter=COUNTER, checksum=CHECKSUM))
    return made


def test_frames_made_by_the_transmit_side_all_pass(checker):
    """The same configuration and arithmetic as the generator, so what it
    makes is what this accepts -- across a wrap of the nibble counter too."""
    outcomes = [checker.check(frame(sent(n))) for n in range(40)]
    assert all(o is not None and not o.fault for o in outcomes)
    assert checker.tallies[(0x100, False)].counter_faults == 0
    assert checker.tallies[(0x100, False)].checksum_faults == 0


def test_a_repeated_count_and_a_skipped_one_are_both_faults(checker):
    checker.check(frame(sent(0)))
    checker.check(frame(sent(1)))
    repeated = checker.check(frame(sent(1)))
    skipped = checker.check(frame(sent(4)))
    assert repeated.counter and not repeated.checksum
    assert skipped.counter and not skipped.checksum
    assert repeated.counter != skipped.counter, "said differently: a repeat is not a loss"
    assert checker.tallies[(0x100, False)].counter_faults == 2


def test_the_first_frame_seen_starts_the_count(checker):
    """Joining a bus part way through is not a fault."""
    assert not checker.check(frame(sent(9))).fault
    assert not checker.check(frame(sent(10))).fault


def test_a_wrong_checksum_is_a_fault_and_says_what_was_expected(checker):
    bad = bytearray(sent(0))
    bad[7] ^= 0xFF
    outcome = checker.check(frame(bad))
    assert outcome.checksum and not outcome.counter
    assert f"{sent(0)[7]:02X}" in outcome.checksum, "the value it should have been"


def test_a_run_of_faults_is_said_to_start_and_to_end(checker):
    checker.check(frame(sent(0)))
    first = checker.check(frame(sent(0)))  # repeat
    second = checker.check(frame(sent(0)))  # repeat again
    right = checker.check(frame(sent(1)))
    assert first.started and not second.started
    assert right.ended == 2 and not right.fault


def test_each_channel_counts_for_itself(checker):
    """A frame lost on one bus is not a count skipped on the other."""
    checker.check(frame(sent(0), channel="CAN1"))
    checker.check(frame(sent(5), channel="CAN2"))
    assert not checker.check(frame(sent(1), channel="CAN1")).fault
    assert not checker.check(frame(sent(6), channel="CAN2")).fault


def test_what_pycangui_sent_and_other_messages_are_left_alone(checker):
    assert checker.check(frame(sent(0), rx=False)) is None
    assert checker.check(frame(sent(0), can_id=0x200)) is None


def test_a_frame_too_short_for_its_fields_is_a_fault_not_a_crash(checker):
    assert checker.check(frame(b"\x01\x02")).fault


def test_a_maker_s_own_checksum_comes_from_the_hook():
    seen = []

    def hook(name, can_id, data):
        seen.append((name, can_id, data))
        return 0x5A

    checker = Checker(hook=hook)
    checker.set(Check(0x100, checksum=CHECKSUM))
    good = bytearray(8)
    good[7] = 0x5A
    assert not checker.check(frame(good)).fault
    assert seen and seen[0][2][7] == 0, "handed the frame with the checksum's own byte at zero"


def test_the_checks_are_kept_and_given_back():
    checker = Checker()
    checker.set(Check(0x123, counter=COUNTER, checksum=CHECKSUM, message="Status"))
    again = Checker()
    assert again.load(checker.to_list()) == []
    assert again.checks[(0x123, False)].to_dict() == checker.checks[(0x123, False)].to_dict()


def test_setting_neither_field_takes_the_check_away(checker):
    checker.set(Check(0x100))
    assert checker.check(frame(sent(0))) is None


# --- named by database signal -------------------------------------------------------------
@pytest.fixture
def pump():
    decoder = DbcDecoder()
    decoder.load(str(resources.path("demo.dbc")))
    return decoder.message_by_name("PumpCommand")


def by_signal(pump, n: int, speed=250):
    counter = tx.Counter(signal="PumpEnable")
    checksum = tx.Checksum(signal="PumpSpeedDemand", algorithm="sum8")
    return tx.apply_signals(
        lambda v: pump.encode(v, padding=False, strict=False),
        {"PumpEnable": 0, "PumpSpeedDemand": speed},
        counter,
        checksum,
        sent=n,
        counter_bits=1,
        checksum_bits=next(s.length for s in pump.signals if s.name == "PumpSpeedDemand"),
    )


def test_fields_in_database_signals_are_checked_through_the_database(pump):
    checker = Checker(message_by_name=lambda name: pump if name == "PumpCommand" else None)
    checker.set(
        Check(
            pump.frame_id,
            counter=tx.Counter(signal="PumpEnable"),
            checksum=tx.Checksum(signal="PumpSpeedDemand", algorithm="sum8"),
            message="PumpCommand",
        )
    )
    frames = [frame(by_signal(pump, n), can_id=pump.frame_id) for n in range(6)]
    assert all(not checker.check(f).fault for f in frames)
    assert checker.check(frame(by_signal(pump, 6), can_id=pump.frame_id)).fault == ""
    repeated = checker.check(frame(by_signal(pump, 6), can_id=pump.frame_id))
    assert repeated.counter, "the one-bit counter did not move"


def test_a_message_not_in_a_loaded_database_says_so():
    checker = Checker()
    checker.set(Check(0x123, counter=tx.Counter(signal="Alive"), message="Gone"))
    assert checker.check(frame(b"\x00" * 8, can_id=0x123)).fault


def test_a_check_is_said_as_the_transmit_list_says_it(pump):
    """A field in a signal by the signal's position, as CAN Transmit shows it."""
    checker = Checker(message_by_name=lambda name: pump if name == "PumpCommand" else None)
    counter = tx.Counter(signal="PumpEnable")
    checker.set(Check(pump.frame_id, counter=counter, message="PumpCommand"))
    outcome = checker.check(frame(bytes(pump.length), can_id=pump.frame_id))
    places = {s.name: (s.start, s.length) for s in pump.signals}
    assert outcome.said == tx.describe(counter, None, places)
    assert "PumpEnable" not in outcome.said
