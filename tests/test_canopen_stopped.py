# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""No SDO to a stopped node.

CiA 301 serves SDO only in Pre-operational and Operational. Everything asked
of a node over SDO used to go out whatever its heartbeat said, and a stopped
node left each request to wait for the timeout. Boot-up is the node entering
Pre-operational, so SDO goes straight out after one.
"""

import time

import canopen
import pytest
from PySide6.QtCore import QCoreApplication

from pycangui import resources
from pycangui.canopen import sdo_log
from pycangui.canopen.manager import CanopenManager
from pycangui.core.bus import BusManager

NODE = 5


def wait_until(pred, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not pred():
        QCoreApplication.processEvents()
        if time.monotonic() > deadline and not pred():
            raise AssertionError("timed out")
        time.sleep(0.005)


def settle(manager):
    done = []
    manager.background(lambda: None, lambda _r, _e: done.append(True))
    wait_until(lambda: done)


@pytest.fixture
def manager(app, demo_device, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    bus = BusManager()
    manager = CanopenManager(bus)
    # Long enough that a request that went out and timed out is told apart
    # from one refused without being sent.
    manager.set_sdo_timing(2.0, 0)
    bus.connect_bus("virtual", "vcan_stopped", 500000, False)
    demo_device(bus, kinds=["canopen_device"])
    wait_until(lambda: NODE in manager.nmt_states)
    yield manager
    bus.disconnect_bus()
    manager.shutdown()


def stop(manager):
    manager.nmt(NODE, "STOPPED")
    wait_until(lambda: manager.is_stopped(NODE))


def start(manager):
    manager.nmt(NODE, "OPERATIONAL")
    wait_until(lambda: not manager.is_stopped(NODE))


def test_sdo_to_a_stopped_node_is_refused_at_once_and_logged(manager):
    logged = []
    manager.sdo_logged.connect(logged.append)
    node = manager.node(NODE) or manager._ensure_node(NODE)
    stop(manager)
    began = time.monotonic()
    with pytest.raises(sdo_log.NodeStopped):
        node.sdo.upload(0x1018, 1)
    with pytest.raises(sdo_log.NodeStopped):
        node.sdo.download(0x2001, 0, b"\x01\x00")
    with pytest.raises(sdo_log.NodeStopped):
        node.sdo.open(0x1018, 1, "rb")
    assert time.monotonic() - began < 1.0, "not sent and timed out: not sent at all"
    QCoreApplication.processEvents()
    assert [r.error is not None and "STOPPED" in r.error for r in logged] == [True, True]

    start(manager)
    assert int.from_bytes(node.sdo.upload(0x1018, 2), "little") == 0x1234, "served again"


def test_a_refusal_is_a_communication_error_not_an_answer_about_the_object(manager):
    """Code that takes an abort to mean the object is absent must not take
    a node that was never asked for one that said no."""
    assert issubclass(sdo_log.NodeStopped, canopen.SdoCommunicationError)
    assert not issubclass(sdo_log.NodeStopped, canopen.SdoAbortedError)


def test_identifying_a_node_first_heard_waits_until_it_leaves_stopped(manager, monkeypatch):
    asked = []
    monkeypatch.setattr(manager, "identify", asked.append)
    stop(manager)
    manager.identify_unasked(NODE)
    QCoreApplication.processEvents()
    assert asked == [], "held while it is stopped"
    start(manager)
    wait_until(lambda: asked == [NODE])


def test_the_pdo_read_after_an_eds_waits_until_it_leaves_stopped(manager, monkeypatch):
    stop(manager)
    loaded = []
    manager.eds_loaded.connect(lambda *a: loaded.append(a))
    manager.load_eds(NODE, str(resources.path("demo.eds")))
    wait_until(lambda: loaded)
    settle(manager)
    assert manager.read_from_node(NODE) == (False, False), "nothing asked of a stopped node"
    node = manager.node(NODE)
    assert all(
        getattr(m, "_pycangui_decoded", False)
        for m in node.tpdo.map.values()
        if m.cob_id is not None and m.enabled
    ), "decoded by the EDS's mapping meanwhile"
    start(manager)
    wait_until(lambda: manager.read_from_node(NODE) == (True, True))


def test_a_batch_on_a_stopped_node_ends_with_why_rather_than_failing_each_object(manager):
    loaded = []
    manager.eds_loaded.connect(lambda *a: loaded.append(a))
    manager.load_eds(NODE, str(resources.path("demo.eds")))
    wait_until(lambda: loaded)
    settle(manager)
    stop(manager)
    results, finished, said = [], [], []
    manager.sdo_result.connect(lambda *a: results.append(a))
    manager.read_finished.connect(lambda *a: finished.append(a))
    manager.message.connect(lambda text, _level: said.append(text))
    manager.read_many(NODE, [(0x1018, 1), (0x1018, 2), (0x1018, 3)])
    wait_until(lambda: finished)
    assert results == [], "no object reported as failing one by one"
    assert any("STOPPED" in text for text in said)

    compared = []
    manager.read_objects(NODE, [(0x1018, 1)], lambda values, error: compared.append(error))
    wait_until(lambda: compared)
    assert compared[0] and "STOPPED" in compared[0], "not a node without the object"


def test_boot_up_counts_as_pre_operational(manager):
    """A boot-up is sent on entering Pre-operational, and SDO is served there."""
    manager._on_heartbeat(0x700 + NODE, bytearray([0]), 0.0)
    assert manager.nmt_states[NODE] == "PRE-OPERATIONAL"
    assert not manager.is_stopped(NODE)
