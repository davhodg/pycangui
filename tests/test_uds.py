"""UDS manager against the demo ECU on the virtual bus."""

import time

import pytest
from PySide6.QtCore import QCoreApplication

from pycangui.core.bus import BusManager
from pycangui.core.context import Context
from pycangui.core.demo import DemoDevice
from pycangui.core.hooks import Hooks
from pycangui.uds import UdsConfig
from pycangui.uds.manager import UdsManager, dtc_code


def wait_until(pred, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not pred():
        QCoreApplication.processEvents()
        if time.monotonic() > deadline:
            raise AssertionError("timed out")
        time.sleep(0.005)


@pytest.fixture
def stack(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    ctx = Context(log=print)
    hooks = Hooks(ctx)
    bus = BusManager()
    manager = UdsManager(bus, hooks, ctx)
    bus.connect_bus("virtual", "vcan_uds", 500000, False)
    demo = DemoDevice("vcan_uds")
    yield bus, manager, demo, tmp_path
    demo.stop()
    manager.shutdown()
    bus.disconnect_bus()


def test_dtc_code():
    assert dtc_code(0x012345) == "P0123-45"
    assert dtc_code(0x9A0100) == "B1A01-00"  # top bits 10 -> Body
    assert dtc_code(0xC00000) == "U0000-00"


def test_uds_services_against_demo_ecu(stack):
    _bus, manager, demo, home = stack
    lines: list[str] = []
    manager.result.connect(lines.append)

    def last_after(n):
        wait_until(lambda: len(lines) > n)
        return lines[-1]

    manager.open(UdsConfig())
    assert lines[-1].startswith("UDS open")
    n = len(lines)

    manager.read_did(0xF190)
    assert 'PYCANGUI0DEMO0001"' in last_after(n)
    n += 1
    manager.read_did(0x9999)
    assert "NRC 0x31" in last_after(n)
    n += 1
    manager.write_did(0x0102, "00 10")
    assert "NRC 0x33" in last_after(n)  # locked
    n += 1
    manager.unlock(1)
    assert "NRC 0x7F" in last_after(n)  # not in default session
    n += 1
    manager.change_session(3)
    assert "Session -> extended (P2 50 ms, P2* 5000 ms)" == last_after(n)
    n += 1
    manager.unlock(1)
    assert "no security algorithm" in last_after(n)  # default hook returns None
    n += 1

    hook = "def security_key(level, seed, *, ctx):" + chr(10)
    hook += "    return bytes(b ^ 0xFF for b in seed)" + chr(10)
    (home / "hooks" / "uds.py").write_text(hook)
    manager._hooks.reload()
    manager.unlock(1)
    assert last_after(n) == "Security level 1: unlocked"
    n += 1
    manager.write_did(0x0102, "00 10")
    assert last_after(n) == "DID 0102 written (2 bytes)"
    assert demo.uds.dids[0x0102] == b"\x00\x10"
    n += 1

    manager.read_dtcs(0xFF)
    text = last_after(n)
    assert "DTCs by status mask (status mask 0xFF): 2 DTC(s)" in text, text
    assert "P0123-45" in text and "confirmed" in text
    n += 1
    manager.clear_dtcs()
    last_after(n)
    n += 1
    manager.read_dtcs(0xFF)
    assert "0 DTC(s)" in last_after(n), "cleared, so the count is nought"
    n += 1

    manager.routine(1, 0x0203, b"")
    assert "Routine 0203 start: OK" in last_after(n)
    n += 1
    manager.routine(3, 0x0203, b"")
    assert last_after(n).endswith("01")
    n += 1
    manager.raw(bytes([0x22, 0xF1, 0x95]))
    assert "62 F1 95" in last_after(n)
    n += 1
    manager.ecu_reset(1)
    assert last_after(n) == "ECUReset (hard reset) OK"
    assert demo.uds.session == 1
    manager.close()
    assert lines[-1] == "UDS closed"
