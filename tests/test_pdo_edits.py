# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""The PDO tab with a node: every edited PDO is marked and written, and an edit
not written is kept -- through a write of the others, a read that arrives from
elsewhere, and choosing another node."""

from __future__ import annotations

import copy

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QMessageBox

from pycangui.canopen import PdoConfig, PdoEntry
from pycangui.canopen.manager import ADDED_BY_HAND, CanopenManager
from pycangui.core.bus import BusManager
from pycangui.core.context import Context
from pycangui.ui import pdo_view, pre_operational
from pycangui.ui.pdo_view import COL_NAME, COL_TIMER, COL_TRANS, EDITED_COLOUR, PdoConfigView


class _Manager(QObject):
    """What the tab asks of the CANopen manager, with the node's PDOs held here."""

    pdo_config = Signal(int)
    pdo_written = Signal(int, list, list)

    def __init__(self) -> None:
        super().__init__()
        self.on_node = {
            node_id: [
                PdoConfig(
                    node_id,
                    "TPDO",
                    1,
                    "TPDO1",
                    0x180 + node_id,
                    True,
                    255,
                    entries=[PdoEntry(0x2000, 1, 16, "Speed")],
                ),
                PdoConfig(
                    node_id,
                    "RPDO",
                    1,
                    "RPDO1",
                    0x200 + node_id,
                    True,
                    255,
                    entries=[PdoEntry(0x2001, 0, 16, "Demand")],
                ),
            ]
            for node_id in (5, 6)
        }
        self.nmt_states: dict[int, str] = {}
        self.writes: list[list[str]] = []
        self.nmt_sent: list[tuple[int, str]] = []
        self.reads: list[int] = []

    def node(self, _node_id):
        return None

    def pdo_configs(self, node_id):
        return copy.deepcopy(self.on_node.get(node_id, []))

    def write_pdo_configs(self, node_id, configs):
        self.writes.append([f"{c.direction}{c.number}" for c in configs])
        self.last_written = (node_id, configs)

    def read_pdo_config(self, node_id):
        self.reads.append(node_id)

    def nmt(self, node_id, command):
        self.nmt_sent.append((node_id, command))

    def finish(self, refused: dict[str, str]) -> None:
        """The node takes all but the refused ones, and says so, as the real one does."""
        node_id, configs = self.last_written
        written = []
        for config in configs:
            name = f"{config.direction}{config.number}"
            if name in refused:
                continue
            written.append(name)
            stored = self.on_node[node_id]
            stored[[f"{c.direction}{c.number}" for c in stored].index(name)] = copy.deepcopy(config)
        self.pdo_written.emit(node_id, written, list(refused.items()))
        self.pdo_config.emit(node_id)


