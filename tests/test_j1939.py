# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""J1939 helpers, DBC PGN matching, and the manager against the demo engine."""

import struct
import time

import pytest
from PySide6.QtCore import QCoreApplication

from pycangui import resources
from pycangui.core.bus import BusManager, Frame
from pycangui.core.context import Context
from pycangui.core.dbc import DbcDecoder
from pycangui.core.hooks import Hooks
from pycangui.j1939 import Dtc, Name, build_id, encode_dm1, parse_dm1, parse_id
from pycangui.j1939.manager import J1939Manager


def test_id_layout():
    mid = parse_id(0x0CF00400)
    assert (mid.priority, mid.pgn, mid.source, mid.destination) == (3, 61444, 0, 0xFF)
    mid = parse_id(0x18EA00F9)  # request from F9 to 00
    assert (mid.pgn, mid.source, mid.destination, mid.pdu1) == (59904, 0xF9, 0x00, True)
    assert build_id(61444, 0, priority=3) == 0x0CF00400
    assert build_id(59904, 0xF9, 0x00) == 0x18EA00F9
    assert parse_id(build_id(0x1FF10, 0x05)).pgn == 0x1FF10  # data page 1 survives


def test_dm1_round_trip():
    dtcs = [Dtc(110, 3, 5, 0), Dtc(520192, 31, 1, 0)]
    data = encode_dm1(dtcs, awl=1, mil=0)
    dm1 = parse_dm1(data)
    assert dm1.dtcs == tuple(dtcs)
    assert dm1.lamps() == "AWL"
    assert parse_dm1(encode_dm1([])).dtcs == ()


def test_name_fields():
    from pycangui.nodes.j1939_engine import ecu_name

    engine = ecu_name()
    n = Name.from_bytes(bytes(engine.bytes))
    assert n.identity_number == 0x1234 and n.manufacturer_code == 0 and n.function == 0
    assert n.industry_group == int(engine.industry_group)
    assert not n.arbitrary_address_capable


def test_dbc_matches_j1939_by_pgn():
    dbc = DbcDecoder()
    dbc.load(resources.path("demo.dbc"))
    rpm_raw = int(1500 / 0.125)
    f = Frame(
        0.0,
        "v",
        build_id(61444, 0x00, priority=3),
        True,
        False,
        True,
        b"\xff\xff\xff" + struct.pack("<H", rpm_raw) + b"\xff\xff\xff",
    )
    assert dbc.message_name(f) == "EEC1"
    _, values = dbc.decode(f)
    assert values["EngineSpeed"] == 1500
    f2 = Frame(
        0.0, "v", build_id(61444, 0x27, priority=6), True, False, True, f.data
    )  # other SA/prio
    assert dbc.message_name(f2) == "EEC1"
    assert dbc.message_name(Frame(0.0, "v", build_id(65226, 0), True, False, True, b"")) is None


# --- an address claim takes as long as it takes ------------------------------------
class FakeClaim:
    """A controller application whose state the test moves by hand."""

    def __init__(self, state, address=0xF9):
        self.state = state
        self.device_address = address

    def stop(self):
        pass


class FakeEcu:
    def remove_ca(self, _address):
        pass


@pytest.fixture
def claiming(app, tmp_path, monkeypatch):
    """A manager mid-claim, with no bus: only the checking is under test."""
    import j1939 as j1939lib

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    manager = J1939Manager(BusManager(), Hooks(Context(log=print)))
    claimed = []
    manager.claimed.connect(claimed.append)
    manager.ecu = FakeEcu()
    manager.ca = FakeClaim(j1939lib.ControllerApplication.State.WAIT_VETO)
    manager._claim_deadline = time.monotonic() + manager.CLAIM_DEADLINE_S
    manager._claim_timer.start()
    yield manager, claimed, j1939lib.ControllerApplication.State
    manager._claim_timer.stop()


