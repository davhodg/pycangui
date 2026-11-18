# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""A custom pane over a UDS ECU: its data identifiers, by number.

Against the demo ECU, through the real UDS manager. What a DID means comes
from the ``did_*`` hooks the UDS pane uses, so the two agree; and every read
or write gets exactly one answer, a refusal included, because a pane's polling
waits on them.
"""

import time

import pytest
from PySide6.QtCore import QCoreApplication, QSettings

from pycangui.core import workspaces
from pycangui.core.bus import BusManager
from pycangui.core.context import Context
from pycangui.core.hooks import Hooks
from pycangui.custom_panes import model
from pycangui.custom_panes.model import UDS, CustomPane, Field
from pycangui.custom_panes.uds_source import UdsSource
from pycangui.uds import UdsConfig
from pycangui.uds.manager import UdsManager

VIN, SETPOINT, MISSING = 0xF190, 0x0102, 0x9999


def wait_until(pred, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not pred():
        QCoreApplication.processEvents()
        if time.monotonic() > deadline and not pred():
            raise AssertionError("timed out")
        time.sleep(0.005)


@pytest.fixture
def stack(app, tmp_path, monkeypatch, demo_device):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    ctx = Context(log=print)
    bus = BusManager()
    manager = UdsManager(bus, Hooks(ctx), ctx)
    bus.connect_bus("virtual", "vcan_uds_source", 500000, False)
    demo = demo_device(bus, kinds=["uds_server"])
    source = UdsSource(manager)
    heard = []
    source.value.connect(lambda *answer: heard.append(answer))
    yield manager, source, heard, demo
    manager.shutdown()
    bus.disconnect_bus()


def opened(manager):
    manager.open(UdsConfig())
    wait_until(lambda: manager.is_open)


def unlocked(manager):
    """Extended session, and the demo ECU's key: its seed with every bit turned over."""
    hook = "def security_key(level, seed, *, ctx):\n"
    hook += "    return bytes(b ^ 0xFF for b in seed)\n"
    (workspaces.hooks_dir() / "uds.py").write_text(hook)
    manager._hooks.reload()
    lines = []
    manager.result.connect(lines.append)
    manager.change_session(3)
    wait_until(lambda: len(lines) >= 1)
    manager.unlock(1)
    wait_until(lambda: len(lines) >= 2)


def answer(heard, ask):
    before = len(heard)
    ask()
    wait_until(lambda: len(heard) > before)
    assert len(heard) == before + 1, "one answer to one request"
    return heard[-1]


# --- the source -------------------------------------------------------------------------
def test_an_identifier_is_read_by_its_number(stack):
    manager, source, heard, _demo = stack
    opened(manager)
    did, value, error = answer(heard, lambda: source.request(VIN))
    assert (did, error) == (VIN, None)
    assert "PYCANGUI0DEMO0001" in str(value)


def test_one_the_ecu_does_not_have_is_answered_with_why(stack):
    manager, source, heard, _demo = stack
    opened(manager)
    did, value, error = answer(heard, lambda: source.request(MISSING))
    assert did == MISSING and value is None and error


def test_nothing_open_is_answered_too(stack):
    _manager, source, heard, _demo = stack
    _did, value, error = answer(heard, lambda: source.request(VIN))
    assert value is None and error


def test_a_locked_write_says_so_and_changes_nothing(stack):
    manager, source, heard, demo = stack
    opened(manager)
    _did, value, error = answer(heard, lambda: source.write(SETPOINT, "00 10"))
    assert value is None and error
    assert demo["uds_server"].state.identifiers[SETPOINT] == b"\x00\x00"


def test_a_write_is_sent_and_read_back(stack):
    manager, source, heard, demo = stack
    opened(manager)
    unlocked(manager)
    did, _value, error = answer(heard, lambda: source.write(SETPOINT, "00 10"))
    assert (did, error) == (SETPOINT, None)
    assert demo["uds_server"].state.identifiers[SETPOINT] == b"\x00\x10"


def test_a_hook_that_reads_a_number_gives_a_number(stack):
    """So a number field shows it, steps it, and plots it."""
    manager, source, heard, _demo = stack
    hook = "def did_decode(did, data, *, ctx):\n"
    hook += "    return str(int.from_bytes(data, 'big')) if did == 0x0102 else None\n"
    (workspaces.hooks_dir() / "uds.py").write_text(hook)
    manager._hooks.reload()
    opened(manager)
    _did, value, error = answer(heard, lambda: source.request(SETPOINT))
    assert error is None and value == 0 and isinstance(value, int)


def test_the_list_to_pick_from_is_the_hooks_shortlist(stack):
    manager, source, _heard, _demo = stack
    offered = {did for did, _name, _access in source.objects()}
    assert offered == set(manager.did_choices())
    assert VIN in offered
    assert source.display(VIN).name, "named by the hook, as the UDS pane names it"


# --- on a pane ---------------------------------------------------------------------------
@pytest.fixture
def window(app, tmp_path, monkeypatch, demo_device):
    from pycangui.ui.main_window import MainWindow

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    win = MainWindow()
    bus = win.channels.active_bus()
    bus.connect_bus("virtual", "vcan_uds_pane", 500000, False)
    demo_device(bus, kinds=["uds_server"])
    opened(win.uds)
    yield win
    win.close()


def test_a_uds_pane_reads_from_the_ecu_as_it_opens(app, window):
    model.save(
        "ids",
        CustomPane(title="Identification", protocol=UDS, fields=[Field(did=VIN, kind="text")]),
    )
    window.open_custom_pane("ids")
    view = window._custom_pane_view("ids")
    assert isinstance(view.source, UdsSource)
    vin = view._widgets[0]
    wait_until(lambda: vin._raw is not None)
    assert "PYCANGUI0DEMO0001" in vin.edit.text()


def test_a_did_picked_or_typed_arrives_as_text(app, window):
    from pycangui.ui.custom_pane_view import AddObjects

    picker = AddObjects(None, UdsSource(window.uds), UDS)
    entry = next(e for e in picker._entries if e[0] == VIN)
    assert picker._as_field(*entry).kind == "text"
    picker.index.setText("F190")
    typed = picker._typed()
    assert typed.did == VIN and typed.kind == "text"
    picker.deleteLater()


def test_a_text_field_writes_what_is_typed(app):
    from pycangui.canopen.display import Display
    from pycangui.ui import field_widgets

    widget = field_widgets.build(Field(did=SETPOINT, kind="text"), Display())
    written = []
    widget.write_requested.connect(lambda key, raw: written.append((key, raw)))
    widget.set_value(SETPOINT, "00 00", None)
    widget.edit.setText("00 10")
    widget._on_entered()
    assert written == [(SETPOINT, "00 10")]
