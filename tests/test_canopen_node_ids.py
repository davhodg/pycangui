# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Only heartbeats make nodes.

Reported: something on the bus used CANopen's heartbeat identifiers for its
own purposes, so pycangui listed "nodes" that were not there and sent them
SDO. A frame on 0x701-0x77F is a heartbeat only if it is one byte carrying
one of the states, and the node ids listened for can be narrowed in Settings
so another protocol can share the reserved identifiers.
"""

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QSettings

from pycangui.canopen import node_ids
from pycangui.canopen.manager import CanopenManager
from pycangui.core.bus import BusManager


# --- writing the ids ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("typed", "ids"),
    [
        ("1-20", set(range(1, 21))),
        ("1-10, 32", {*range(1, 11), 32}),
        (" 5 ", {5}),
        ("3,1-2", {1, 2, 3}),
    ],
)
def test_ids_are_read_as_single_ids_and_ranges(typed, ids):
    assert node_ids.parse(typed) == ids


@pytest.mark.parametrize("typed", ["", "0-5", "1-128", "20-1", "five", "1-x", ","])
def test_what_is_not_a_node_id_is_refused_with_why(typed):
    with pytest.raises(ValueError) as error:
        node_ids.parse(typed)
    assert str(error.value)


def test_ids_are_written_back_the_shortest_way():
    for typed in ("1-127", "1-10, 32", "5", "1-3, 7-9, 100"):
        assert node_ids.text(node_ids.parse(typed)) == typed


# --- what the manager makes of a frame on 0x7xx ----------------------------------------------
@pytest.fixture
def manager(app):
    manager = CanopenManager(BusManager())
    said = []
    manager.message.connect(lambda text, _level: said.append(text))
    manager.said = said
    yield manager
    manager.shutdown()


def heard(manager, node_id, *data):
    manager._on_heartbeat(0x700 + node_id, bytearray(data), 0.0)
    QCoreApplication.processEvents()


@pytest.mark.parametrize("state", [0x00, 0x04, 0x05, 0x7F, 0x85, 0xFF])
def test_a_heartbeat_or_a_guarding_reply_is_a_node(manager, state):
    """0x85 and 0xFF are node-guarding replies, Operational and
    Pre-operational with the toggle bit set."""
    heard(manager, 9, state)
    assert manager.nodes() == [9]


@pytest.mark.parametrize("data", [(0x05, 0x00), (0x01,), (0x7E,), (0x12, 0x34, 0x56, 0x78), ()])
def test_anything_else_on_the_identifier_is_not_a_node(manager, data):
    seen = []
    manager.node_seen.connect(lambda n, s: seen.append(n))
    heard(manager, 112, *data)
    heard(manager, 112, *data)
    assert manager.nodes() == [] and seen == [], "not listed, so not identified"
    frame = SimpleNamespace(extended=False, can_id=0x770)
    assert manager.classify(frame) is None and manager.known_ids() == {}, "Other in the trace"
    if data:
        assert len([t for t in manager.said if "0x770" in t]) == 1, "said once for the id"


def test_heartbeats_outside_the_node_ids_are_ignored_altogether(manager):
    manager.set_node_ids(node_ids.parse("1-20"))
    heard(manager, 30, 0x05)
    heard(manager, 31, 0x12, 0x34)
    assert manager.nodes() == []
    assert manager.said == [], "not even said: the identifier is somebody else's"
    heard(manager, 20, 0x05)
    assert manager.nodes() == [20]
    assert manager.classify(SimpleNamespace(extended=False, can_id=0x794)) is None


def test_a_node_added_by_hand_is_heard_whatever_the_node_ids_say(manager, monkeypatch):
    manager.set_node_ids(node_ids.parse("1-20"))
    monkeypatch.setattr(manager, "network", SimpleNamespace(nodes={}, listeners=[]))
    monkeypatch.setattr(manager, "_ensure_node", lambda n: manager.network.nodes.setdefault(n, n))
    assert manager.add_node(30)
    heard(manager, 30, 0x05)
    assert manager.nmt_states.get(30) == "OPERATIONAL"


# --- the setting -------------------------------------------------------------------------------
def test_the_node_ids_are_kept_with_the_workspace(app, tmp_path, monkeypatch):
    from pycangui.core.context import Context
    from pycangui.ui import canopen_settings

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    ctx = Context(log=print)
    assert canopen_settings.load(ctx).node_ids == node_ids.ALL, "every id unless told"
    chosen = canopen_settings.CanopenSettings(node_ids=node_ids.parse("1-10, 32"))
    canopen_settings.save(ctx, chosen)
    assert canopen_settings.load(ctx).node_ids == chosen.node_ids
    said = canopen_settings.changes(canopen_settings.CanopenSettings(), chosen)
    assert said and "1-10, 32" in said[0] and "1-127" in said[0]


def test_the_dialog_refuses_ids_it_cannot_read(app, monkeypatch):
    from pycangui.ui import canopen_settings

    warned = []
    monkeypatch.setattr(canopen_settings.messages, "warning", lambda *a: warned.append(a[2]))
    dialog = canopen_settings.CanopenSettingsDialog(None, canopen_settings.CanopenSettings())
    dialog.node_ids.setText("1-200")
    dialog._accept()
    assert warned and "node ids" in warned[0] and dialog.result() == 0
    dialog.node_ids.setText("1-20")
    dialog._accept()
    assert dialog.settings().node_ids == set(range(1, 21))


def test_narrowing_the_ids_takes_nodes_outside_them_off_the_list(app, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QDialog

    from pycangui.ui.canopen_settings import CanopenSettingsDialog
    from pycangui.ui.main_window import MainWindow

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    window = MainWindow()
    try:
        view = window.canopen_view
        manager = view.manager
        monkeypatch.setattr(manager, "identify_unasked", lambda n: None)
        for node_id in (5, 112):
            heard(manager, node_id, 0x05)
        assert {5, 112} <= set(manager.nodes())

        def narrowed(dialog):
            dialog.node_ids.setText("1-20")
            return QDialog.Accepted

        monkeypatch.setattr(CanopenSettingsDialog, "exec", narrowed)
        view._open_settings()
        assert view._node_item(112) is None and 112 not in manager.nodes()
        assert view._node_item(5) is not None, "the ones inside stay"
    finally:
        window.close()