@pytest.fixture
def tab(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    manager = _Manager()
    widget = PdoConfigView(manager, Context(log=print))
    widget.set_node(5)
    widget.boxes = []
    monkeypatch.setattr(pdo_view.messages, "warning", lambda *a, **k: widget.boxes.append(a[1]))
    return widget


def row(tab, name):
    return next(
        tab.tree.topLevelItem(i)
        for i in range(tab.tree.topLevelItemCount())
        if tab.tree.topLevelItem(i).text(COL_NAME) == name
    )


def marked(tab) -> set[str]:
    return {
        tab.tree.topLevelItem(i).text(COL_NAME)
        for i in range(tab.tree.topLevelItemCount())
        if tab.tree.topLevelItem(i).background(COL_NAME).color() == EDITED_COLOUR
    }


def test_nothing_edited_nothing_to_write(tab):
    assert marked(tab) == set() and not tab.write_btn.isEnabled()


def test_every_edited_pdo_is_marked_and_written(tab):
    row(tab, "TPDO1").setText(COL_TIMER, "100")
    row(tab, "RPDO1").setText(COL_TRANS, "1")
    assert marked(tab) == {"TPDO1", "RPDO1"} and tab.write_btn.isEnabled()
    tab._write()
    assert tab.manager.writes == [["TPDO1", "RPDO1"]]


def test_one_refused_stays_edited_and_the_written_one_does_not(tab):
    row(tab, "TPDO1").setText(COL_TIMER, "100")
    row(tab, "RPDO1").setText(COL_TRANS, "1")
    tab._write()
    tab.manager.finish({"RPDO1": "refused"})
    assert marked(tab) == {"RPDO1"}
    assert row(tab, "RPDO1").text(COL_TRANS) == "1", "the edit is still there to try again"
    assert row(tab, "TPDO1").text(COL_TIMER) == "100", "as the node now has it"
    assert tab.boxes, "a refusal is said where it is seen"


def test_a_read_from_elsewhere_keeps_the_edits(tab):
    row(tab, "RPDO1").setText(COL_TRANS, "1")
    tab.manager.pdo_config.emit(5)  # Apply DCF or a boot-up read it again
    assert marked(tab) == {"RPDO1"} and row(tab, "RPDO1").text(COL_TRANS) == "1"


def test_another_node_and_back_keeps_the_edits(tab):
    row(tab, "RPDO1").setText(COL_TRANS, "1")
    tab.set_node(6)
    assert marked(tab) == set()
    tab.set_node(5)
    assert marked(tab) == {"RPDO1"} and row(tab, "RPDO1").text(COL_TRANS) == "1"


def test_unmapping_marks_it_edited(tab):
    tpdo = row(tab, "TPDO1")
    tab.tree.setCurrentItem(tpdo.child(0))
    tab._remove_entry()
    assert marked(tab) == {"TPDO1"}


@pytest.mark.parametrize(
    ("answer", "read", "kept"),
    [(QMessageBox.Cancel, [], {"RPDO1"}), (QMessageBox.Discard, [5], set())],
)
def test_reading_from_the_node_asks_before_losing_edits(tab, monkeypatch, answer, read, kept):
    row(tab, "RPDO1").setText(COL_TRANS, "1")
    monkeypatch.setattr(pdo_view.messages, "question", lambda *a, **k: answer)
    tab._read()
    assert tab.manager.reads == read
    tab.refresh()
    assert marked(tab) == kept


@pytest.mark.parametrize(
    ("answer", "nmt", "writes"),
    [
        (QMessageBox.Yes, [(5, "PRE-OPERATIONAL")], [["RPDO1"]]),
        (QMessageBox.No, [], [["RPDO1"]]),
        (QMessageBox.Cancel, [], []),
    ],
)
def test_an_operational_node_is_offered_pre_operational(tab, monkeypatch, answer, nmt, writes):
    tab.manager.nmt_states[5] = "OPERATIONAL"
    row(tab, "RPDO1").setText(COL_TRANS, "1")
    monkeypatch.setattr(pre_operational.messages, "question", lambda *a, **k: answer)
    tab._write()
    assert (tab.manager.nmt_sent, tab.manager.writes) == (nmt, writes)


def test_a_node_not_operational_is_not_asked(tab, monkeypatch):
    tab.manager.nmt_states[5] = "PRE-OPERATIONAL"
    asked = []
    monkeypatch.setattr(pre_operational.messages, "question", lambda *a, **k: asked.append(1))
    assert pre_operational.offer(tab, tab.manager, 5, "PDO changes") and not asked


# --- a node whose NMT state cannot be seen ---------------------------------------------


def test_a_node_with_no_heartbeat_says_why_its_state_is_not_known(app, tmp_path, monkeypatch):
    from pycangui.core.hooks import Hooks
    from pycangui.ui.canopen_view import CanopenView

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    ctx = Context(log=print)
    manager = CanopenManager(BusManager())
    view = CanopenView(manager, Hooks(ctx), ctx)
    try:
        view.on_node_seen(7, ADDED_BY_HAND)
        assert view._node_item(7).toolTip(2), "the State column says why it has no state"
        view.on_node_seen(7, "OPERATIONAL")
        assert not view._node_item(7).toolTip(2), "and stops once a heartbeat says"
    finally:
        manager.shutdown()


def test_heartbeat_off_is_said_once(app):
    manager = CanopenManager(BusManager())
    said = []
    manager.message.connect(lambda text, _level: said.append(text))
    try:
        manager.heartbeat_times[7] = 0
        manager._say_if_heartbeat_off(7)
        manager._say_if_heartbeat_off(7)
        manager.heartbeat_times[8] = 500
        manager._say_if_heartbeat_off(8)
        assert len(said) == 1
    finally:
        manager.shutdown()
