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
    assert last_after(n) == "Security level 1 (req 01 resp 02): unlocked"
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
def test_a_level_is_a_pair_and_the_level_is_the_number():
    """Reported twice: first the box held the requestSeed sub-function
    under a label saying Level, then "03  Level 2" left it unclear which
    of the two numbers the field was set to."""
    from pycangui.uds.standard import level_label, security_pair, seed_subfunction

    assert seed_subfunction(1) == 0x01
    assert seed_subfunction(2) == 0x03
    assert seed_subfunction(9) == 0x11, "the usual bootloader pair"

    assert security_pair(0x03) == "req 03 resp 04"
    assert level_label(2) == "2  req 03 resp 04", "the level leads"


def test_the_levels_run_to_the_last_pair_there_is():
    """Sub-functions stop at 0x7E, so the request half of the last pair is
    0x7D and there is no level 64."""
    from pycangui.uds.standard import MAX_SECURITY_LEVEL, seed_subfunction

    assert seed_subfunction(MAX_SECURITY_LEVEL) == 0x7D
    assert MAX_SECURITY_LEVEL == 63


def test_the_pane_holds_a_level_and_shows_what_it_sends(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    from pycangui.core.bus import BusManager
    from pycangui.core.context import Context
    from pycangui.core.hooks import Hooks
    from pycangui.uds.manager import UdsManager
    from pycangui.ui.uds_view import UdsView

    ctx = Context(log=print)
    bus = BusManager()
    view = UdsView(UdsManager(bus, Hooks(ctx), ctx), ctx)

    assert view.level.value() == 1
    assert view.level_pair.text() == "req 01 resp 02"

    view.level.setValue(2)
    assert view.level_pair.text() == "req 03 resp 04", "the pair did not follow the level"
    view.level.setValue(9)
    assert view.level_pair.text() == "req 11 resp 12"

    assert view.level.maximum() == 63, "there is no level past the last pair"
    bus.disconnect_bus()


def test_the_level_is_turned_into_its_request_sub_function(app, tmp_path, monkeypatch):
    """The pane counts levels; the wire takes the odd half of the pair."""
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

    manager.unlock(2)
    joined = " ".join(said)
    assert "sent 03" in joined, "level 2 did not become sub-function 03"
    assert "Security level 2 (req 03 resp 04)" in joined, "the log hides what went out"
    bus.disconnect_bus()
