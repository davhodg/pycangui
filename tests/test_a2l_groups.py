# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""GROUPs and FUNCTIONs, FORMAT and PHYS_UNIT, and text (ASCII) characteristics."""

import pytest
from PySide6.QtCore import Qt
from test_a2l_tables import demo, wait_until  # noqa: F401

from pycangui.xcp.a2l import A2l

FILE = """
/begin MODULE M ""
  /begin COMPU_METHOD tenths "" LINEAR "%6.1" "V" COEFFS_LINEAR 0.1 0 /end COMPU_METHOD
  /begin COMPU_METHOD bent "" FORM "%6.3" "x" /begin FORMULA "sin(" /end FORMULA /end COMPU_METHOD
  /begin RECORD_LAYOUT RL_BYTES FNC_VALUES 1 UBYTE ROW_DIR DIRECT /end RECORD_LAYOUT
  /begin RECORD_LAYOUT RL_WORDS FNC_VALUES 1 UWORD ROW_DIR DIRECT /end RECORD_LAYOUT

  /begin MEASUREMENT Volts "" UWORD tenths 0 0 0 100 ECU_ADDRESS 0x10 /end MEASUREMENT
  /begin MEASUREMENT Precise "" UWORD tenths 0 0 0 100 ECU_ADDRESS 0x12 FORMAT "%8.3"
  /end MEASUREMENT
  /begin MEASUREMENT Millivolts "" UWORD tenths 0 0 0 100 ECU_ADDRESS 0x14 PHYS_UNIT "mV"
  /end MEASUREMENT
  /begin MEASUREMENT Raw "" UWORD bent 0 0 0 100 ECU_ADDRESS 0x16 PHYS_UNIT "Nm" /end MEASUREMENT
  /begin MEASUREMENT Listed "" UBYTE NO_COMPU_METHOD 0 0 0 9 ECU_ADDRESS 0x18
    /begin FUNCTION_LIST Older /end FUNCTION_LIST
  /end MEASUREMENT

  /begin CHARACTERISTIC Serial "" ASCII 0x100 RL_BYTES 0 NO_COMPU_METHOD 0 255 NUMBER 8
  /end CHARACTERISTIC
  /begin CHARACTERISTIC Wide "" ASCII 0x200 RL_WORDS 0 NO_COMPU_METHOD 0 255 NUMBER 8
  /end CHARACTERISTIC
  /begin CHARACTERISTIC Endless "" ASCII 0x300 RL_BYTES 0 NO_COMPU_METHOD 0 255
  /end CHARACTERISTIC

  /begin GROUP Top "the top" ROOT
    /begin REF_MEASUREMENT Volts /end REF_MEASUREMENT
    /begin SUB_GROUP Middle /end SUB_GROUP
  /end GROUP
  /begin GROUP Middle "" /begin REF_MEASUREMENT Precise /end REF_MEASUREMENT
    /begin SUB_GROUP Bottom /end SUB_GROUP
  /end GROUP
  /begin GROUP Bottom "" /begin REF_CHARACTERISTIC Serial /end REF_CHARACTERISTIC
    /begin SUB_GROUP Top /end SUB_GROUP
  /end GROUP
  /begin GROUP Loose "inside nothing" /begin REF_MEASUREMENT Raw /end REF_MEASUREMENT /end GROUP
  /begin FUNCTION Control ""
    /begin DEF_CHARACTERISTIC Serial /end DEF_CHARACTERISTIC
    /begin IN_MEASUREMENT Volts /end IN_MEASUREMENT
    /begin OUT_MEASUREMENT Millivolts /end OUT_MEASUREMENT
  /end FUNCTION
/end MODULE
"""


@pytest.fixture
def a2l():
    return A2l.parse(FILE)


# --- groups and functions ---------------------------------------------------------------------
def test_a_group_holds_what_its_groups_hold_however_deep(a2l):
    assert a2l.members("Middle") == {"Precise", "Serial", "Volts"}, "Bottom puts Top back in"
    assert a2l.members("Top") == {"Volts", "Precise", "Serial"}


