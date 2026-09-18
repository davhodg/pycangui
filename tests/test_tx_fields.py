# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Counters and checksums for transmitted messages.

The arithmetic, away from the pane. A wrong checksum is invisible from the
sending end -- the frames go out and look fine -- so the only place it can be
caught is here, which is why these are specific about values rather than
merely about shapes.

The CRC values below are checked against the published check value for each
polynomial ("123456789" in ASCII), not against what this module happens to
produce, because a CRC that agrees with itself is no use at all.
"""

import pytest

from pycangui.core import tx_fields as tx
from pycangui.core.tx_fields import Checksum, Counter, FieldError, Placement

CHECK = b"123456789"  # the standard CRC check string


# --- where a field sits ------------------------------------------------------------------
def test_a_whole_byte_is_read_and_written():
    data = bytearray(b"\x00\x00\x00\x00")
    at = Placement(byte=2)
    at.write(data, 0xAB)
    assert bytes(data) == b"\x00\x00\xab\x00"
    assert at.read(data) == 0xAB


def test_a_nibble_leaves_the_other_half_alone():
    """The case that makes nibbles worth supporting: a four-bit counter
    sharing a byte with something that must survive it."""
    data = bytearray(b"\xf5")
    Placement(byte=0, part=tx.LOW).write(data, 0x3)
    assert bytes(data) == b"\xf3", "the high nibble was overwritten"

    data = bytearray(b"\xf5")
    Placement(byte=0, part=tx.HIGH).write(data, 0x3)
    assert bytes(data) == b"\x35", "the low nibble was overwritten"


def test_a_value_too_big_for_its_field_wraps_rather_than_raising():
    """A counter told to count to 100 in a nibble is a configuration somebody
    will make, and wrapping is what the field does anyway."""
    data = bytearray(b"\x00")
    Placement(byte=0, part=tx.LOW).write(data, 0x1F)
    assert data[0] == 0x0F


def test_two_bytes_go_in_the_order_asked_for():
    data = bytearray(4)
    Placement(byte=1, width=2, endian=tx.BIG).write(data, 0x1234)
    assert bytes(data) == b"\x00\x12\x34\x00"

    data = bytearray(4)
    Placement(byte=1, width=2, endian=tx.LITTLE).write(data, 0x1234)
    assert bytes(data) == b"\x00\x34\x12\x00"


def test_a_field_past_the_end_says_so():
    """Rather than silently extending the message, which would change its
    length and mean a different message entirely."""
    with pytest.raises(FieldError, match="past the end"):
        Placement(byte=7, width=2).write(bytearray(8), 0)


def test_a_nibble_cannot_be_two_bytes_wide():
    with pytest.raises(FieldError, match="half of one byte"):
        Placement(byte=0, part=tx.LOW, width=2)


# --- the algorithms, against their published check values ------------------------
@pytest.mark.parametrize(
    ("algorithm", "expected"),
    [
        ("crc8_j1850", 0x4B),  # CRC-8/SAE-J1850
        ("crc8_2f", 0xDF),  # CRC-8/AUTOSAR, the 0x2F polynomial
        ("crc16_ccitt", 0x29B1),  # CRC-16/IBM-3740, "CCITT-FALSE"
    ],
)
def test_the_crcs_agree_with_the_published_check_value(algorithm, expected):
    """A CRC that only agrees with itself is worth nothing: the point of one
    is that the device at the other end computes the same number."""
    assert tx.ALGORITHMS[algorithm][0](CHECK) == expected


def test_xor_and_sum_are_what_they_say():
    data = b"\x01\x02\x03\xff"
    assert tx.ALGORITHMS["xor"][0](data) == 0x01 ^ 0x02 ^ 0x03 ^ 0xFF
    assert tx.ALGORITHMS["sum8"][0](data) == (1 + 2 + 3 + 255) & 0xFF


def test_a_twos_complement_sum_makes_the_message_add_up_to_nothing():
    """Which is how a receiver checks one: add every byte including the
    checksum and expect zero."""
    data = b"\x10\x20\x30"
    checksum = tx.ALGORITHMS["sum8_twos"][0](data)
    assert (sum(data) + checksum) & 0xFF == 0


def test_a_sixteen_bit_algorithm_says_it_needs_two_bytes():
    assert tx.width_of("crc16_ccitt") == 2
    assert tx.width_of("xor") == 1


# --- what is covered ---------------------------------------------------------------------
def test_by_default_a_checksum_leaves_its_own_bytes_out():
    """Including them means hashing a field that is about to be overwritten,
    so the number never matches on the receiving side."""
    checksum = Checksum(at=Placement(byte=7), algorithm="xor")
    assert checksum.over(8) == [0, 1, 2, 3, 4, 5, 6]


def test_a_sixteen_bit_checksum_leaves_both_of_its_bytes_out():
    checksum = Checksum(at=Placement(byte=6, width=2), algorithm="crc16_ccitt")
    assert checksum.over(8) == [0, 1, 2, 3, 4, 5]


def test_an_explicit_range_is_obeyed():
    """Some protocols checksum a part of the payload and nothing else."""
    checksum = Checksum(at=Placement(byte=7), algorithm="xor", first=1, last=3)
    assert checksum.over(8) == [1, 2, 3]


def test_a_backwards_range_is_refused():
    with pytest.raises(FieldError, match="after byte"):
        Checksum(at=Placement(byte=7), first=5, last=2).over(8)


# --- the counter -------------------------------------------------------------------------
def test_a_counter_moves_on_every_frame():
    counter = Counter(at=Placement(byte=0, part=tx.LOW))
    assert [counter.value(n) for n in range(4)] == [0, 1, 2, 3]


def test_a_counter_wraps_at_the_size_of_its_field():
    """A nibble holds sixteen, and saying so in the dialog as well would be
    one more thing to get wrong."""
    counter = Counter(at=Placement(byte=0, part=tx.LOW))
    assert counter.value(16) == 0
    assert counter.value(17) == 1


def test_a_counter_can_wrap_early():
    """Plenty of protocols count 0..14 in a nibble, or 0..5, and the field is
    bigger than the count."""
    counter = Counter(at=Placement(byte=0, part=tx.LOW), wrap=6)
    assert [counter.value(n) for n in range(8)] == [0, 1, 2, 3, 4, 5, 0, 1]


def test_a_counter_can_start_somewhere_else_and_step_by_more_than_one():
    counter = Counter(at=Placement(byte=0), start=10, step=2, wrap=20)
    assert [counter.value(n) for n in range(4)] == [10, 12, 14, 16]


# --- the two together, which is where the order matters ---------------------
def test_the_checksum_covers_the_counter_that_was_just_written():
    """The whole reason this module exists rather than two independent
    features. A checksum computed before the counter is stale on every
    frame, and looks perfectly healthy from the sending end."""
    counter = Counter(at=Placement(byte=0, part=tx.LOW))
    checksum = Checksum(at=Placement(byte=7), algorithm="sum8")
    base = bytes(8)

    for n in (0, 1, 2, 5):
        out = tx.apply(base, counter, checksum, sent=n)
        assert out[0] & 0x0F == n, "the counter did not land"
        assert out[7] == sum(out[:7]) & 0xFF, f"frame {n}: the checksum is stale"


def test_every_frame_differs_when_a_counter_is_in_use():
    """Which is the point. A receiver checking for a moving counter rejects
    a stream of identical frames, and that is what the pane sent before."""
    counter = Counter(at=Placement(byte=0, part=tx.LOW))
    checksum = Checksum(at=Placement(byte=7), algorithm="crc8_j1850")
    frames = {tx.apply(bytes(8), counter, checksum, sent=n) for n in range(16)}
    assert len(frames) == 16


def test_a_value_from_elsewhere_is_used_in_place_of_the_algorithm():
    """The escape hatch: a maker's own arithmetic comes from a hook, and is
    still written where the configuration says it goes."""
    checksum = Checksum(at=Placement(byte=7), algorithm="xor")
    out = tx.apply(bytes(8), None, checksum, computed=0x5A)
    assert out[7] == 0x5A


def test_neither_field_leaves_the_payload_alone():
    assert tx.apply(b"\x01\x02") == b"\x01\x02"


def test_the_message_keeps_its_length():
    """A transmitted message whose length changed would be a different
    message, and a receiver would reject it for that alone."""
    counter = Counter(at=Placement(byte=0))
    checksum = Checksum(at=Placement(byte=6, width=2), algorithm="crc16_ccitt")
    assert len(tx.apply(bytes(8), counter, checksum, sent=3)) == 8


# --- saved and loaded --------------------------------------------------------------------
def test_a_configuration_survives_being_written_down():
    """It lives in settings.json, so a transmit list restores with its
    counters still counting the same way."""
    counter = Counter(at=Placement(byte=1, part=tx.HIGH), start=2, step=3, wrap=12)
    checksum = Checksum(at=Placement(byte=6, width=2, endian=tx.LITTLE), algorithm="crc16_ccitt")

    assert tx.counter_from_dict(tx.counter_to_dict(counter)) == counter
    assert tx.checksum_from_dict(tx.checksum_to_dict(checksum)) == checksum


def test_nothing_configured_saves_as_nothing():
    assert tx.counter_to_dict(None) is None
    assert tx.checksum_from_dict(None) is None
    assert tx.checksum_from_dict({}) is None


def test_a_row_can_say_what_it_is_doing():
    counter = Counter(at=Placement(byte=1, part=tx.LOW))
    checksum = Checksum(at=Placement(byte=7), algorithm="crc8_2f")
    assert tx.describe(counter, checksum) == "count b1.lo, crc8_2f b7"
    assert tx.describe(None, None) == ""


# --- the same, addressed by database signal ------------------------------------
class FakeMessage:
    """A stand-in database message: signals at fixed byte positions.

    Deliberately not cantools. What is being tested here is the two-encode
    dance, not the packing -- a real database is exercised in
    test_tx_counters, where the pane has one.
    """

    def __init__(self, layout, length=8):
        self.layout = layout  # name -> byte index
        self.length = length
        self.encodes = 0

    def encode(self, values):
        self.encodes += 1
        out = bytearray(self.length)
        for name, index in self.layout.items():
            out[index] = int(values.get(name, 0)) & 0xFF
        return bytes(out)


def test_a_counter_in_a_signal_is_set_rather_than_patched():
    message = FakeMessage({"Speed": 0, "Alive": 6})
    counter = Counter(signal="Alive")
    out = tx.apply_signals(message.encode, {"Speed": 9}, counter, sent=3)
    assert out[6] == 3 and out[0] == 9


def test_a_signal_counter_takes_its_width_from_the_database():
    """A placement knows it is a nibble; a signal does not, so the database
    has to say -- otherwise a four-bit counter would count to 255 and the
    receiver would see it jump."""
    counter = Counter(signal="Alive")
    assert [counter.value(n, bits=4) for n in (15, 16, 17)] == [15, 0, 1]
    assert counter.value(16) == 16, "with nothing said, a byte"


def test_a_signal_checksum_is_hashed_with_itself_set_to_zero():
    """Not by leaving bytes out: a signal may share a byte with real data,
    and dropping the byte would drop that too."""
    message = FakeMessage({"A": 0, "B": 1, "Crc": 7})
    checksum = Checksum(signal="Crc", algorithm="sum8")
    out = tx.apply_signals(message.encode, {"A": 0x10, "B": 0x20}, checksum=checksum)
    assert out[7] == 0x30, "the sum of the frame with Crc zeroed"
    assert message.encodes == 2, "once to hash, once for real"


def test_the_signal_checksum_covers_the_signal_counter():
    """The ordering rule again, on the database path."""
    message = FakeMessage({"Alive": 0, "Crc": 7})
    counter = Counter(signal="Alive")
    checksum = Checksum(signal="Crc", algorithm="sum8")
    for n in (0, 1, 7):
        out = tx.apply_signals(message.encode, {}, counter, checksum, sent=n)
        assert out[0] == n
        assert out[7] == sum(out[:7]) & 0xFF, f"frame {n}: stale"


def test_a_hook_on_the_signal_path_sees_the_zeroed_frame():
    """The same bytes the named algorithms get, so a hook and an algorithm
    are interchangeable rather than subtly different."""
    message = FakeMessage({"Alive": 0, "Crc": 7})
    seen = []
    out = tx.apply_signals(
        message.encode,
        {},
        Counter(signal="Alive"),
        Checksum(signal="Crc"),
        sent=2,
        hook=lambda frame: seen.append(bytes(frame)) or 0x99,
    )
    assert seen and seen[0][0] == 2, "the counter was already in it"
    assert seen[0][7] == 0, "and its own field was not"
    assert out[7] == 0x99


def test_the_two_ways_of_addressing_are_exclusive():
    """A field cannot be in two places, and silently preferring one would be
    a configuration that does something other than it says."""
    with pytest.raises(FieldError, match="not both"):
        Counter(at=Placement(byte=0), signal="Alive")
    with pytest.raises(FieldError, match="not both"):
        Counter()


def test_using_the_wrong_function_for_the_kind_of_field_says_so():
    with pytest.raises(FieldError, match="use apply_signals"):
        tx.apply(bytes(8), Counter(signal="Alive"))
    with pytest.raises(FieldError, match="use apply"):
        tx.apply_signals(lambda v: bytes(8), {}, Counter(at=Placement(byte=0)))


def test_a_signal_configuration_survives_being_written_down():
    counter = Counter(signal="AliveCounter", start=1, wrap=15)
    checksum = Checksum(signal="Crc", algorithm="crc8_2f")
    assert tx.counter_from_dict(tx.counter_to_dict(counter)) == counter
    assert tx.checksum_from_dict(tx.checksum_to_dict(checksum)) == checksum


def test_a_row_says_the_signal_names_it_is_using():
    counter = Counter(signal="AliveCounter")
    checksum = Checksum(signal="Crc", algorithm="crc8_2f")
    assert tx.describe(counter, checksum) == "count AliveCounter, crc8_2f Crc"


# --- two fields in one place ------------------------------------------------------
def test_a_counter_and_a_checksum_on_the_same_byte_are_refused():
    """The checksum is written second, so the counter is put in and then
    stamped out -- every frame, with nothing wrong to see from this end."""
    counter = tx.Counter(at=tx.Placement(byte=6))
    checksum = tx.Checksum(at=tx.Placement(byte=6), algorithm="xor")
    assert "overlap" in tx.conflict(counter, checksum)
    with pytest.raises(tx.FieldError):
        tx.apply(b"\x00" * 8, counter, checksum)


def test_two_nibbles_of_one_byte_are_not_a_conflict():
    """Half a byte each is an arrangement people build on purpose."""
    counter = tx.Counter(at=tx.Placement(byte=6, part=tx.LOW))
    checksum = tx.Checksum(at=tx.Placement(byte=6, part=tx.HIGH), algorithm="xor")
    assert tx.conflict(counter, checksum) is None
    out = tx.apply(b"\x00" * 8, counter, checksum, sent=3)
    assert out[6] & 0x0F == 3, "the counter survived"


def test_a_two_byte_checksum_reaching_the_counter_is_refused():
    counter = tx.Counter(at=tx.Placement(byte=7))
    checksum = tx.Checksum(at=tx.Placement(byte=6, width=2), algorithm="crc16_ccitt")
    assert tx.conflict(counter, checksum) is not None


def test_one_signal_cannot_hold_both():
    both = "Rolling"
    assert tx.conflict(tx.Counter(signal=both), tx.Checksum(signal=both)) is not None
    assert tx.conflict(tx.Counter(signal=both), tx.Checksum(signal="Crc")) is None


def test_a_field_on_its_own_never_conflicts():
    counter = tx.Counter(at=tx.Placement(byte=6))
    assert tx.conflict(counter, None) is None
    assert tx.conflict(None, None) is None
