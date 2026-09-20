# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""EMCY decoding, the manufacturer-bytes hook, and the demo device's emergency."""

import time

import pytest
from PySide6.QtCore import QCoreApplication

from pycangui import resources
from pycangui.canopen.emcy import Emcy, describe_code, describe_register, encode
from pycangui.canopen.manager import CanopenManager
from pycangui.core import workspaces
from pycangui.core.bus import BusManager
from pycangui.core.context import Context
from pycangui.core.hooks import Hooks


def test_describe_code_exact_then_category():
    assert describe_code(0x0000) == "Error reset or no error"
    assert describe_code(0x2310) == "Continuous over current"
    assert describe_code(0x8130) == "Life guard or heartbeat error"
    assert describe_code(0x3210) == "DC link over voltage"
    assert describe_code(0x2311) == "Current, device output side"  # falls back to 0x2300
    assert describe_code(0x4567) == "Temperature"  # falls back to the category
    assert describe_code(0xAB12) == "Unknown"


def test_describe_register_bits():
    assert describe_register(0x00) == "no error"
    assert describe_register(0x01) == "generic"
    assert describe_register(0x11) == "generic, communication"
    assert describe_register(0x82) == "current, manufacturer"


def test_emcy_helpers():
    e = Emcy(node_id=5, code=0x2310, register=0x02, data=b"\xe8\x03\x01\x00\x00")
    assert e.description == "Continuous over current"
    assert e.register_text == "current"
    assert e.data_hex == "E8 03 01 00 00"
    assert e.as_int(0, 2) == 1000
    assert e.as_int(2, 1) == 1
    assert e.as_int(4, 4) is None  # past the end
    assert not e.is_reset and Emcy(5, 0x0000, 0).is_reset
    assert "0x2310 Continuous over current" in str(e)


def test_encode_pads_to_eight_bytes():
    assert encode(0x2310, 0x02, b"\x01") == b"\x10\x23\x02\x01\x00\x00\x00\x00"
    assert len(encode(0x0000, 0x00)) == 8


def wait_until(pred, timeout=8.0):
    deadline = time.monotonic() + timeout
    while not pred():
        QCoreApplication.processEvents()
        if time.monotonic() > deadline:
            raise AssertionError("timed out")
        time.sleep(0.005)


@pytest.fixture
def stack(app, tmp_path, monkeypatch, demo_device):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    ctx = Context(log=print)
    hooks = Hooks(ctx)
    bus = BusManager()
    manager = CanopenManager(bus, hooks)
    bus.connect_bus("virtual", "vcan_emcy", 500000, False)
    demo = demo_device(bus, kinds=["canopen_device"])
    manager.load_eds(5, str(resources.path("demo.eds")))
    wait_until(lambda: manager.node(5) is not None and len(manager.node(5).object_dictionary))
    yield manager, demo, hooks, tmp_path
    manager.shutdown()
    bus.disconnect_bus()


def test_demo_emergency_is_decoded(stack):
    manager, _demo, hooks, _home = stack
    seen: list[Emcy] = []
    manager.emcy.connect(seen.append)
    manager.emcy.connect(manager.remember_emcy)

    # over-speed the demo drive: it raises an over-current emergency
    manager.sdo_write(5, 0x2001, 0, "3000")
    wait_until(lambda: seen)
    e = seen[0]
    assert e.node_id == 5 and e.code == 0x2310
    assert e.description == "Continuous over current"
    assert e.register == 0x02 and e.register_text == "current"
    assert e.as_int(0, 2) == 300 and e.as_int(2, 1) == 1  # 3000/10 A, channel 1
    assert e.manufacturer_text == ""  # no hook yet
    assert manager.emcy_history == seen

    # bring it back down: the node sends an error reset
    manager.sdo_write(5, 0x2001, 0, "100")
    wait_until(lambda: len(seen) > 1)
    assert seen[-1].is_reset and seen[-1].description == "Error reset or no error"

    # a hook decodes the manufacturer bytes
    hook = "def emcy_manufacturer(code, register, data, *, ctx):" + chr(10)
    hook += "    if code == 0x2310 and len(data) >= 3:" + chr(10)
    hook += "        amps = int.from_bytes(data[0:2], 'little') / 10" + chr(10)
    hook += "        return f'{amps:.1f} A on channel {data[2]}'" + chr(10)
    hook += "    return None" + chr(10)
    (workspaces.hooks_dir() / "canopen.py").write_text(hook)
    hooks.reload()

    n = len(seen)
    manager.sdo_write(5, 0x2001, 0, "-2500")
    wait_until(lambda: len(seen) > n)
    latest = seen[-1]
    assert latest.code == 0x2310
    assert latest.manufacturer_text == "25.0 A on channel 1"
    assert "25.0 A on channel 1" in str(latest)


def test_history_is_bounded_and_clearable(stack):
    manager, _demo, _hooks, _home = stack
    for i in range(600):
        manager.remember_emcy(Emcy(5, 0x1000, 0x01, timestamp=float(i)))
    assert len(manager.emcy_history) == 500
    assert manager.emcy_history[0].timestamp == 100.0  # oldest dropped
    manager.clear_emcy_history()
    assert manager.emcy_history == []


# --- what an emergency is now, as against what it was -----------------------------------
def make(node_id, code, at=0.0):
    return Emcy(node_id=node_id, code=code, register=0x01, timestamp=at)


def test_an_emergency_is_active_until_its_own_node_resets():
    """An arrival log answers "what happened". Somebody with a machine that
    will not run is asking "what is still wrong"."""
    from pycangui.canopen.emcy import ACTIVE, CLEARED, RESET, states

    history = [make(1, 0x2310), make(1, 0x3210), make(1, 0x0000), make(1, 0x4210)]
    assert states(history) == [CLEARED, CLEARED, RESET, ACTIVE]


def test_one_node_recovering_says_nothing_about_another():
    from pycangui.canopen.emcy import ACTIVE, CLEARED, RESET, states

    history = [make(1, 0x2310), make(2, 0x3210), make(2, 0x0000)]
    assert states(history) == [ACTIVE, CLEARED, RESET]


def test_a_node_that_faults_again_after_a_reset_is_active_again():
    from pycangui.canopen.emcy import ACTIVE, CLEARED, RESET, states

    history = [make(1, 0x2310), make(1, 0x0000), make(1, 0x2310)]
    assert states(history) == [CLEARED, RESET, ACTIVE]


def test_nothing_at_all_is_not_an_error():
    from pycangui.canopen.emcy import states

    assert states([]) == []


def test_the_export_carries_the_state_and_the_raw_register():
    """A maker quoting a bit number wants the number, not only the words."""
    from pycangui.canopen.emcy import ACTIVE, as_rows

    rows = as_rows([Emcy(node_id=3, code=0x2310, register=0x06, data=b"\x01\x02")])
    assert rows[0][:5] == ["Time", "Node", "Code", "Description", "State"]
    assert rows[1][1] == "3"
    assert rows[1][2] == "2310"
    assert rows[1][4] == ACTIVE
    assert rows[1][5] == "06", "the byte itself"
    assert "current" in rows[1][6], "and what it decodes to, beside it"
    assert rows[1][7] == "01 02"
