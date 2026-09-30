# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""The CANopen log: every SDO transfer, and what else happens once rather than
over and over -- NMT sent, a node's state changing, emergencies, LSS."""

import time

import pytest
from PySide6.QtCore import QCoreApplication

from pycangui import resources
from pycangui.canopen.manager import ADDED_BY_HAND, CanopenManager
from pycangui.canopen.sdo_log import SdoRecord
from pycangui.core.bus import BusManager
from pycangui.core.context import Context
from pycangui.ui.canopen_log_view import BOOT_UP, NMT, SDO, STATE, CanopenLogView


def wait_until(pred, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not pred():
        QCoreApplication.processEvents()
        if time.monotonic() > deadline:
            raise AssertionError("timed out")
        time.sleep(0.005)


@pytest.fixture
def stack(app, demo_device):
    bus = BusManager()
    manager = CanopenManager(bus)
    bus.connect_bus("virtual", "vcan_sdo_log", 500000, False)
    demo_device(bus, kinds=["canopen_device"])
    records: list[SdoRecord] = []
    manager.sdo_logged.connect(records.append)
    yield manager, records
    bus.disconnect_bus()
    manager.shutdown()


def test_every_transfer_is_logged_with_what_it_said(stack):
    manager, records = stack
    loaded = []
    manager.eds_loaded.connect(lambda *a: loaded.append(a))
    manager.load_eds(5, str(resources.path("demo.eds")))
    wait_until(lambda: loaded)
    records.clear()  # what loading the EDS asked is logged too, but not the point here

    results = []
    manager.sdo_result.connect(lambda *a: results.append(a))
    manager.sdo_read(5, 0x6041, 0)
    manager.sdo_write(5, 0x2001, 0, "-40")
    manager.sdo_read(5, 0x9999, 0)
    wait_until(lambda: len(results) == 3)
    wait_until(lambda: len(records) >= 3)

    read, write, missing = (
        next(r for r in records if (r.index, r.write) == key)
        for key in ((0x6041, False), (0x2001, True), (0x9999, False))
    )
    assert read.node_id == 5 and read.data == (0x0237).to_bytes(2, "little")
    assert read.name, "named from the EDS"
    assert write.data == (-40).to_bytes(2, "little", signed=True)
    assert missing.data is None and missing.error, "a failure, with the node's reason"


# --- the tab --------------------------------------------------------------------------
@pytest.fixture
def log(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    ctx = Context(log=print)
    manager = CanopenManager(BusManager())
    selected = [5]
    view = CanopenLogView(manager, ctx, lambda: selected[0])
    view.selected = selected
    yield view
    manager.shutdown()


def visible(view) -> list[tuple[int | None, str]]:
    """(node, kind) of each line on screen, oldest first."""
    view.flush()
    lines = [line for line in view._lines if view._wanted(line)]
    assert len(view.text.toPlainText().splitlines()) == len(lines), "one line each, shown"
    return [(line.node_id, line.kind) for line in lines]


def sdo(node_id):
    return SdoRecord(1.0, node_id, False, 0x6041, 0, "", b"\x01", None, 0.002)


def test_a_state_is_logged_when_it_changes_not_every_heartbeat(log):
    for state in ("PRE-OPERATIONAL", "PRE-OPERATIONAL", "OPERATIONAL", "OPERATIONAL"):
        log.manager.node_seen.emit(5, state)
    assert visible(log) == [(5, STATE), (5, STATE)], "first heard, then the one change"

    log.manager.node_seen.emit(5, BOOT_UP)
    log.manager.node_seen.emit(5, BOOT_UP)
    assert len(visible(log)) == 4, "a boot-up is news every time"
    log.manager.node_seen.emit(6, ADDED_BY_HAND)
    log.manager.node_lost.emit(5)
    assert visible(log)[-2:] == [(6, STATE), (5, STATE)]


def test_nmt_and_sync_are_logged_to_whom_they_went(log):
    log.manager.nmt_sent.emit(0, "RESET")
    log.manager.nmt_sent.emit(5, "OPERATIONAL")
    log.manager.sync_changed.emit(True, 0.1)
    assert visible(log) == [(0, NMT), (5, NMT), (None, NMT)]


def test_the_ticks_filter_what_is_shown_and_lose_nothing(log):
    for node in (5, 6):
        log.manager.sdo_logged.emit(sdo(node))
    log.manager.nmt_sent.emit(0, "RESET")
    everything = [(5, SDO), (6, SDO), (0, NMT)]
    assert visible(log) == everything

    log.kinds[SDO].setChecked(False)
    assert visible(log) == [(0, NMT)]
    assert log.ctx.settings.get("canopen.log.hidden") == [SDO], "and it is remembered"
    log.kinds[SDO].setChecked(True)
    assert visible(log) == everything, "hidden, not thrown away"

    log.only_selected.setChecked(True)
    assert visible(log) == [(5, SDO), (0, NMT)], "what went to every node reached this one"
    log.selected[0] = 6
    log.node_changed()
    assert visible(log) == [(6, SDO), (0, NMT)]