def test_a_slow_claim_is_waited_for_not_given_up_on(claiming):
    """The Intel Mac runner answered a single look at 600 ms before the J1939
    library had, and the claim was released as failed. Still undecided means look again."""
    manager, claimed, states = claiming
    for _ in range(5):
        manager._check_claim()
    assert claimed == [] and manager.ca is not None, "undecided is not failed"
    assert manager._claim_timer.isActive()

    manager.ca.state = states.NORMAL
    manager._check_claim()
    assert claimed == [0xF9]
    assert not manager._claim_timer.isActive(), "reported once, then quiet"


def test_an_address_in_use_fails_at_once(claiming):
    manager, claimed, states = claiming
    manager.ca.state = states.CANNOT_CLAIM
    manager._check_claim()
    assert claimed == [0xFE] and manager.ca is None
    assert not manager._claim_timer.isActive()


def test_a_claim_that_never_resolves_gives_up_at_the_deadline(claiming):
    manager, claimed, _states = claiming
    manager._claim_deadline = time.monotonic() - 1
    manager._check_claim()
    assert claimed == [0xFE] and manager.ca is None


def test_releasing_stops_the_checking(claiming):
    manager, claimed, _states = claiming
    manager.release_address()
    assert claimed == [0xFE] and not manager._claim_timer.isActive()


