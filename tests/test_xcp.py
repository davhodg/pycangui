# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""A2L parsing and the XCP master against the demo slave on the virtual bus."""

import struct
import time

import pytest
from PySide6.QtCore import QCoreApplication

from pycangui import resources
from pycangui.core import workspaces
from pycangui.core.bus import BusManager
from pycangui.core.context import Context
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
def stack(app, tmp_path, monkeypatch, demo_device):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    ctx = Context(log=print)
    hooks = Hooks(ctx)
    hub = SignalHub()
    bus = BusManager()
    manager = XcpManager(bus, hooks, hub, ctx)
    bus.connect_bus("virtual", "vcan_xcp", 500000, False)
    demo = demo_device(bus, kinds=["xcp_slave"])
    yield bus, manager, demo, hub, tmp_path
    manager.shutdown()
    bus.disconnect_bus()


def test_xcp_against_demo_slave(stack):
    _bus, manager, demo, hub, _home = stack
    lines: list[str] = []
    values: list[tuple] = []
    manager.result.connect(lines.append)
    manager.value.connect(lambda n, v: values.append((n, v)))

    def last_after(n):
        wait_until(lambda: len(lines) > n)
        return lines[-1]

    manager.load_a2l(str(resources.path("demo.a2l")))
    # Said rather than assumed: XCP on CAN standardises no identifiers, so
    # the pane starts with the boxes empty and these are the demo device's.
    manager.set_ids(0x7A0, 0x7A1, False)
    n = len(lines)
    manager.connect_slave()
    assert "resources CAL" in last_after(n) and "(xcp-builtin)" in lines[-1]
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
    (workspaces.hooks_dir() / "xcp.py").write_text(hook)
    manager._hooks.reload()
    manager.unlock(0x01)
    assert last_after(n) == "XCP resource 0x01 unlocked"
    n += 1
    manager.write("SpeedLimit", "7000")
    assert last_after(n) == "SpeedLimit <- 7000"
    assert struct.unpack_from("<H", demo["xcp_slave"].state.memory, 0x2000)[0] == 7000

    manager.set_polled("EngineSpeed", True)
    wait_until(lambda: len(hub.keys()) and len(hub.get("XCP/EngineSpeed").values) >= 3, timeout=4)
    series = hub.get("XCP/EngineSpeed")
    assert series.unit == "rpm" and 800 <= series.latest <= 3800
    manager.set_polled("EngineSpeed", False)

    manager.disconnect_slave()
    wait_until(lambda: not manager.is_connected)


# --- forgetting an A2L ------------------------------------------------------------------
def test_clearing_the_a2l_leaves_nothing_named(tmp_path):
    """A file that has moved has to be removable, so the pane can say there is
    none rather than naming parameters nothing can read."""
    a2l = tmp_path / "demo.a2l"
    a2l.write_text(resources.path("demo.a2l").read_text(encoding="utf-8"), encoding="utf-8")
    loaded = A2l.load(str(a2l))
    assert loaded.parameters, "the demo A2L has parameters in it"
    assert loaded.path == str(a2l), "it remembers which file it came from"


