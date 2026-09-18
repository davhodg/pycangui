# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""SDO settings: how long to wait for a node, how often to ask, and where.

The ``canopen`` package waits 300 ms, asks once, and talks to every node on the
channel CiA 301 predefines. Each of those is right for a bench node on a quiet
bus and wrong for somebody's machine, and which is found out in the field -- so
they are settings, and they have to reach every node, including the one an EDS
has just replaced.
"""

import canopen
import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QDialog, QPushButton

from pycangui.canopen.manager import DEFAULT_SDO_RETRIES, DEFAULT_SDO_TIMEOUT_S, CanopenManager
from pycangui.core.bus import BusManager
from pycangui.ui import canopen_settings, messages
from pycangui.ui.canopen_settings import CanopenSettings, CanopenSettingsDialog
from pycangui.ui.main_window import MainWindow


@pytest.fixture
def manager(app):
    bus = BusManager()
    manager = CanopenManager(bus)
    bus.connect_bus("virtual", "vcan_sdo_settings", 500000, False)
    yield manager
    bus.disconnect_bus()
    manager.shutdown()


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    return tmp_path


def timing(node):
    return node.sdo.RESPONSE_TIMEOUT, node.sdo.MAX_RETRIES


def channel(node):
    return node.sdo.rx_cobid, node.sdo.tx_cobid


# --- timing -------------------------------------------------------------------------------------
def test_until_somebody_changes_it_the_timing_is_the_library_s(manager):
    assert (DEFAULT_SDO_TIMEOUT_S, DEFAULT_SDO_RETRIES) == (0.3, 0)
    assert timing(manager._ensure_node(99)) == (0.3, 1), "one try is no retries"


def test_changed_timing_reaches_the_nodes_already_known_and_the_ones_to_come(manager):
    known = manager._ensure_node(99)
    manager.set_sdo_timing(2.0, 2)
    assert timing(known) == (2.0, 3), "two retries are three tries"
    assert timing(manager._ensure_node(100)) == (2.0, 3)


def test_a_node_given_an_eds_keeps_the_timing(manager):
    """Loading an EDS replaces the node object, which is how a setting applied
    in only one place would quietly stop applying."""
    manager.set_sdo_timing(1.5, 1)
    manager._adopt(99, canopen.RemoteNode(99, None))
    assert timing(manager.node(99)) == (1.5, 2)


def test_nothing_else_using_canopen_is_changed(manager):
    manager.set_sdo_timing(5.0, 3)
    manager._ensure_node(99)
    assert canopen.sdo.client.SdoClient.RESPONSE_TIMEOUT == 0.3
    assert canopen.sdo.client.SdoClient.MAX_RETRIES == 1


def test_nonsense_timing_is_not_taken_literally(manager):
    manager.set_sdo_timing(0, -4)
    assert manager.sdo_timeout_s > 0 and manager.sdo_retries == 0


# --- the channel ---------------------------------------------------------------------------------
def test_a_node_starts_on_the_predefined_channel(manager):
    assert channel(manager._ensure_node(5)) == (0x605, 0x585)


def test_a_node_off_the_predefined_channel_is_talked_to_on_its_own(manager):
    node = manager._ensure_node(5)
    manager.set_sdo_channels({5: (0x640, 0x5C0)})
    assert channel(node) == (0x640, 0x5C0)
    subscribers = manager.network.subscribers
    assert node.sdo.on_response in subscribers.get(0x5C0, [])
    assert node.sdo.on_response not in subscribers.get(0x585, []), "no longer listening there"


def test_a_request_goes_out_on_the_node_s_own_channel(manager, monkeypatch):
    node = manager._ensure_node(5)
    manager.set_sdo_channels({5: (0x640, 0x5C0)})
    sent = []
    monkeypatch.setattr(
        manager.network, "send_message", lambda can_id, _data, remote=False: sent.append(can_id)
    )
    node.sdo.send_request(bytearray(8))
    assert sent == [0x640]


def test_taken_off_the_list_a_node_goes_back_to_the_predefined_channel(manager):
    node = manager._ensure_node(5)
    manager.set_sdo_channels({5: (0x640, 0x5C0)})
    manager.set_sdo_channels({})
    assert channel(node) == (0x605, 0x585)
    assert node.sdo.on_response in manager.network.subscribers.get(0x585, [])


def test_a_node_heard_later_or_given_an_eds_gets_its_channel(manager):
    manager.set_sdo_channels({7: (0x650, 0x5D0)})
    assert channel(manager._ensure_node(7)) == (0x650, 0x5D0)
    manager._adopt(7, canopen.RemoteNode(7, None))
    assert channel(manager.node(7)) == (0x650, 0x5D0)


# --- kept in the workspace -----------------------------------------------------------------------
def test_settings_survive_the_trip_through_the_workspace(app, home):
    window = MainWindow()
    chosen = CanopenSettings(1500, 2, {5: (0x640, 0x5C0)})
    canopen_settings.save(window.ctx, chosen)
    assert canopen_settings.load(window.ctx) == chosen
    assert window.ctx.settings.get("canopen.sdo_channels") == {
        "5": {"request": "0x640", "response": "0x5C0"}
    }, "in hex, as a COB-ID is written"
    window.close()


def test_a_hand_edited_file_costs_the_bad_entry_and_not_the_rest(app, home):
    window = MainWindow()
    window.ctx.settings.set("canopen.sdo_timeout_ms", "slow")
    window.ctx.settings.set(
        "canopen.sdo_channels",
        {
            "5": {"request": "0x640", "response": "0x5C0"},
            "6": {"request": "banana"},
            "200": {"request": "0x100", "response": "0x101"},
        },
    )
    loaded = canopen_settings.load(window.ctx)
    assert loaded.timeout_ms == 300
    assert loaded.channels == {5: (0x640, 0x5C0)}
    window.close()


def test_what_was_saved_is_applied_when_the_window_opens(app, home):
    first = MainWindow()
    canopen_settings.save(first.ctx, CanopenSettings(2000, 1, {5: (0x640, 0x5C0)}))
    first.close()
    app.processEvents()

    second = MainWindow()
    assert (second.canopen.sdo_timeout_s, second.canopen.sdo_retries) == (2.0, 1)
    assert second.canopen.sdo_channels == {5: (0x640, 0x5C0)}
    second.close()


# --- the pane ------------------------------------------------------------------------------------
def test_the_pane_keeps_its_settings_behind_one_button(app, home):
    """The bar is for commands; settings beside them hid them further."""
    window = MainWindow()
    view = window.canopen_view
    assert "Settings..." in [b.text() for b in view.findChildren(QPushButton)]
    assert not hasattr(view, "sdo_timeout")
    window.close()


def test_what_the_dialog_is_told_reaches_the_manager_and_the_workspace(app, home, monkeypatch):
    window = MainWindow()

    def answered(dialog):
        dialog.timeout.setValue(1500)
        dialog.retries.setValue(2)
        dialog._add_row(5, 0x640, 0x5C0)
        return QDialog.Accepted

    monkeypatch.setattr(CanopenSettingsDialog, "exec", answered)
    window.canopen_view._open_settings()
    assert (window.canopen.sdo_timeout_s, window.canopen.sdo_retries) == (1.5, 2)
    assert window.canopen.sdo_channels == {5: (0x640, 0x5C0)}
    assert canopen_settings.load(window.ctx).channels == {5: (0x640, 0x5C0)}
    assert "node 5 on 0x640/0x5C0" in window.log.toPlainText()
    window.close()


def test_cancel_changes_nothing(app, home, monkeypatch):
    window = MainWindow()

    def cancelled(dialog):
        dialog.timeout.setValue(1500)
        return QDialog.Rejected

    monkeypatch.setattr(CanopenSettingsDialog, "exec", cancelled)
    window.canopen_view._open_settings()
    assert window.canopen.sdo_timeout_s == 0.3
    assert canopen_settings.load(window.ctx).timeout_ms == 300
    window.close()


# --- the dialog ----------------------------------------------------------------------------------
def dialog(settings=None, selected=None):
    return CanopenSettingsDialog(None, settings or CanopenSettings(), selected)


def row(d, at):
    return [d.table.item(at, column).text() for column in range(3)]


def test_add_starts_from_the_selected_node_on_its_predefined_channel(app):
    d = dialog(selected=5)
    d._add()
    assert row(d, 0) == ["5", "0x605", "0x585"]
    d._add()
    assert row(d, 1)[0] == "1", "then the first node not already listed"
    d.deleteLater()


def test_the_overrides_already_set_are_listed(app):
    d = dialog(CanopenSettings(channels={9: (0x641, 0x5C1)}))
    assert row(d, 0) == ["9", "0x641", "0x5C1"]
    d.deleteLater()


def test_a_row_left_on_the_predefined_channel_is_no_override(app):
    d = dialog(selected=5)
    d._add()
    assert d.settings().channels == {}
    d.table.item(0, 1).setText("640")
    d.table.item(0, 2).setText("0x5C0")
    assert d.settings().channels == {5: (0x640, 0x5C0)}, "bare digits are hex"
    d.deleteLater()


@pytest.mark.parametrize(
    "texts, because",
    [
        (("5", "0x800", "0x5C0"), "11-bit"),
        (("5", "0x640", "0x640"), "cannot share"),
        (("200", "0x640", "0x5C0"), "not a node id"),
        (("five", "0x640", "0x5C0"), "not a node id"),
        (("5", "banana", "0x5C0"), "not a COB-ID"),
    ],
)
def test_settings_that_will_not_do_are_refused_and_the_dialog_stays(
    app, monkeypatch, texts, because
):
    said = []
    monkeypatch.setattr(messages, "warning", lambda _p, _t, text: said.append(text))
    d = dialog()
    d._add()
    for column, text in enumerate(texts):
        d.table.item(0, column).setText(text)
    accepted = []
    d.accepted.connect(lambda: accepted.append(True))

    d._accept()
    assert said and because in said[0]
    assert not accepted
    d.deleteLater()


def test_the_same_node_twice_is_refused(app, monkeypatch):
    said = []
    monkeypatch.setattr(messages, "warning", lambda _p, _t, text: said.append(text))
    d = dialog()
    d._add_row(5, 0x640, 0x5C0)
    d._add_row(5, 0x641, 0x5C1)
    d._accept()
    assert said and "listed twice" in said[0]
    d.deleteLater()