def test_groups_are_listed_each_under_the_one_it_is_in(a2l):
    listed = [(depth, group.name) for depth, group in a2l.group_tree("GROUP")]
    assert listed[:3] == [(0, "Top"), (1, "Middle"), (2, "Bottom")]
    assert (0, "Loose") in listed, "a group inside no other is at the top too"


def test_a_function_lists_what_it_defines_and_uses(a2l):
    assert a2l.members("Control") == {"Serial", "Volts", "Millivolts"}
    assert a2l.members("Older") == {"Listed"}, "from a parameter's own FUNCTION_LIST"


# --- format and unit --------------------------------------------------------------------------
def test_a_value_is_shown_to_its_format(a2l):
    assert a2l.parameters["Volts"].decimals == 1, "the conversion's %6.1"
    assert a2l.parameters["Precise"].decimals == 3, "its own FORMAT wins"


def test_its_own_unit_wins_over_its_conversions(a2l):
    assert a2l.parameters["Volts"].unit == "V"
    assert a2l.parameters["Millivolts"].unit == "mV"


def test_a_value_shown_raw_is_raw_whatever_it_says(a2l):
    raw = a2l.parameters["Raw"]
    assert raw.unit == "raw" and raw.decimals is None


# --- text -------------------------------------------------------------------------------------
def test_text_is_its_length_in_bytes(a2l):
    serial = a2l.parameters["Serial"]
    assert serial.is_text and serial.length == 8 and serial.writable
    assert not serial.is_array, "one string, not eight values"


@pytest.mark.parametrize("name", ["Wide", "Endless"])
def test_text_that_is_not_bytes_or_has_no_length_is_not_read(a2l, name):
    assert a2l.parameters[name].unreadable


# --- against the demo slaves ------------------------------------------------------------------
def test_text_is_read_and_written(demo):  # noqa: F811
    manager, slave, _values, _order = demo
    said = {}
    manager.text.connect(lambda name, text: said.__setitem__(name, text))
    manager.read("PartNumber")
    wait_until(lambda: "PartNumber" in said)
    assert said["PartNumber"] == "PCG-DEMO-0001"
    manager.unlock(0x01)
    manager.write("PartNumber", "PCG-NEW")
    wait_until(lambda: bytes(slave.memory[0x2400:0x2410]) == b"PCG-NEW".ljust(16, b"\x00"))


def test_text_longer_than_its_space_is_not_written(demo):  # noqa: F811
    manager, slave, _values, _order = demo
    before = bytes(slave.memory[0x2400:0x2410])
    lines = []
    manager.result.connect(lines.append)
    manager.write("PartNumber", "X" * 17)
    wait_until(lambda: lines)
    assert bytes(slave.memory[0x2400:0x2410]) == before


def test_a_value_is_shown_to_the_files_format(demo):  # noqa: F811
    manager, _slave, values, _order = demo
    manager.read("BatteryVoltage")
    wait_until(lambda: "BatteryVoltage" in values)
    assert manager.shown("BatteryVoltage", values["BatteryVoltage"]) == "13.20", "%.2f"


def test_the_pane_shows_one_group_at_a_time(demo):  # noqa: F811
    from pycangui.ui.xcp_view import XcpView

    manager, _slave, _values, _order = demo
    view = XcpView(manager, manager._ctx)
    assert view.group_box.count() > 1
    shown = lambda: {  # noqa: E731
        name for name, item in view._items.items() if item.parent() is None and not item.isHidden()
    }
    everything = shown()
    view.group_box.setCurrentIndex(view.group_box.findData("Idle"))
    assert shown() == {"IdleTarget", "IdleCurve"}
    view.group_box.setCurrentIndex(view.group_box.findData("Engine"))
    assert shown() == manager.a2l.members("Engine"), "with the groups inside it"
    view.group_box.setCurrentIndex(0)
    assert shown() == everything
    part = view._items["PartNumber"]
    assert part.data(5, Qt.CheckStateRole) is None, "no Plot tick: text is not plotted"
    view.deleteLater()
