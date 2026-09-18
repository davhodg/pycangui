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
    assert n.identity_number == 0x1234 and n.manufacturer_code == 66 and n.function == 0
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
    """The Intel Mac runner answered a single look at 600 ms before can-j1939
    had, and the claim was released as failed. Still undecided means look again."""
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
        if time.monotonic() > deadline:
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
    assert sa == 0 and name.manufacturer_code == 66
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
