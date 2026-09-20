# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""UDS manager against the demo ECU on the virtual bus."""

import time

import pytest
from PySide6.QtCore import QCoreApplication

from pycangui.core import workspaces
from pycangui.core.bus import BusManager
from pycangui.core.context import Context
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
def stack(app, tmp_path, monkeypatch, demo_device):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    ctx = Context(log=print)
    hooks = Hooks(ctx)
    bus = BusManager()
    manager = UdsManager(bus, hooks, ctx)
    bus.connect_bus("virtual", "vcan_uds", 500000, False)
    demo = demo_device(bus, kinds=["uds_server"])
    yield bus, manager, demo, tmp_path
    manager.shutdown()
    bus.disconnect_bus()


def test_dtc_code():
    assert dtc_code(0x012345) == "P0123-45"
    assert dtc_code(0x9A0100) == "B1A01-00"  # top bits 10 -> Body
    assert dtc_code(0xC00000) == "U0000-00"


def test_uds_services_against_demo_ecu(stack):
    _bus, manager, demo, _home = stack
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
    assert "Session -> extended (P2 250 ms, P2* 5000 ms)" == last_after(n)
    n += 1
    manager.unlock(1)
    assert "no security algorithm" in last_after(n)  # default hook returns None
    n += 1

    hook = "def security_key(level, seed, *, ctx):" + chr(10)
    hook += "    return bytes(b ^ 0xFF for b in seed)" + chr(10)
    (workspaces.hooks_dir() / "uds.py").write_text(hook)
    manager._hooks.reload()
    manager.unlock(1)
    # Says which sub-functions went out, not just which level: the pair is
    # what an ECU document is written in.
    assert last_after(n) == "Security level 1: requestSeed 01, sendKey 02 -- unlocked"
    n += 1
    manager.write_did(0x0102, "00 10")
    assert last_after(n) == "DID 0102 written (2 bytes)"
    assert demo["uds_server"].state.identifiers[0x0102] == b"\x00\x10"
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
    assert demo["uds_server"].state.session == 1
    manager.close()
    assert lines[-1] == "UDS closed"


# --- what "level" means on the SecurityAccess row ---------------------------------------
def test_a_level_is_a_pair_of_sub_functions():
    """Reported: the box was labelled Level and held the requestSeed
    sub-function, so level 2 read as 2 when it is 03 and 04."""
    from pycangui.uds.standard import security_level, security_levels, security_pair

    assert security_levels()[0x01] == "Level 1"
    assert security_levels()[0x03] == "Level 2"
    assert security_levels()[0x11] == "Level 9", "the usual bootloader pair"

    assert security_level(0x03) == 2
    assert security_level(0x11) == 9
    assert security_level(0x04) is None, "an even sub-function is half of a pair"

    assert security_pair(0x03) == "requestSeed 03, sendKey 04"


def test_the_first_ten_levels_are_offered():
    from pycangui.uds.standard import security_levels

    offered = security_levels()
    assert len(offered) == 10
    assert list(offered) == [1, 3, 5, 7, 9, 0x0B, 0x0D, 0x0F, 0x11, 0x13]


def test_the_pane_offers_them_and_still_takes_anything_typed(app, tmp_path, monkeypatch):
    """Most real unlocking is in the manufacturer range and will never be
    on the list."""
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    from pycangui.core.bus import BusManager
    from pycangui.core.context import Context
    from pycangui.core.hooks import Hooks
    from pycangui.uds.manager import UdsManager
    from pycangui.ui.uds_view import UdsView, _picked

    ctx = Context(log=print)
    bus = BusManager()
    view = UdsView(UdsManager(bus, Hooks(ctx), ctx), ctx)

    assert view.level.count() == 10
    assert "Level 1" in view.level.itemText(0)
    assert view.level.isEditable(), "a list that refuses anything else is worse than none"

    view.level.setCurrentIndex(1)
    assert _picked(view.level) == 0x03, "level 2 is sub-function 03"

    view.level.setCurrentText("2B")
    assert _picked(view.level) == 0x2B, "a manufacturer sub-function was refused"
    bus.disconnect_bus()


def test_an_even_sub_function_is_not_silently_rounded_down(app, tmp_path, monkeypatch):
    """udsoncan rounds one down without saying so, which means asking for
    02 and being given 01: the right thing, done to the wrong level."""
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    from pycangui.core.bus import BusManager
    from pycangui.core.context import Context
    from pycangui.core.hooks import Hooks
    from pycangui.uds.manager import UdsManager

    ctx = Context(log=print)
    bus = BusManager()
    manager = UdsManager(bus, Hooks(ctx), ctx)

    said = []
    manager.result.connect(said.append)

    class Pretend:
        def unlock_security_access(self, level):
            said.append(f"sent {level:02X}")

    manager.client = Pretend()
    manager._run = lambda label, fn: said.append(fn(manager.client))

    manager.unlock(0x04)
    joined = " ".join(said)
    assert "sent 03" in joined, "the pair was not worked out"
    assert "level 2" in joined
    assert "04" in joined and "sendKey half" in joined, "said nothing about what was asked"
    bus.disconnect_bus()