def test_the_pane_says_which_a2l_and_removes_it(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    from PySide6.QtCore import QSettings

    from pycangui.ui.main_window import MainWindow

    QSettings().clear()
    window = MainWindow()
    a2l = tmp_path / "demo.a2l"
    a2l.write_text(resources.path("demo.a2l").read_text(encoding="utf-8"), encoding="utf-8")
    window.xcp.load_a2l(str(a2l))
    window.ctx.settings.set("xcp.a2l", str(a2l))

    view = window.xcp_view
    assert "demo.a2l" in view.a2l_label.text()
    assert view.tree.topLevelItemCount() > 0

    view._remove_a2l()

    assert window.xcp.a2l is None
    assert view.tree.topLevelItemCount() == 0, "the parameters go with it"
    assert window.ctx.settings.get("xcp.a2l", "") == "", "and it stops being remembered"
    assert not view.remove_a2l_btn.isEnabled(), "nothing left to remove"
    window.close()


def test_an_a2l_that_has_moved_is_reported_at_startup(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    from PySide6.QtCore import QSettings

    from pycangui.core.settings import Settings
    from pycangui.ui.main_window import MainWindow

    QSettings().clear()
    # Written before the window exists, as a workspace carried from elsewhere
    # would have it: the file it names is not there.
    settings = Settings(workspaces.active_dir() / "settings.json")
    settings.set("xcp.a2l", str(tmp_path / "gone.a2l"))

    window = MainWindow()

    assert "gone.a2l" in window.log.toPlainText(), "it says so instead of going quiet"
    assert window.xcp.a2l is None
    window.close()


# --- finding a parameter in a large A2L --------------------------------------------------
def _xcp_window(tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings

    from pycangui.ui.main_window import MainWindow

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    window = MainWindow()
    a2l = tmp_path / "demo.a2l"
    a2l.write_text(resources.path("demo.a2l").read_text(encoding="utf-8"), encoding="utf-8")
    window.xcp.load_a2l(str(a2l))
    return window


def _shown(view):
    tree = view.tree
    return [
        tree.topLevelItem(i).text(0)
        for i in range(tree.topLevelItemCount())
        if not tree.topLevelItem(i).isHidden()
    ]


def test_searching_narrows_the_parameter_list(app, tmp_path, monkeypatch):
    window = _xcp_window(tmp_path, monkeypatch)
    view = window.xcp_view
    everything = _shown(view)
    assert len(everything) > 1

    view.search.setText("speed")
    narrowed = _shown(view)

    assert "EngineSpeed" in narrowed, "by name"
    assert "IdleTarget" in narrowed, "by description -- 'Idle speed target'"
    assert "BatteryVoltage" not in narrowed, "and what does not match is hidden"
    assert len(narrowed) < len(everything)

    view.search.setText("")
    assert _shown(view) == everything, "clearing it brings everything back"
    window.close()


def test_a_parameter_can_be_found_by_address_or_kind(app, tmp_path, monkeypatch):
    """A map file gives an address, not a name, and one kind at a time is
    what somebody calibrating wants."""
    window = _xcp_window(tmp_path, monkeypatch)
    view = window.xcp_view
    param = next(iter(window.xcp.a2l.parameters.values()))

    view.search.setText(f"0x{param.address:x}")
    assert param.name in _shown(view)

    view.search.setText("characteristic")
    kinds = {window.xcp.a2l.parameters[name].kind for name in _shown(view)}
    assert kinds == {"CHARACTERISTIC"}
    window.close()


def test_two_words_must_both_match(app, tmp_path, monkeypatch):
    window = _xcp_window(tmp_path, monkeypatch)
    view = window.xcp_view
    view.search.setText("zzzz speed")
    assert _shown(view) == [], "nothing has both"
    window.close()


def test_showing_only_the_plotted_ones(app, tmp_path, monkeypatch):
    from PySide6.QtCore import Qt

    window = _xcp_window(tmp_path, monkeypatch)
    view = window.xcp_view
    first = view.tree.topLevelItem(0)
    first.setCheckState(5, Qt.Checked)

    view.plotted_only.setChecked(True)

    assert _shown(view) == [first.text(0)], "a filter must not hide what is being polled"
    view.plotted_only.setChecked(False)
    assert len(_shown(view)) > 1
    window.close()


# --- identifiers are asked for, not guessed ---------------------------------------------
def test_the_identifiers_start_empty_and_connect_waits_for_them(app, tmp_path, monkeypatch):
    """XCP on CAN fixes no identifiers, so a default pair would be a guess
    sent to whatever happens to answer on it."""
    from PySide6.QtCore import QSettings

    from pycangui.ui.main_window import MainWindow

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    window = MainWindow()
    view = window.xcp_view

    assert view.cmd_id.text() == "" and view.res_id.text() == ""
    assert not view.connect_btn.isEnabled(), "nothing to connect to yet"

    view.cmd_id.setText("7A0")
    assert not view.connect_btn.isEnabled(), "one of the two is not enough"
    view.res_id.setText("7A1")
    assert view.connect_btn.isEnabled()

    view.res_id.setText("nonsense")
    assert not view.connect_btn.isEnabled(), "and it has to be an identifier"
    window.close()


def test_identifiers_already_chosen_are_still_there_next_time(app, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings

    from pycangui.ui.main_window import MainWindow

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    first = MainWindow()
    first.xcp_view.cmd_id.setText("6A0")
    first.xcp_view.res_id.setText("6A1")
    first.xcp_view._config()  # what pressing Connect does before it connects
    first.close()

    again = MainWindow()
    assert again.xcp_view.cmd_id.text() == "6A0"
    assert again.xcp_view.connect_btn.isEnabled()
    again.close()


def test_nothing_is_called_xcp_in_the_trace_until_the_ids_are_given(app, tmp_path, monkeypatch):
    """A default pair would label frames as XCP on a bus with no XCP on it."""
    from PySide6.QtCore import QSettings

    from pycangui.core.bus import Frame
    from pycangui.ui.main_window import MainWindow

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    window = MainWindow()
    frame = Frame(
        timestamp=0.0,
        channel="CAN",
        can_id=0x7A0,
        extended=False,
        fd=False,
        rx=True,
        data=b"\xff",
    )

    assert window.xcp.classify(frame) is None, "nobody said this was XCP"

    window.xcp.set_ids(0x7A0, 0x7A1, False)
    assert window.xcp.classify(frame) == "XCP cmd"
    window.close()
