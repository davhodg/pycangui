# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Polling objects ticked in the CANopen object dictionary, into Signals and Plot."""

import pytest
from PySide6.QtCore import QObject, Qt, Signal

from pycangui.canopen.display import Display
from pycangui.core.signals import SignalHub
from pycangui.ui.od_poll import SETTING, DictionaryPoll


class Settings(dict):
    def set(self, key, value):
        self[key] = value


class Manager(QObject):
    """Just what the polling asks of a CANopen manager."""

    identified = Signal(object)
    node_back = Signal(int)
    node_lost = Signal(int)
    node_seen = Signal(int, str)

    def __init__(self, nodes=(5,)):
        super().__init__()
        self.known = list(nodes)
        self.lost_nodes: set[int] = set()
        self.stopped: set[int] = set()
        self.reads: list[tuple[int, int, int]] = []

    def nodes(self):
        return list(self.known)

    def is_stopped(self, node_id):
        return node_id in self.stopped

    def display(self, node_id, index, sub):
        return Display(name=f"Object {index:04X}", unit="rpm")

    def sdo_read(self, node_id, index, sub):
        self.reads.append((node_id, index, sub))


@pytest.fixture
def made(app):
    manager = Manager(nodes=(5, 7))
    settings = Settings()
    hub = SignalHub()
    poll = DictionaryPoll(manager, settings, lambda n: f"node{n}", signals=hub, now=lambda: 1.0)
    yield poll, manager, settings, hub
    poll.stop()


def answer_all(poll, manager, value=100):
    asked, manager.reads = manager.reads, []
    for node_id, index, sub in asked:
        poll.answer(node_id, index, sub, value, None)
    return asked


def test_ticked_objects_are_kept_per_device(made):
    poll, _manager, settings, _hub = made
    poll.set_polled(5, (0x6041, 0), True)
    poll.set_polled(5, (0x606C, 0), True)
    assert poll.polled(5) == {(0x6041, 0), (0x606C, 0)}
    assert poll.polled(7) == set()
    assert settings[SETTING] == {"node5": [[0x6041, 0], [0x606C, 0]]}
    poll.set_polled(5, (0x6041, 0), False)
    poll.set_polled(5, (0x606C, 0), False)
    assert settings[SETTING] == {}, "an empty list is not stored"


def test_nothing_is_read_until_polling_is_turned_on(made):
    poll, manager, _settings, _hub = made
    poll.set_polled(5, (0x6041, 0), True)
    assert manager.reads == []
    poll.start()
    assert manager.reads == [(5, 0x6041, 0)]
    assert poll.nodes() == [5], "only nodes with something ticked"


def test_a_round_answered_starts_the_next_and_the_values_go_to_signals(made, app):
    from PySide6.QtTest import QTest

    poll, manager, _settings, hub = made
    poll.set_rate(50)
    poll.set_polled(5, (0x606C, 0), True)
    poll.start()
    answer_all(poll, manager, value=1500)
    series = hub.get("Node 5/Object 606C")
    assert series is not None and series.latest == 1500 and series.unit == "rpm"
    QTest.qWait(60)  # the next round, no sooner than the rate allows
    assert manager.reads == [(5, 0x606C, 0)]


def test_a_read_by_hand_is_not_taken_for_polling(made):
    poll, manager, _settings, hub = made
    poll.set_polled(5, (0x606C, 0), True)
    poll.start()
    manager.reads.clear()
    poll.note_read(5, 0x2001, 0)  # not polled at all
    assert not poll.answer(5, 0x2001, 0, 7, None)
    assert hub.get("Node 5/Object 2001") is None, "a reading by hand is not a trace"
    assert poll.answer(5, 0x606C, 0, 1, None), "the poll's own answer is"


