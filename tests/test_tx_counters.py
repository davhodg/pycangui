"""Counters and checksums as the transmit pane uses them.

The arithmetic is tested in test_tx_fields; these are about the pane doing
the right thing with it -- which frames actually leave, on which timer, and
whether the configuration survives being saved.

The one that matters most is the timer.  A message with a counter cannot go
through the adapter's cyclic task: that repeats fixed bytes, and every frame
of a counted message has to differ.  If that ever regresses the frames still
go out and still look plausible, and only the device at the other end knows.
"""

import time

import can
import pytest
from PySide6.QtCore import Qt

from pycangui.core import tx_fields as tx
from pycangui.core.bus import BusManager
from pycangui.core.context import Context
from pycangui.core.dbc import DbcDecoder
from pycangui.ui.tx_view import COL_CYCLIC, COL_FIELDS, TxView

CHANNEL = "vtxcount"


@pytest.fixture
def view(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    bus = BusManager()
    bus.connect_bus("virtual", CHANNEL, 500000, False)
    made = TxView(bus, Context(), DbcDecoder(), canopen=None)
    yield made
    made.stop_all()
    bus.disconnect_bus()


@pytest.fixture
def listening():
    other = can.Bus(interface="virtual", channel=CHANNEL)
    yield other
    other.shutdown()


COUNTER = {"at": {"byte": 0, "part": "low"}, "start": 0, "step": 1, "wrap": 0}
CHECKSUM = {"at": {"byte": 7, "part": "all", "width": 1}, "algorithm": "sum8"}


def a_row(view, **extra):
    spec = {"kind": "raw", "id": "100", "data": "00 00 00 00 00 00 00 00", "period": 20}
    spec.update(extra)
    return view.add_message(spec)


def collect(app, bus, wanted, seconds=2.0):
    """Frames seen, until there are ``wanted`` of them or time runs out."""
    got = []
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and len(got) < wanted:
        app.processEvents()
        if (msg := bus.recv(timeout=0.01)) is not None:
            got.append(msg)
    return got


# --- one-shot sends ----------------------------------------------------------------------
def test_a_counted_message_moves_on_every_manual_send(app, view, listening):
    """A receiver does not know which button sent a frame.  A manual send
    that repeated the last count would be rejected like any other repeat."""
    row = a_row(view, counter=COUNTER)
    for _ in range(3):
        view.send_row(row)
    got = collect(app, listening, 3)
    assert [m.data[0] & 0x0F for m in got] == [0, 1, 2]


def test_the_checksum_is_over_the_counter_that_was_just_written(app, view, listening):
    """The ordering, end to end: a checksum computed before the counter is
    stale on every frame and looks perfectly healthy from here."""
    row = a_row(view, counter=COUNTER, checksum=CHECKSUM)
    for _ in range(3):
        view.send_row(row)
    for msg in collect(app, listening, 3):
        assert msg.data[7] == sum(msg.data[:7]) & 0xFF


def test_a_message_with_neither_is_sent_exactly_as_typed(app, view, listening):
    row = a_row(view, data="01 02 03")
    view.send_row(row)
    got = collect(app, listening, 1)
    assert bytes(got[0].data) == b"\x01\x02\x03"


# --- which timer, which is the thing that must not regress ------------------------
def test_a_plain_message_uses_the_adapter_cyclic_task(app, view):
    """Unchanged behaviour, and worth keeping: some adapters time these in
    hardware, which is steadier than anything a GUI can do."""
    row = a_row(view)
    view.item(row).setCheckState(COL_CYCLIC, Qt.Checked)
    assert row in view._tasks and row not in view._timers


def test_a_counted_message_is_timed_by_pycangui_instead(app, view):
    """It has to be: the adapter's task repeats fixed bytes, and modifying a
    free-running one races it -- some frames would repeat a count and some
    would skip one, which is what the receiver is checking for."""
    row = a_row(view, counter=COUNTER)
    view.item(row).setCheckState(COL_CYCLIC, Qt.Checked)
    assert row in view._timers and row not in view._tasks


def test_a_checksum_alone_is_enough_to_take_it_off_the_adapter(app, view):
    """The bytes are fixed, so the adapter could manage -- but the hook may
    return a different value each time, and one rule is easier to trust than
    a rule with an exception in it."""
    row = a_row(view, checksum=CHECKSUM)
    view.item(row).setCheckState(COL_CYCLIC, Qt.Checked)
    assert row in view._timers


def test_both_kinds_count_as_cyclic_and_both_stop(app, view):
    """Stop all cyclic says all.  A big red stop that stopped half of what
    was going onto a live bus would be the worst kind of wrong."""
    plain = a_row(view)
    counted = a_row(view, counter=COUNTER)
    for row in (plain, counted):
        view.item(row).setCheckState(COL_CYCLIC, Qt.Checked)
    assert view.cyclic_count() == 2

    view.stop_all()
    assert view.cyclic_count() == 0
    assert view.item(plain).checkState(COL_CYCLIC) == Qt.Unchecked
    assert view.item(counted).checkState(COL_CYCLIC) == Qt.Unchecked


def test_every_frame_of_a_cycling_counted_message_differs(app, view, listening):
    """The whole point, on the wire rather than in a unit test."""
    row = a_row(view, counter=COUNTER, checksum=CHECKSUM)
    view.item(row).setCheckState(COL_CYCLIC, Qt.Checked)
    got = collect(app, listening, 5, seconds=3.0)
    assert len(got) >= 3, f"only {len(got)} frames arrived"
    counts = [m.data[0] & 0x0F for m in got]
    assert counts == sorted(counts), f"the counter went backwards: {counts}"
    assert len(set(counts)) == len(counts), f"a count repeated: {counts}"


# --- the bespoke escape hatch ------------------------------------------------------
class FakeHooks:
    """Stands in for the hook registry, answering only transmit.checksum."""

    def __init__(self, answer):
        self.answer = answer
        self.seen = []

    def call(self, module, name, *args, **_kwargs):
        assert (module, name) == ("transmit", "checksum")
        self.seen.append(args)
        return self.answer


def test_a_hook_decides_the_arithmetic_and_the_dialog_decides_the_place(app, view, listening):
    """A maker's checksum is nobody's standard, so the escape hatch has to
    exist -- but where the value goes is still the pane's business."""
    view.hooks = FakeHooks(0x5A)
    row = a_row(view, checksum=CHECKSUM)
    view.send_row(row)
    got = collect(app, listening, 1)
    assert got[0].data[7] == 0x5A


def test_the_hook_sees_the_counter_already_written(app, view, listening):
    """Otherwise it would be computing over bytes that are about to change,
    which is the same trap the built-in ordering avoids."""
    hooks = FakeHooks(0x00)
    view.hooks = hooks
    row = a_row(view, counter=COUNTER, checksum=CHECKSUM)
    view.send_row(row)
    view.send_row(row)
    collect(app, listening, 2)
    counts = [payload[0] & 0x0F for _name, _can_id, payload in hooks.seen]
    assert counts == [0, 1], f"the hook saw {counts}"


def test_no_hook_means_the_named_algorithm(app, view, listening):
    """A pane built by a plugin, or one in a test, has no hooks at all."""
    assert view.hooks is None
    row = a_row(view, checksum=CHECKSUM)
    view.send_row(row)
    got = collect(app, listening, 1)
    assert got[0].data[7] == 0


# --- the list remembers ------------------------------------------------------------------
def test_the_configuration_is_saved_with_the_row(app, view):
    row = a_row(view, counter=COUNTER, checksum=CHECKSUM)
    spec = view._spec(row)
    assert spec["counter"] == COUNTER
    assert tx.checksum_from_dict(spec["checksum"]).algorithm == "sum8"


def test_a_restored_row_says_what_it_is_doing(app, view):
    """The column exists so that a list nobody has looked at for a week still
    says which messages are computing something."""
    row = a_row(view, counter=COUNTER, checksum=CHECKSUM)
    assert view.item(row).text(COL_FIELDS) == "count b0.lo, sum8 b7"


def test_a_row_with_neither_says_nothing(app, view):
    assert view.item(a_row(view)).text(COL_FIELDS) == ""


# --- named by database signal rather than counted to ------------------------------
DBC_COUNTER = {"signal": "PumpEnable", "start": 0, "step": 1, "wrap": 0}
DBC_CHECKSUM = {"signal": "PumpSpeedDemand", "algorithm": "sum8"}


@pytest.fixture
def dbc_view(app, tmp_path, monkeypatch):
    """A pane with the demo database loaded, on its own channel."""
    from pycangui import resources

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    bus = BusManager()
    bus.connect_bus("virtual", "vtxdbc", 500000, False)
    decoder = DbcDecoder()
    decoder.load(str(resources.path("demo.dbc")))
    made = TxView(bus, Context(), decoder, canopen=None)
    yield made
    made.stop_all()
    bus.disconnect_bus()


@pytest.fixture
def dbc_listening():
    other = can.Bus(interface="virtual", channel="vtxdbc")
    yield other
    other.shutdown()


def a_dbc_row(view, **extra):
    spec = {
        "kind": "dbc",
        "message": "PumpCommand",
        "id": "123",
        "period": 20,
        "signals": {"PumpEnable": "0", "PumpSpeedDemand": "0"},
    }
    spec.update(extra)
    return view.add_message(spec)


def decode(view, msg):
    return view.dbc.message_by_name("PumpCommand").decode(bytes(msg.data))


def test_a_counter_in_a_signal_is_packed_by_the_database(app, dbc_view, dbc_listening):
    """PumpEnable is one bit.  Nothing here counts bytes or nibbles -- the
    database knows where that bit lives, which is the whole argument for
    naming a field instead of placing it."""
    row = a_dbc_row(dbc_view, counter=DBC_COUNTER)
    for _ in range(4):
        dbc_view.send_row(row)
    got = collect(app, dbc_listening, 4)
    assert [decode(dbc_view, m)["PumpEnable"] for m in got] == [0, 1, 0, 1]


def test_a_one_bit_counter_wraps_at_two(app, dbc_view, dbc_listening):
    """The width came from the database.  Without it the counter would run
    to 255 and the encode would fail, or worse, silently truncate."""
    row = a_dbc_row(dbc_view, counter=DBC_COUNTER)
    counter, _checksum = dbc_view.fields(row)
    assert counter.modulus(bits=1) == 2


def test_a_checksum_in_a_signal_is_computed_with_itself_zeroed(app, dbc_view, dbc_listening):
    """Not by leaving bytes out: a signal can share a byte with data that
    has to survive, so whole-byte exclusion would drop it."""
    row = a_dbc_row(
        dbc_view,
        signals={"PumpEnable": "1", "PumpSpeedDemand": "0"},
        checksum=DBC_CHECKSUM,
    )
    dbc_view.send_row(row)
    got = collect(app, dbc_listening, 1)
    values = decode(dbc_view, got[0])
    # With the checksum signal zeroed the frame is PumpEnable alone, so the
    # 8-bit sum of those bytes is 1.
    assert values["PumpSpeedDemand"] == 1


def test_the_signal_checksum_covers_the_signal_counter(app, dbc_view, dbc_listening):
    """The ordering rule, through a real database and a real encoder."""
    row = a_dbc_row(dbc_view, counter=DBC_COUNTER, checksum=DBC_CHECKSUM)
    for _ in range(4):
        dbc_view.send_row(row)
    got = collect(app, dbc_listening, 4)
    seen = [decode(dbc_view, m) for m in got]
    assert [v["PumpEnable"] for v in seen] == [0, 1, 0, 1]
    # Each checksum is the sum of that frame's bytes with the checksum
    # signal zeroed, which for this message is the counter bit alone.
    assert [v["PumpSpeedDemand"] for v in seen] == [0, 1, 0, 1]


def test_a_named_row_is_still_timed_by_pycangui(app, dbc_view):
    row = a_dbc_row(dbc_view, counter=DBC_COUNTER)
    dbc_view.item(row).setCheckState(COL_CYCLIC, Qt.Checked)
    assert row in dbc_view._timers and row not in dbc_view._tasks


def test_a_row_says_which_signals_it_is_using(app, dbc_view):
    row = a_dbc_row(dbc_view, counter=DBC_COUNTER, checksum=DBC_CHECKSUM)
    assert dbc_view.item(row).text(COL_FIELDS) == "count PumpEnable, sum8 PumpSpeedDemand"


def test_a_message_missing_from_the_database_sends_what_it_had(app, dbc_view, dbc_listening):
    """A database can be unloaded or replaced while a list still refers to
    it.  Saying so and sending the last known bytes beats sending nothing
    and beats a traceback."""
    row = a_dbc_row(dbc_view, message="NotInTheDatabase", counter=DBC_COUNTER)
    dbc_view.send_row(row)
    assert collect(app, dbc_listening, 1), "nothing was sent at all"
