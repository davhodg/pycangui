# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""A heartbeat timeout set for a node, rather than worked out.

pycangui calls a node lost after three heartbeats' worth of silence, judged
from 0x1017 or from the heartbeats it sends.  That is wrong for a node whose
producer time is not what it sends, for one that goes quiet on purpose while it
erases, and for a bus so busy that three is too few.  Whoever sets a timeout
knows something the heartbeats do not say, so a set one wins, as set.
"""

import time

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QDialog

from pycangui.canopen.manager import CanopenManager
from pycangui.core.bus import BusManager
from pycangui.ui import canopen_settings, messages
from pycangui.ui.canopen_settings import CanopenSettings, CanopenSettingsDialog
from pycangui.ui.main_window import MainWindow


@pytest.fixture
def manager(app):
    bus = BusManager()
    manager = CanopenManager(bus)
    bus.connect_bus("virtual", "vcan_heartbeat_timeout", 500000, False)
    yield manager
    bus.disconnect_bus()
    manager.shutdown()


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    return tmp_path


def heard(manager, node_id, seconds_ago, interval=0.5):
    manager.last_heartbeat[node_id] = time.monotonic() - seconds_ago
    manager.heartbeat_interval[node_id] = interval


# --- the manager ---------------------------------------------------------------------------
def test_a_set_timeout_wins_over_the_worked_out_one(manager):
    heard(manager, 9, 0)
    assert manager.heartbeat_timeout(9) == pytest.approx(1.5), "three heartbeats of 500 ms"
    manager.set_heartbeat_timeouts({9: 4.0})
    assert manager.heartbeat_timeout(9) == 4.0
    manager.set_heartbeat_timeouts({})
    assert manager.heartbeat_timeout(9) == pytest.approx(1.5), "and back when taken off"


def test_a_set_timeout_is_taken_as_set_even_below_the_usual_floor(manager):
    heard(manager, 9, 0)
    manager.set_heartbeat_timeouts({9: 0.2})
    assert manager.heartbeat_timeout(9) == 0.2


def test_a_node_with_a_set_timeout_is_judged_before_its_period_is_known(manager):
    """One heartbeat is too little to work a timeout out from, but not too
    little to hold a node to one somebody set."""
    manager.last_heartbeat[10] = time.monotonic()
    assert manager.heartbeat_timeout(10) == 0.0
    manager.set_heartbeat_timeouts({10: 2.0})
    assert manager.heartbeat_timeout(10) == 2.0


def test_a_longer_timeout_keeps_a_slow_node_from_being_called_lost(manager):
    lost = []
    manager.node_lost.connect(lost.append)
    heard(manager, 9, seconds_ago=3)  # worked out: 1.5 s, so overdue

    manager.set_heartbeat_timeouts({9: 5.0})
    manager._check_liveness()
    assert lost == [] and 9 not in manager.lost_nodes

    manager.set_heartbeat_timeouts({9: 2.0})
    manager._check_liveness()
    assert lost == [9]


def test_a_set_timeout_survives_a_disconnect(manager):
    """It is configuration, not something observed on this connection."""
    manager.set_heartbeat_timeouts({9: 4.0})
    manager.forget_nodes()
    assert manager.heartbeat_overrides == {9: 4.0}


# --- kept in the workspace ------------------------------------------------------------------
def test_heartbeat_timeouts_survive_the_trip_through_the_workspace(app, home):
    window = MainWindow()
    chosen = CanopenSettings(heartbeat_timeouts={5: 1500.0, 6: 250.5})
    canopen_settings.save(window.ctx, chosen)
    assert window.ctx.settings.get("canopen.heartbeat_timeouts") == {"5": 1500, "6": 250.5}
    assert canopen_settings.load(window.ctx).heartbeat_timeouts == {5: 1500.0, 6: 250.5}
    window.close()


def test_a_bad_heartbeat_entry_costs_only_itself(app, home):
    window = MainWindow()
    window.ctx.settings.set(
        "canopen.heartbeat_timeouts", {"5": 1500, "6": "soon", "7": 5, "300": 1000}
    )
    assert canopen_settings.load(window.ctx).heartbeat_timeouts == {5: 1500.0}
    window.close()


def test_a_saved_heartbeat_timeout_is_applied_when_the_window_opens(app, home):
    first = MainWindow()
    canopen_settings.save(first.ctx, CanopenSettings(heartbeat_timeouts={5: 1500}))
    first.close()
    app.processEvents()

    second = MainWindow()
    assert second.canopen.heartbeat_overrides == {5: 1.5}
    second.close()


def test_the_dialog_s_heartbeat_timeout_reaches_the_manager(app, home, monkeypatch):
    window = MainWindow()

    def answered(dialog):
        dialog._add_row(5, 0x605, 0x585, 2500)
        return QDialog.Accepted

    monkeypatch.setattr(CanopenSettingsDialog, "exec", answered)
    window.canopen_view._open_settings()
    assert window.canopen.heartbeat_overrides == {5: 2.5}
    assert window.canopen.sdo_channels == {}, "the channel was left as it was"
    assert "heartbeat timeout node 5 2500 ms" in window.log.toPlainText()
    window.close()


# --- the dialog -------------------------------------------------------------------------------
def dialog(settings=None, selected=None):
    return CanopenSettingsDialog(None, settings or CanopenSettings(), selected)


def test_a_new_row_changes_nothing_until_something_is_typed(app):
    d = dialog(selected=5)
    d._add()
    assert d.table.item(0, canopen_settings.HEARTBEAT).text() == ""
    chosen = d.settings()
    assert chosen.channels == {} and chosen.heartbeat_timeouts == {}
    d.deleteLater()


def test_a_heartbeat_timeout_alone_is_an_override_of_its_own(app):
    d = dialog(selected=5)
    d._add()
    d.table.item(0, canopen_settings.HEARTBEAT).setText("1500 ms")
    chosen = d.settings()
    assert chosen.heartbeat_timeouts == {5: 1500.0}
    assert chosen.channels == {}
    d.deleteLater()


def test_a_node_with_only_a_heartbeat_timeout_is_listed_on_its_own_channel(app):
    d = dialog(CanopenSettings(heartbeat_timeouts={9: 2000}))
    texts = [d.table.item(0, column).text() for column in range(4)]
    assert texts == ["9", "0x609", "0x589", "2000"]
    d.deleteLater()


def test_a_node_with_both_is_one_row(app):
    d = dialog(CanopenSettings(channels={9: (0x641, 0x5C1)}, heartbeat_timeouts={9: 2000}))
    assert d.table.rowCount() == 1
    assert d.settings().channels == {9: (0x641, 0x5C1)}
    assert d.settings().heartbeat_timeouts == {9: 2000.0}
    d.deleteLater()


@pytest.mark.parametrize(
    "typed, because", [("soon", "not a heartbeat timeout"), ("50", "outside"), ("0", "outside")]
)
def test_a_heartbeat_timeout_that_will_not_do_is_refused(app, monkeypatch, typed, because):
    said = []
    monkeypatch.setattr(messages, "warning", lambda _p, _t, text: said.append(text))
    d = dialog(selected=5)
    d._add()
    d.table.item(0, canopen_settings.HEARTBEAT).setText(typed)
    accepted = []
    d.accepted.connect(lambda: accepted.append(True))
    d._accept()
    assert said and because in said[0]
    assert not accepted
    d.deleteLater()
