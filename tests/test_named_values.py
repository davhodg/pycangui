# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""A value that has a name is written one way in every pane: ``Run (1)``."""

import struct
import time

import pytest
from PySide6.QtCore import QCoreApplication, QSettings

from pycangui import resources
from pycangui.canopen.display import Display
from pycangui.canopen.display import text as as_shown
from pycangui.core.bus import Frame
from pycangui.core.named_values import named, number_in, plain, shown, with_its_name
from pycangui.core.signals import SignalHub
from pycangui.ui.signals_view import MAXIMUM, MINIMUM, SignalsView

CHOICES = {0: "Idle", 1: "Run", 2: "Fault"}


def wait_until(pred, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not pred():
        QCoreApplication.processEvents()
        if time.monotonic() > deadline:
            raise AssertionError("timed out")
        time.sleep(0.005)


# --- the form itself ------------------------------------------------------------------------
@pytest.mark.parametrize("typed", ["1", "Run", "Run (1)", " 1 ", "1.0"])
def test_any_of_the_three_ways_in_means_the_same_value(typed):
    assert with_its_name(typed, CHOICES) == named(1, "Run")
    assert plain(typed, CHOICES) in ("1", "1.0"), "and it is a number again for whatever parses it"


def test_what_the_table_does_not_name_is_left_as_it_was_typed():
    assert with_its_name("7", CHOICES) == "7" and plain("7", CHOICES) == "7"
    assert with_its_name("Nonsense", CHOICES) == "Nonsense"
    assert with_its_name("5", {}) == "5", "no table, nothing to do"
    assert shown(1.5, CHOICES) is None and shown(1.0, CHOICES) == named(1, "Run")
    assert number_in(named(-3, "Reverse")) == -3 and number_in("Reverse") is None


# --- CANopen: name first, and typed back any of the three ways ----------------------------
def test_the_canopen_tree_writes_the_name_first_and_reads_it_back(tmp_path):
    from pycangui.canopen import load_od
    from pycangui.ui.canopen_view import _typed_value

    display = Display(choices=CHOICES)
    assert as_shown(display, 2) == named(2, "Fault")

    variable = load_od(resources.path("demo.eds")).get_variable(0x2001, 0)
    for typed in (as_shown(display, 2), "Fault", "2"):
        assert _typed_value(variable, display, typed) == (2, None), typed


# --- Signals and Plot ------------------------------------------------------------------------
def test_the_signal_list_shows_a_named_value_with_its_name(app):
    hub = SignalHub()
    for i, value in enumerate((0, 2, 1)):
        hub.push("DBC Status", "Mode", i * 0.1, value)
    hub.push("DBC Status", "Speed", 0.0, 1.0)
    hub.set_choices("DBC Status/Mode", CHOICES)
    view = SignalsView(hub)
    view.set_columns(["Min", "Max"])
    view.show()
    view._refresh_values()

    mode, speed = view._items["DBC Status/Mode"], view._items["DBC Status/Speed"]
    assert mode.text(1) == named(1, "Run")
    assert mode.text(MINIMUM) == named(0, "Idle") and mode.text(MAXIMUM) == named(2, "Fault")
    assert speed.text(1) == "1", "a signal with no names is a number, as before"
    assert list(hub.get("DBC Status/Mode").values) == [0.0, 2.0, 1.0], "the samples stay numbers"
    view.close()


def test_a_decoded_dbc_signal_arrives_with_its_names(app, tmp_path, monkeypatch):
    from pycangui.ui.main_window import MainWindow

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path / "home"))
    QSettings().clear()
    dbc = tmp_path / "status.dbc"
    dbc.write_text(
        'VERSION ""\nNS_ :\nBS_:\nBU_: ECU\n'
        "BO_ 256 Status: 2 ECU\n"
        ' SG_ Mode : 0|8@1+ (1,0) [0|3] "" ECU\n'
        ' SG_ Level : 8|8@1+ (1,0) [0|255] "" ECU\n'
        'VAL_ 256 Mode 0 "Idle" 1 "Run" 2 "Fault" ;\n',
        encoding="utf-8",
    )
    window = MainWindow()
    try:
        assert window._load_dbc(str(dbc))
        frame = Frame(1.0, "CAN", 0x100, False, False, True, bytes([2, 9]))
        window._decode_frames([frame])
        assert window.signals.get("DBC Status/Mode").choices == CHOICES
        assert window.signals.get("DBC Status/Level").choices == {}
    finally:
        window.close()


# --- a CANopen RPDO row in Transmit ----------------------------------------------------------
def test_an_rpdo_variable_with_named_values_is_picked_from_a_list(
    app, tmp_path, monkeypatch, demo_device
):
    from pycangui.canopen.manager import CanopenManager
    from pycangui.core.bus import BusManager
    from pycangui.core.context import Context
    from pycangui.core.dbc import DbcDecoder
    from pycangui.ui.tx_view import COL_DATA, ROLE_CHOICES, TxView

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    ctx = Context(log=print)
    bus = BusManager()
    canopen = CanopenManager(bus)
    bus.connect_bus("virtual", "vcan_named", 500000, False)
    demo = demo_device(bus, kinds=["canopen_device"])
    try:
        canopen.load_eds(5, str(resources.path("demo.eds")))
        wait_until(lambda: canopen.rpdos(5))
        number = canopen.rpdos(5)[0][0]
        # As a hook would name them: the EDS itself has no key for it.
        real = canopen.display
        speeds = {0: "Stop", 100: "Slow", 1000: "Fast"}
        monkeypatch.setattr(
            canopen,
            "display",
            lambda node, index, sub: (
                Display(choices=speeds) if (index, sub) == (0x2001, 0) else real(node, index, sub)
            ),
        )
        assert list(canopen.rpdo_choices(5, number).values()) == [speeds]

        view = TxView(bus, ctx, DbcDecoder(), canopen)
        row = view.add_message({"kind": "rpdo", "node": 5, "pdo": number, "period": 100})
        child = view.item(row).child(0)
        assert [name for _n, name in child.data(COL_DATA, ROLE_CHOICES)] == ["Stop", "Slow", "Fast"]
        assert child.text(COL_DATA) == named(0, "Stop")

        child.setText(COL_DATA, "Slow")
        assert child.text(COL_DATA) == named(100, "Slow")
        assert view.item(row).text(COL_DATA) == struct.pack("<h", 100).hex(" ").upper()
    finally:
        demo.stop_all()
        canopen.shutdown()
        bus.disconnect_bus()