def test_a_stopped_or_lost_node_is_left_alone_until_it_is_back(made):
    poll, manager, _settings, _hub = made
    poll.set_polled(5, (0x606C, 0), True)
    poll.set_polled(7, (0x606C, 0), True)
    manager.stopped.add(7)
    poll.start()
    assert poll.nodes() == [5]
    manager.stopped.discard(7)
    manager.node_seen.emit(7, "OPERATIONAL")
    assert poll.nodes() == [5, 7]
    manager.lost_nodes.add(5)
    manager.node_lost.emit(5)
    assert poll.nodes() == [7]
    manager.lost_nodes.discard(5)
    manager.node_back.emit(5)
    assert poll.nodes() == [5, 7]


def test_a_pause_holds_the_rounds_and_stop_ends_them(made, app):
    from PySide6.QtTest import QTest

    poll, manager, _settings, _hub = made
    poll.set_rate(50)
    poll.set_polled(5, (0x606C, 0), True)
    poll.start()
    poll.set_paused(True)
    answer_all(poll, manager)
    QTest.qWait(60)
    assert manager.reads == [], "nothing new while paused"
    poll.set_paused(False)
    assert manager.reads == [(5, 0x606C, 0)]
    poll.stop()
    assert not poll.running and poll.nodes() == []


# --- in the CANopen pane ----------------------------------------------------------------
@pytest.fixture
def pane(app, tmp_path, monkeypatch):
    import canopen

    from pycangui import resources
    from pycangui.canopen import NodeIdentity
    from pycangui.canopen.manager import CanopenManager
    from pycangui.core.bus import BusManager
    from pycangui.core.context import Context
    from pycangui.core.hooks import Hooks
    from pycangui.ui.canopen_view import CanopenView

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    ctx = Context(log=print)
    bus = BusManager()
    manager = CanopenManager(bus)
    view = CanopenView(manager, Hooks(ctx), ctx, signals=SignalHub(), now=lambda: 0.0)
    node = canopen.RemoteNode(5, str(resources.path("demo.eds")))
    view._identities[5] = NodeIdentity(5, vendor_id=1, product_code=2, revision=3)
    monkeypatch.setattr(view.manager, "node", lambda _n, node=node: node)
    view._populate_od(5)
    view._od_node = 5
    yield view
    view.poll_ticked.setChecked(False)
    bus.disconnect_bus()


def od_item(view, index, sub):
    return view._od_item(index, sub)


def test_the_poll_column_ticks_objects_apart_from_bookmarks(pane):
    from pycangui.ui.canopen_view import COL_BOOKMARK, COL_POLL

    item = od_item(pane, 0x1018, 1)
    item.setCheckState(COL_POLL, Qt.Checked)
    assert pane.sdo_poll.polled(5) == {(0x1018, 1)}
    assert pane._bookmarked(5) == set(), "polling is not bookmarking"
    item.setCheckState(COL_BOOKMARK, Qt.Checked)
    item.setCheckState(COL_POLL, Qt.Unchecked)
    assert pane.sdo_poll.polled(5) == set() and pane._bookmarked(5) == {(0x1018, 1)}


def test_stop_all_takes_the_poll_tick_off(pane):
    pane.poll_ticked.setChecked(True)
    assert pane.sdo_poll.running
    pane.ctx.pause.stop_all()
    assert not pane.poll_ticked.isChecked() and not pane.sdo_poll.running


def test_a_failing_polled_read_is_said_once_until_it_reads_again(pane, monkeypatch):
    said = []
    monkeypatch.setattr(pane.ctx, "warn", said.append)
    asked = []
    monkeypatch.setattr(pane.manager, "sdo_read", lambda *a: asked.append(a))
    monkeypatch.setattr(pane.manager, "nodes", lambda: [5])
    pane.sdo_poll.set_polled(5, (0x1018, 1), True)
    pane.poll_ticked.setChecked(True)
    for _ in range(3):
        pane.sdo_poll._read(5, 0x1018, 1)
        pane.on_sdo_result(5, 0x1018, 1, None, "timed out")
    assert len(said) == 1
    pane.sdo_poll._read(5, 0x1018, 1)
    pane.on_sdo_result(5, 0x1018, 1, 7, None)
    pane.sdo_poll._read(5, 0x1018, 1)
    pane.on_sdo_result(5, 0x1018, 1, None, "timed out")
    assert len(said) == 2, "failing again after reading is news"