def wait_until(pred, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not pred():
        QCoreApplication.processEvents()
        if time.monotonic() > deadline and not pred():
            raise AssertionError("timed out")
        time.sleep(0.005)


@pytest.fixture
def stack(app, tmp_path, monkeypatch, demo_device):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    ctx = Context(log=print)
    bus = BusManager()
    manager = J1939Manager(bus, Hooks(ctx))
    bus.connect_bus("virtual", "vcan_j1939", 500000, False)
    demo = demo_device(bus, kinds=["j1939_engine"])
    # Not before the engine has its address. It claims one half a second after
    # it starts, on a thread of its own, and like any J1939 node it says nothing
    # to a request until then -- nor afterwards, since a request is sent once.
    # A test that asked straight after its own claim usually came second, and
    # on a busy machine came first and waited for an answer that never came.
    engine = demo["j1939_engine"].state
    wait_until(lambda: engine.ca.state == engine.library.ControllerApplication.State.NORMAL)
    yield bus, manager, demo
    manager.shutdown()
    bus.disconnect_bus()


def test_manager_against_demo_engine(stack):
    _bus, manager, _demo = stack
    nodes, dm1s, msgs, claimed = [], [], [], []
    manager.node_seen.connect(lambda sa, name: nodes.append((sa, name)))
    manager.dm1.connect(lambda sa, d: dm1s.append((sa, d)))
    manager.message.connect(lambda p, pgn, sa, t, data: msgs.append((pgn, sa, data)))
    manager.claimed.connect(claimed.append)

    wait_until(lambda: any(name is not None for _, name in nodes))  # address claim seen
    sa, name = next((sa, n) for sa, n in nodes if n is not None)
    assert sa == 0 and name.identity_number == 0x1234 and name.manufacturer_code == 0
    wait_until(lambda: any(pgn == 61444 for pgn, _, _ in msgs))
    wait_until(lambda: dm1s, timeout=3)
    assert dm1s[0][1].dtcs[0].spn == 110 and dm1s[0][1].lamps() == "AWL"

    manager.claim_address(0xF9)
    wait_until(lambda: claimed, timeout=manager.CLAIM_DEADLINE_S + 2)
    assert claimed[-1] == 0xF9 and manager.own_address == 0xF9

    manager.request_pgn(65259, 0x00)  # ComponentID -> BAM multi-packet
    wait_until(lambda: any(pgn == 65259 for pgn, _, _ in msgs), timeout=5)
    _, sa, data = next(m for m in msgs if m[0] == 65259)
    assert sa == 0 and data == b"PYCANGUI*DEMO ENGINE*SN0001*UNIT1*"

    f = Frame(0.0, "v", build_id(61444, 0x00, priority=3), True, False, True, b"")
    assert manager.classify(f) == "EEC1 SA 00"
    assert (
        manager.classify(Frame(0.0, "v", 0x18EA00F9, True, False, True, b"")) == "Request SA F9->00"
    )

    manager.release_address()
    assert claimed[-1] == 0xFE


# --- requests from the list, their answers, and DM13 ---------------------------------------
def test_the_listed_requests_are_answered_and_decoded(stack):
    """DM2 used to arrive on the dm1 signal and be shown as active."""
    from pycangui.j1939 import PGN_DM2, PGN_DM3, PGN_DM5, PGN_ECU_ID, PGN_SOFTWARE_ID

    _bus, manager, _demo = stack
    active, previous, said, claimed = [], [], [], []
    manager.dm1.connect(lambda sa, d: active.append(d))
    manager.dm2.connect(lambda sa, d: previous.append(d))
    manager.log.connect(said.append)
    manager.claimed.connect(claimed.append)
    manager.claim_address(0xF9)
    wait_until(lambda: claimed, timeout=manager.CLAIM_DEADLINE_S + 2)

    manager.request_pgn(PGN_DM2, 0x00)
    wait_until(lambda: previous)
    assert previous[0].dtcs[0].spn == 190, "a previously active fault, and not on dm1"
    assert all(d.dtcs[0].spn == 110 for d in active if d.dtcs)

    manager.request_pgn(PGN_ECU_ID, 0x00)
    wait_until(lambda: any("Part number: PN-1000" in line for line in said))
    manager.request_pgn(PGN_SOFTWARE_ID, 0x00)
    wait_until(lambda: any("Software 2: BOOT 2.1" in line for line in said))
    manager.request_pgn(PGN_DM5, 0x00)
    wait_until(lambda: any("DM5 readiness: 1 active, 1 previously active" in s for s in said))

    manager.request_pgn(PGN_DM3, 0x00)
    wait_until(lambda: any("clearing previously active faults accepted" in s for s in said))
    previous.clear()
    manager.request_pgn(PGN_DM2, 0x00)
    wait_until(lambda: previous)
    assert previous[0].dtcs == (), "and it really did clear them"


def test_stopping_broadcasts_holds_until_started(app, tmp_path, monkeypatch):
    from pycangui.j1939 import DM13_HOLD, DM13_START, DM13_STOP

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    bus = BusManager()
    manager = J1939Manager(bus, Hooks(Context(log=print)))
    sent: list[bytes] = []
    monkeypatch.setattr(bus, "send", lambda can_id, data, **k: sent.append(bytes(data)))
    manager._hold_timer.setInterval(20)
    states = []
    manager.broadcasts_stopped.connect(states.append)

    manager.stop_broadcasts()
    assert sent == [DM13_STOP] and manager.holding_broadcasts
    wait_until(lambda: sent.count(DM13_HOLD) >= 2, timeout=2)
    manager.start_broadcasts()
    assert sent[-1] == DM13_START and not manager.holding_broadcasts
    assert states == [True, False]


def test_a_clear_asks_first_whether_picked_or_typed(app, tmp_path, monkeypatch):
    from pycangui.ui.j1939_view import J1939View

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    ctx = Context(log=print)
    manager = J1939Manager(BusManager(), Hooks(ctx))
    view = J1939View(manager, ctx)
    asked, requested = [], []
    monkeypatch.setattr(view.confirm, "ask", lambda *a: asked.append(a) or False)
    monkeypatch.setattr(manager, "request_pgn", lambda *a: requested.append(a))
    # As the tester, which here is already so: no claim to wait for.
    monkeypatch.setattr(manager, "as_tester", lambda address, action: action())

    view.req_pgn.setCurrentText("FED3")  # DM11, typed rather than chosen
    view._request()
    assert asked and requested == [], "declined, so nothing is sent"
    view.req_pgn.setCurrentIndex(0)  # DM1 reads, and does not ask
    view._request()
    assert requested == [(0xFECA, 0xFF)]


def test_every_request_says_what_came_of_it(stack, monkeypatch):
    """An empty DM2 changed nothing on screen, so an answer and no answer
    looked the same."""
    from pycangui.j1939 import PGN_DM1, PGN_DM2, PGN_DM3

    _bus, manager, _demo = stack
    said, claimed = [], []
    manager.log.connect(said.append)
    manager.claimed.connect(claimed.append)
    manager.claim_address(0xF9)
    wait_until(lambda: claimed, timeout=manager.CLAIM_DEADLINE_S + 2)

    manager.request_pgn(PGN_DM3, 0x00)
    wait_until(lambda: any("clearing previously active faults accepted" in s for s in said))
    manager.request_pgn(PGN_DM2, 0x00)
    wait_until(
        lambda: any("DM2 previously active faults: no previously active faults" in s for s in said)
    )

    # Answered once, though the engine broadcasts DM1 every second.
    manager.request_pgn(PGN_DM1, 0x00)
    wait_until(lambda: any("DM1 active faults: 1 active fault(s)" in s for s in said))
    deadline = time.monotonic() + manager.RESPONSE_S + 0.3
    while time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    assert sum("DM1 active faults:" in s for s in said) == 1

    manager.request_address_claims()
    wait_until(lambda: any("J1939 00 claims its address" in s for s in said))


def test_a_request_nothing_answers_says_so(stack, monkeypatch):
    _bus, manager, _demo = stack
    monkeypatch.setattr(manager, "RESPONSE_S", 0.3)
    said, warned = [], []
    manager.log.connect(said.append)
    manager.problem.connect(warned.append)
    manager.request_pgn(0xFEE5, 0x55)  # nobody at 55
    wait_until(lambda: warned, timeout=2)
    assert len(warned) == 1 and not said, "a warning, since what was asked did not happen"


def test_a_request_goes_out_from_the_tester_address_claiming_it_first(stack):
    """It went out from FE until the address was claimed, whatever the pane said."""
    from pycangui.j1939 import PGN_ECU_ID, parse_id

    bus, manager, _demo = stack
    sent, said = [], []
    bus.frames.connect(lambda frames: sent.extend(f for f in frames if not f.rx))
    manager.log.connect(said.append)

    manager.as_tester(0xF9, lambda: manager.request_pgn(PGN_ECU_ID, 0x00))
    wait_until(lambda: any("Part number: PN-1000" in s for s in said), timeout=6)
    requests = [parse_id(f.can_id) for f in sent if parse_id(f.can_id).pgn == 59904]
    assert requests and all(r.source == 0xF9 for r in requests), "not from FE"
    assert any("claiming F9 first" in s for s in said)


def test_an_answer_sent_to_fe_is_reported_but_not_decoded(app, tmp_path, monkeypatch):
    """Seen on a real ECU: a request from FE answered with a transfer to FE,
    which J1939-21 does not allow. It is the ECU's bug, but it did answer."""
    from pycangui.core.bus import Frame
    from pycangui.j1939 import PGN_ECU_ID

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    bus = BusManager()
    monkeypatch.setattr(bus, "send", lambda *a, **k: None)
    manager = J1939Manager(bus, Hooks(Context(log=print)))
    monkeypatch.setattr(manager, "RESPONSE_S", 0.2)
    said, problems = [], []
    manager.log.connect(said.append)
    manager.problem.connect(problems.append)
    manager.request_pgn(PGN_ECU_ID, 0x71)

    announce = Frame(0.0, "CAN 1", 0x1CECFE71, True, False, True, bytes.fromhex("1040000AFFC5FD00"))
    packet = Frame(0.0, "CAN 1", 0x1CEBFE71, True, False, True, bytes.fromhex("012A313831323130"))
    manager._on_frames([announce, packet])
    wait_until(lambda: problems)
    time.sleep(0.3)
    QCoreApplication.processEvents()

    assert "answered ECU identification with 64 bytes addressed to FE" in problems[0]
    assert not any("no answer" in s for s in said), "it did answer"
    assert not any("Part number" in s for s in said), "and it is not decoded"
