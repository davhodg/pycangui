"""A2L parsing and the XCP master against the demo slave on the virtual bus."""

import struct
import time

import pytest
from PySide6.QtCore import QCoreApplication

from pycangui import resources
from pycangui.core.bus import BusManager
from pycangui.core.context import Context
from pycangui.core.demo import DemoDevice
from pycangui.core.hooks import Hooks
from pycangui.core.signals import SignalHub
from pycangui.xcp import decode_value, encode_value
from pycangui.xcp.a2l import A2l
from pycangui.xcp.manager import XcpManager


def test_a2l_parsing():
    a2l = A2l.load(str(resources.path("demo.a2l")))
    assert len(a2l.measurements()) == 3 and len(a2l.characteristics()) == 2
    speed = a2l.parameters["EngineSpeed"]
    assert speed.datatype == "UWORD" and speed.address == 0x1000 and speed.unit == "rpm"
    assert not speed.writable and speed.upper == 8000
    volts = a2l.parameters["BatteryVoltage"]
    assert volts.conversion.to_phys(1320) == pytest.approx(13.2)
    assert volts.conversion.to_raw(13.2) == pytest.approx(1320)
    limit = a2l.parameters["SpeedLimit"]
    assert limit.writable and limit.address == 0x2000
    assert a2l.parameters["CoolantTemp"].conversion is None  # NO_COMPU_METHOD


def test_value_codecs():
    assert decode_value(b"\xe8\x03", "UWORD", False) == 1000
    assert decode_value(b"\x03\xe8", "UWORD", True) == 1000
    assert decode_value(b"\xd8\xff", "SWORD", False) == -40
    assert encode_value(1000, "UWORD", False) == b"\xe8\x03"
    assert encode_value(12.4, "UWORD", False) == b"\x0c\x00"  # rounded
    assert struct.unpack("<f", encode_value(1.5, "FLOAT32_IEEE", False))[0] == 1.5


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
    hub = SignalHub()
    bus = BusManager()
    manager = XcpManager(bus, hooks, hub)
    bus.connect_bus("virtual", "vcan_xcp", 500000, False)
    demo = DemoDevice("vcan_xcp")
    yield bus, manager, demo, hub, tmp_path
    demo.stop()
    manager.shutdown()
    bus.disconnect_bus()


def test_xcp_against_demo_slave(stack):
    _bus, manager, demo, hub, home = stack
    lines: list[str] = []
    values: list[tuple] = []
    manager.result.connect(lines.append)
    manager.value.connect(lambda n, v: values.append((n, v)))

    def last_after(n):
        wait_until(lambda: len(lines) > n)
        return lines[-1]

    manager.load_a2l(str(resources.path("demo.a2l")))
    n = len(lines)
    manager.connect_slave()
    assert "resources CAL" in last_after(n)
    assert manager.is_connected
    n += 1

    manager.read("BatteryVoltage")
    assert last_after(n) == "BatteryVoltage = 13.2 V"
    n += 1
    manager.read("SpeedLimit")
    assert last_after(n) == "SpeedLimit = 6500 rpm"
    n += 1

    manager.write("SpeedLimit", "7000")  # CAL locked
    assert "ERR_ACCESS_LOCKED" in last_after(n)
    n += 1
    manager.unlock(0x01)
    assert "no key algorithm" in last_after(n)  # default hook returns None
    n += 1

    hook = "def compute_key(resource, seed, *, ctx):" + chr(10)
    hook += "    return bytes(b ^ 0xFF for b in seed)" + chr(10)
    (home / "hooks" / "xcp.py").write_text(hook)
    manager._hooks.reload()
    manager.unlock(0x01)
    assert last_after(n) == "XCP resource 0x01 unlocked"
    n += 1
    manager.write("SpeedLimit", "7000")
    assert last_after(n) == "SpeedLimit <- 7000"
    assert struct.unpack_from("<H", demo.xcp.memory, 0x2000)[0] == 7000

    manager.set_polled("EngineSpeed", True)
    wait_until(lambda: len(hub.keys()) and len(hub.get("XCP/EngineSpeed").values) >= 3, timeout=4)
    series = hub.get("XCP/EngineSpeed")
    assert series.unit == "rpm" and 800 <= series.latest <= 3800
    manager.set_polled("EngineSpeed", False)

    manager.disconnect_slave()
    wait_until(lambda: not manager.is_connected)
