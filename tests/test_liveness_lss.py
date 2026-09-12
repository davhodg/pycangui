"""Heartbeat liveness (the consumer side) and LSS commissioning."""

import struct
import time

import can
import pytest
from PySide6.QtCore import QCoreApplication

from pycangui import resources
from pycangui.canopen.manager import LSS_BIT_TIMINGS, MISSED_HEARTBEATS, CanopenManager
from pycangui.core.bus import BusManager


def wait_until(pred, timeout=8.0):
    deadline = time.monotonic() + timeout
    while not pred():
        QCoreApplication.processEvents()
        if time.monotonic() > deadline:
            raise AssertionError("timed out")
        time.sleep(0.005)


@pytest.fixture
def stack(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    bus = BusManager()
    manager = CanopenManager(bus)
    bus.connect_bus("virtual", "vcan_live", 500000, False)
    yield bus, manager
    manager.shutdown()
    bus.disconnect_bus()


# --- heartbeat liveness -------------------------------------------------------
def test_node_is_reported_lost_then_back(stack, demo_device):
    bus, manager = stack
    lost: list[int] = []
    back: list[int] = []
    manager.node_lost.connect(lost.append)
    manager.node_back.connect(back.append)

    demo = demo_device(bus, kinds=["canopen_device"])  # heartbeat every 500 ms
    try:
        # Four heartbeats, not two: the period is the median of several gaps,
        # so that one burst from a producer catching up cannot be mistaken for
        # the rate.  At 500 ms that is about two seconds, and this waits well
        # past it rather than racing the machine for the last one.
        wait_until(lambda: manager.heartbeat_interval.get(5) is not None, timeout=8)
        # the timeout follows the observed period, not a fixed guess
        interval = manager.heartbeat_interval[5]
        assert 0.4 < interval < 0.7
        assert manager.heartbeat_timeout(5) == pytest.approx(interval * MISSED_HEARTBEATS, abs=0.2)
        assert 5 not in manager.lost_nodes
    finally:
        demo.stop_all()  # the node goes quiet

    wait_until(lambda: lost, timeout=5)
    assert lost == [5] and 5 in manager.lost_nodes

    # a heartbeat arriving again clears it
    other = can.Bus(interface="virtual", channel="vcan_live")
    try:
        other.send(can.Message(arbitration_id=0x705, data=[0x05], is_extended_id=False))
        wait_until(lambda: back, timeout=3)
    finally:
        other.shutdown()
    assert back == [5] and 5 not in manager.lost_nodes


def test_one_heartbeat_alone_is_not_judged(stack):
    _bus, manager = stack
    lost: list[int] = []
    manager.node_lost.connect(lost.append)
    other = can.Bus(interface="virtual", channel="vcan_live")
    try:
        other.send(can.Message(arbitration_id=0x70A, data=[0x05], is_extended_id=False))
        wait_until(lambda: 10 in manager.last_heartbeat, timeout=3)
        assert manager.heartbeat_timeout(10) == 0.0  # no interval known yet
        time.sleep(0.5)
        QCoreApplication.processEvents()
        assert lost == []
    finally:
        other.shutdown()


def test_disconnect_forgets_nodes(stack):
    bus, manager = stack
    manager.last_heartbeat[9] = time.monotonic()
    manager.heartbeat_interval[9] = 0.5
    manager.lost_nodes.add(9)
    bus.disconnect_bus()
    assert manager.last_heartbeat == {} and manager.lost_nodes == set()


# --- LSS -----------------------------------------------------------------------
class FakeLssSlave:
    """Just enough of a CiA 305 slave to drive the master: selective addressing,
    node-ID and bit timing configuration, store, and the inquire services."""

    RX = 0x7E5  # master -> slave
    TX = 0x7E4  # slave -> master

    def __init__(self, bus: can.BusABC, identity: tuple[int, int, int, int]) -> None:
        self._bus = bus
        self.identity = identity
        self.node_id: int | None = None
        self.bit_timing: int | None = None
        self.stored = False
        self.configuring = False
        self._pending: list[int] = []

    def on_frame(self, msg: can.Message) -> None:
        if msg.arbitration_id != self.RX or len(msg.data) < 8 or not msg.is_rx:
            return
        cs = msg.data[0]
        value = struct.unpack_from("<I", msg.data, 1)[0]
        if cs in (0x40, 0x41, 0x42, 0x43):  # switch state selective
            self._pending.append(value)
            if cs == 0x43:
                if tuple(self._pending[-4:]) == self.identity:
                    self.configuring = True
                    self._reply(bytes([0x44]) + bytes(7))
                self._pending.clear()
        elif cs == 0x04:  # switch state global
            self.configuring = msg.data[1] == 0x01
        elif cs == 0x11 and self.configuring:  # configure node-ID
            self.node_id = msg.data[1]
            self._reply(bytes([0x11, 0x00]) + bytes(6))
        elif cs == 0x13 and self.configuring:  # configure bit timing
            self.bit_timing = msg.data[2]
            self._reply(bytes([0x13, 0x00]) + bytes(6))
        elif cs == 0x17 and self.configuring:  # store
            self.stored = True
            self._reply(bytes([0x17, 0x00]) + bytes(6))
        elif cs in (0x5A, 0x5B, 0x5C, 0x5D) and self.configuring:  # inquire identity
            self._reply(bytes([cs]) + struct.pack("<I", self.identity[cs - 0x5A]) + bytes(3))
        elif cs == 0x5E and self.configuring:  # inquire node-ID
            self._reply(bytes([0x5E, self.node_id or 0xFF]) + bytes(6))

    def _reply(self, data: bytes) -> None:
        self._bus.send(can.Message(arbitration_id=self.TX, data=data, is_extended_id=False))


class _Listener(can.Listener):
    def __init__(self, slave: FakeLssSlave) -> None:
        self._slave = slave

    def on_message_received(self, msg: can.Message) -> None:
        self._slave.on_frame(msg)

    def on_error(self, exc: Exception) -> None:
        pass


@pytest.fixture
def lss_stack(stack):
    _bus, manager = stack
    # The slave needs its own bus and notifier: on the master's bus our own
    # transmissions come back as echoes (is_rx False) and would be ignored.
    slave_bus = can.Bus(interface="virtual", channel="vcan_live")
    slave = FakeLssSlave(slave_bus, (0x42, 0x1234, 0x10002, 0xBEEF))
    notifier = can.Notifier(slave_bus, [_Listener(slave)], timeout=0.05)
    yield manager, slave
    notifier.stop()
    slave_bus.shutdown()


def test_lss_select_configure_and_store(lss_stack):
    manager, slave = lss_stack
    results: list[str] = []
    manager.lss_result.connect(results.append)

    manager.lss_select(0x42, 0x1234, 0x10002, 0xBEEF)
    wait_until(lambda: results)
    assert "is in configuration state" in results[-1] and slave.configuring

    n = len(results)
    manager.lss_set_node_id(7)
    wait_until(lambda: len(results) > n)
    assert slave.node_id == 7 and "node-ID set to 7" in results[-1]

    n = len(results)
    index = dict((rate, i) for i, rate in LSS_BIT_TIMINGS)[250_000]
    manager.lss_set_bit_timing(index)
    wait_until(lambda: len(results) > n)
    assert slave.bit_timing == index and "250000 bit/s" in results[-1]

    n = len(results)
    manager.lss_store()
    wait_until(lambda: len(results) > n)
    assert slave.stored and "stored" in results[-1]

    n = len(results)
    manager.lss_inquire()
    wait_until(lambda: len(results) > n)
    assert "node-ID 7, address 00000042:00001234:00010002:0000BEEF" in results[-1]

    manager.lss_switch_global(False)
    wait_until(lambda: not slave.configuring, timeout=3)


def test_lss_select_reports_no_match(lss_stack):
    manager, slave = lss_stack
    results: list[str] = []
    manager.lss_result.connect(results.append)
    manager.lss_select(0x99, 0x99, 0x99, 0x99)
    wait_until(lambda: results, timeout=5)
    assert "no node matched" in results[-1] and not slave.configuring


def test_lss_without_a_bus_is_reported(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    manager = CanopenManager(BusManager())
    results: list[str] = []
    manager.lss_result.connect(results.append)
    manager.lss_store()
    assert results == ["LSS: not connected"]
    manager.shutdown()


def test_bit_timing_table_matches_cia_305():
    assert dict(LSS_BIT_TIMINGS)[0] == 1_000_000
    assert dict(LSS_BIT_TIMINGS)[2] == 500_000
    assert dict(LSS_BIT_TIMINGS)[4] == 125_000
    assert 5 not in dict(LSS_BIT_TIMINGS)  # reserved in the standard
    assert str(resources.path("demo.eds")).endswith("demo.eds")
