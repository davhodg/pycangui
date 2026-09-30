# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Read PDO config: how a remapped node's PDOs reach Transmit and the plot.

The manager could read them and CAN Transmit told people to press the button,
but the button itself was never put in the pane -- so the one way to offer a
remapped node's RPDOs was a control that did not exist. It read the RPDOs
only, as *Read RPDO config*, so a remapped TPDO went on being decoded into
Signals and Plot as the EDS had it; it reads both now.
"""

import pytest

from pycangui.canopen.manager import CanopenManager
from pycangui.core.bus import BusManager
from pycangui.core.context import Context
from pycangui.core.hooks import Hooks
from pycangui.ui.canopen_view import CanopenView


@pytest.fixture
def view(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    ctx = Context(log=print)
    bus = BusManager()
    widget = CanopenView(CanopenManager(bus), Hooks(ctx), ctx)
    yield widget
    bus.disconnect_bus()


def test_the_button_is_in_the_pane(view):
    assert view.read_pdos_btn.isVisibleTo(view), "a button nobody can reach is no button"


def test_it_reads_the_selected_node(view, monkeypatch):
    asked = []
    monkeypatch.setattr(view.manager, "read_pdo_config", asked.append)
    monkeypatch.setattr(view, "selected_node", lambda: 5)
    view._offer_node_buttons()  # the row follows the selection, so say there is one
    view.read_pdos_btn.click()
    assert asked == [5]


def test_without_a_node_selected_it_does_nothing(view, monkeypatch):
    asked = []
    monkeypatch.setattr(view.manager, "read_pdo_config", asked.append)
    monkeypatch.setattr(view, "selected_node", lambda: None)
    view.read_pdos_btn.click()
    assert asked == []


class FakeMap:
    def __init__(self, name, enabled):
        self.name, self.cob_id, self.enabled, self.callbacks = name, 0x185, enabled, []

    def add_callback(self, callback):
        self.callbacks.append(callback)


class FakeNode:
    def __init__(self, *maps):
        self.tpdo = {n: m for n, m in enumerate(maps, 1)}


def test_each_tpdo_is_decoded_once_however_often_it_is_read(view):
    """Every read of the mapping asks again, so a TPDO enabled since is
    decoded -- and one already decoded is not given a second callback, which
    would put every value into the plot twice."""
    on, off = FakeMap("TxPDO1", True), FakeMap("TxPDO2", False)
    node = FakeNode(on, off)
    manager = view.manager
    assert manager._decode_tpdos(5, node) == ["TxPDO1"]
    assert manager._decode_tpdos(5, node) == ["TxPDO1"]
    assert len(on.callbacks) == 1, "read twice, decoded once"
    assert off.callbacks == [], "a disabled TPDO is not decoded"

    off.enabled = True  # enabled in the PDO tab, then read again
    assert manager._decode_tpdos(5, node) == ["TxPDO1", "TxPDO2"]
    assert len(off.callbacks) == 1


def test_nmt_says_who_it_goes_to_and_can_go_to_all(view, monkeypatch):
    """Reported: with a node listed there was no way to send NMT to every
    node, since a node stayed selected, and nothing said which it would be."""
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QKeyEvent, QMouseEvent

    view.manager.node_seen.emit(5, "added by hand")
    assert view.selected_node() == 5, "the first node heard is selected for you"
    sent = []
    monkeypatch.setattr(view.manager, "nmt", lambda node, command: sent.append(node))
    view.send_nmt.click()

    # A click below the last row selects none.
    view.show()
    empty = QPointF(10, view.nodes.viewport().height() - 5)
    press = QMouseEvent(
        QEvent.MouseButtonPress, empty, empty, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier
    )
    view.nodes.mousePressEvent(press)
    assert view.selected_node() is None
    view.send_nmt.click()
    assert sent == [5, 0], "one node, then every node (id 0)"

    view.nodes.setCurrentItem(view._node_item(5))
    view.nodes.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
    assert view.selected_node() is None, "and Esc does the same"
