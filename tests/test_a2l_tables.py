# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Curves and maps with their axes, record layouts read in full, and the
conversions that are tables and formulas."""

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
from pycangui.xcp import layouts
from pycangui.xcp.a2l import A2l
from pycangui.xcp.manager import XcpManager

FILE = """
/begin MODULE M ""
  /begin MOD_COMMON "" ALIGNMENT_LONG 2 /end MOD_COMMON

  /begin COMPU_METHOD half "" LINEAR "%4.1" "deg" COEFFS_LINEAR 0.5 0 /end COMPU_METHOD
  /begin COMPU_METHOD lookup "" TAB_INTP "%4.1" "bar" COMPU_TAB_REF pressure /end COMPU_METHOD
  /begin COMPU_TAB pressure "" TAB_INTP 3 0 0.0 100 1.0 200 4.0 /end COMPU_TAB
  /begin COMPU_METHOD steps "" TAB_NOINTP "%4.1" "" COMPU_TAB_REF stepped /end COMPU_METHOD
  /begin COMPU_TAB stepped "" TAB_NOINTP 2 1 10 2 20 DEFAULT_VALUE_NUMERIC -1 /end COMPU_TAB
  /begin COMPU_METHOD both_ways "" FORM "%4.1" "degC"
    /begin FORMULA "X1*0.1-40" FORMULA_INV "(X1+40)*10" /end FORMULA
  /end COMPU_METHOD
  /begin COMPU_METHOD sneaky "" FORM "%4.1" ""
    /begin FORMULA "__import__('os').getcwd()" /end FORMULA
  /end COMPU_METHOD

  /begin RECORD_LAYOUT RL_FIXED_CURVE
    FIX_NO_AXIS_PTS_X 4 AXIS_PTS_X 1 UWORD INDEX_INCR DIRECT FNC_VALUES 2 UBYTE COLUMN_DIR DIRECT
  /end RECORD_LAYOUT
  /begin RECORD_LAYOUT RL_COUNTED
    NO_AXIS_PTS_X 1 UBYTE AXIS_PTS_X 2 ULONG INDEX_INCR DIRECT FNC_VALUES 3 UWORD COLUMN_DIR DIRECT
  /end RECORD_LAYOUT
  /begin RECORD_LAYOUT RL_COUNTED_STATIC
    NO_AXIS_PTS_X 1 UBYTE AXIS_PTS_X 2 UWORD INDEX_INCR DIRECT FNC_VALUES 3 UWORD COLUMN_DIR DIRECT
    STATIC_RECORD_LAYOUT
  /end RECORD_LAYOUT
  /begin RECORD_LAYOUT RL_ROWS FNC_VALUES 1 UBYTE ROW_DIR DIRECT /end RECORD_LAYOUT
  /begin RECORD_LAYOUT RL_COLUMNS FNC_VALUES 1 UBYTE COLUMN_DIR DIRECT /end RECORD_LAYOUT
  /begin RECORD_LAYOUT RL_AXIS AXIS_PTS_X 1 SWORD INDEX_INCR DIRECT /end RECORD_LAYOUT
  /begin RECORD_LAYOUT RL_POINTER FNC_VALUES 1 UBYTE COLUMN_DIR PBEFORE /end RECORD_LAYOUT
  /begin RECORD_LAYOUT RL_COUNT_LAST
    AXIS_PTS_X 1 UWORD INDEX_INCR DIRECT NO_AXIS_PTS_X 2 UBYTE FNC_VALUES 3 UWORD COLUMN_DIR DIRECT
  /end RECORD_LAYOUT

  /begin AXIS_PTS Shared "" 0x500 NO_INPUT_QUANTITY RL_AXIS 0 NO_COMPU_METHOD 3 -100 100
  /end AXIS_PTS

  /begin CHARACTERISTIC Fixed4 "" CURVE 0x100 RL_FIXED_CURVE 0 NO_COMPU_METHOD 0 255
    /begin AXIS_DESCR STD_AXIS Speed NO_COMPU_METHOD 4 0 9000 /end AXIS_DESCR
  /end CHARACTERISTIC
  /begin CHARACTERISTIC Counted "" CURVE 0x200 RL_COUNTED 0 NO_COMPU_METHOD 0 9000
    /begin AXIS_DESCR STD_AXIS Speed NO_COMPU_METHOD 8 0 9000 /end AXIS_DESCR
  /end CHARACTERISTIC
  /begin CHARACTERISTIC CountedStatic "" CURVE 0x300 RL_COUNTED_STATIC 0 NO_COMPU_METHOD 0 9000
    /begin AXIS_DESCR STD_AXIS Speed NO_COMPU_METHOD 8 0 9000 /end AXIS_DESCR
  /end CHARACTERISTIC
  /begin CHARACTERISTIC ByRows "" MAP 0x600 RL_ROWS 0 half 0 100
    /begin AXIS_DESCR COM_AXIS Speed NO_COMPU_METHOD 3 -100 100 AXIS_PTS_REF Shared /end AXIS_DESCR
    /begin AXIS_DESCR FIX_AXIS Load NO_COMPU_METHOD 2 0 100 FIX_AXIS_PAR 10 2 2 /end AXIS_DESCR
  /end CHARACTERISTIC
  /begin CHARACTERISTIC ByColumns "" MAP 0x700 RL_COLUMNS 0 half 0 100
    /begin AXIS_DESCR COM_AXIS Speed NO_COMPU_METHOD 3 -100 100 AXIS_PTS_REF Shared /end AXIS_DESCR
    /begin AXIS_DESCR FIX_AXIS Load NO_COMPU_METHOD 2 0 100
      /begin FIX_AXIS_PAR_LIST 5 7 /end FIX_AXIS_PAR_LIST
    /end AXIS_DESCR
  /end CHARACTERISTIC
  /begin CHARACTERISTIC Rescaled "" CURVE 0x800 RL_COLUMNS 0 NO_COMPU_METHOD 0 255
    /begin AXIS_DESCR RES_AXIS Speed NO_COMPU_METHOD 4 0 9000 /end AXIS_DESCR
  /end CHARACTERISTIC
  /begin CHARACTERISTIC Pointed "" CURVE 0x900 RL_POINTER 0 NO_COMPU_METHOD 0 255
    /begin AXIS_DESCR FIX_AXIS Speed NO_COMPU_METHOD 2 0 9 FIX_AXIS_PAR_DIST 0 1 2 /end AXIS_DESCR
  /end CHARACTERISTIC
  /begin CHARACTERISTIC CountLast "" CURVE 0xA00 RL_COUNT_LAST 0 NO_COMPU_METHOD 0 255
    /begin AXIS_DESCR STD_AXIS Speed NO_COMPU_METHOD 4 0 9000 /end AXIS_DESCR
  /end CHARACTERISTIC
  /begin CHARACTERISTIC Cube "" CUBOID 0xB00 RL_COLUMNS 0 NO_COMPU_METHOD 0 255
  /end CHARACTERISTIC

  /begin MEASUREMENT Pressure "" UWORD lookup 0 0 0 4 ECU_ADDRESS 0x1000 /end MEASUREMENT
  /begin MEASUREMENT Step "" UBYTE steps 0 0 0 30 ECU_ADDRESS 0x1002 /end MEASUREMENT
  /begin CHARACTERISTIC Temperature "" VALUE 0x1004 RL_COLUMNS 0 both_ways -40 200
  /end CHARACTERISTIC
  /begin MEASUREMENT Sneaky "" UWORD sneaky 0 0 0 4 ECU_ADDRESS 0x1006 /end MEASUREMENT
/end MODULE
"""


@pytest.fixture
def a2l():
    return A2l.parse(FILE)


# --- record layouts ------------------------------------------------------------------------------
def test_each_part_is_laid_after_the_last_at_its_types_alignment():
    layout = layouts.parse(
        "RL",
        "NO_AXIS_PTS_X 1 UBYTE AXIS_PTS_X 2 ULONG INDEX_INCR DIRECT "
        "FNC_VALUES 3 UWORD COLUMN_DIR DIRECT".split(),
    )
    where = layout.place(0x100, {"X": 3}, {"X": 8}, {})
    assert where[("NO_AXIS_PTS", "X")] == 0x100
    assert where[("AXIS_PTS", "X")] == 0x104, "a ULONG starts on four, by default"
    assert where[("FNC_VALUES", "")] == 0x104 + 3 * 4
    two = layout.place(0x100, {"X": 3}, {"X": 8}, {"ULONG": 2})
    assert two[("AXIS_PTS", "X")] == 0x102, "the module's ALIGNMENT_LONG 2"


def test_a_static_layout_keeps_room_for_the_most_points():
    layout = layouts.parse(
        "RL",
        "NO_AXIS_PTS_X 1 UBYTE AXIS_PTS_X 2 UWORD INDEX_INCR DIRECT "
        "FNC_VALUES 3 UWORD COLUMN_DIR DIRECT STATIC_RECORD_LAYOUT".split(),
    )
    assert layout.static
    assert layout.place(0, {"X": 3}, {"X": 8}, {})[("FNC_VALUES", "")] == 2 + 8 * 2


# --- curves --------------------------------------------------------------------------------------
def test_a_curve_with_a_fixed_number_of_points_is_laid_out_on_reading_the_file(a2l):
    curve = a2l.parameters["Fixed4"]
    assert curve.readable and curve.sizes == (4,) and curve.datatype == "UBYTE"
    assert curve.address == 0x100 + 4 * 2, "the values after the four points"
    assert a2l.find("Fixed4:x[3]").address == 0x100 + 3 * 2
    assert a2l.find("Fixed4[3]").address == 0x108 + 3
    assert a2l.find("Fixed4:x[2]").writable, "a calibrator moves the points too"


def test_a_curve_whose_count_is_in_the_slave_waits_for_it(a2l):
    curve = a2l.parameters["Counted"]
    assert curve.readable and curve.counted == ("X",) and not curve.sizes
    [(holder, letter, address, datatype, _owner)] = a2l.counts_to_read(curve)
    assert (holder, letter, address, datatype) == ("Counted", "X", 0x200, "UBYTE")
    resolved = a2l.resolve("Counted", {("Counted", "X"): 3})
    # count UBYTE at 0x200; ULONG points from 0x202 (ALIGNMENT_LONG 2); values after three of them
    assert a2l.find("Counted:x[0]").address == 0x202
    assert resolved.address == 0x202 + 3 * 4 and resolved.sizes == (3,)
    assert a2l.find("Counted[3]") is None, "three values, not eight"


def test_a_static_layout_puts_the_values_after_room_for_all_the_points(a2l):
    resolved = a2l.resolve("CountedStatic", {("CountedStatic", "X"): 3})
    assert resolved.sizes == (3,) and resolved.address == 0x302 + 8 * 2


def test_a_count_beyond_the_most_is_refused(a2l):
    with pytest.raises(ValueError):
        a2l.resolve("Counted", {("Counted", "X"): 9})


# --- maps ----------------------------------------------------------------------------------------
def test_a_map_is_named_x_then_y_whichever_way_it_is_stored(a2l):
    rows, columns = a2l.parameters["ByRows"], a2l.parameters["ByColumns"]
    assert rows.sizes == columns.sizes == (3, 2)
    # x = 2, y = 1: row by row a row is the x values; column by column, the y values.
    assert a2l.find("ByRows[2][1]").address == 0x600 + 1 * 3 + 2
    assert a2l.find("ByColumns[2][1]").address == 0x700 + 2 * 2 + 1


def test_a_shared_axis_and_fixed_ones(a2l):
    x, y = a2l.parameters["ByRows"].axes
    assert x.attribute == "COM_AXIS" and x.points == "Shared"
    assert a2l.find("Shared[2]").address == 0x500 + 2 * 2
    assert y.fixed == (10, 14), "FIX_AXIS_PAR: 10, then 2 to the 2 further on"
    assert a2l.parameters["ByColumns"].axes[1].fixed == (5, 7)
    assert a2l.parameters["Pointed"].unreadable, "values reached through a pointer"


@pytest.mark.parametrize("name", ["Rescaled", "Pointed", "CountLast", "Cube"])
def test_what_cannot_be_laid_out_says_why(a2l, name):
    assert a2l.parameters[name].unreadable


def test_one_value_of_a_map_is_written_like_one_of_an_array(a2l):
    cell = a2l.find("ByRows[0][0]")
    assert cell.writable and cell.conversion.unit == "deg"
    assert not a2l.parameters["ByRows"].writable, "the map as a whole is not typed in"


# --- conversions ---------------------------------------------------------------------------------
def test_a_table_is_interpolated_and_turned_back(a2l):
    pressure = a2l.parameters["Pressure"].conversion
    assert pressure.to_phys(150) == pytest.approx(2.5)
    assert pressure.to_phys(500) == pytest.approx(4.0), "beyond the end is the end"
    assert pressure.to_raw(2.5) == pytest.approx(150)


def test_a_table_without_interpolation_has_a_default(a2l):
    steps = a2l.parameters["Step"].conversion
    assert steps.to_phys(2) == 20 and steps.to_phys(3) == -1


def test_a_formula_is_worked_both_ways_where_the_file_gives_both(a2l):
    temperature = a2l.parameters["Temperature"]
    assert temperature.conversion.to_phys(650) == pytest.approx(25)
    assert temperature.conversion.to_raw(25) == pytest.approx(650)
    assert temperature.writable


def test_a_formula_that_is_more_than_arithmetic_is_not_run(a2l):
    sneaky = a2l.parameters["Sneaky"]
    assert not sneaky.conversion.exact and sneaky.unit == "raw"


# --- against the demo slaves -------------------------------------------------------------------
def wait_until(pred, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not pred():
        QCoreApplication.processEvents()
        if time.monotonic() > deadline and not pred():
            raise AssertionError("timed out")
        time.sleep(0.005)


@pytest.fixture(params=["xcp_slave", "ccp_slave"])
def demo(request, app, tmp_path, monkeypatch, demo_device):
    kind = request.param
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    ctx = Context(log=print)
    hooks = Hooks(ctx)
    bus = BusManager()
    manager = XcpManager(bus, hooks, SignalHub(), ctx)
    manager.set_component("ccp-builtin" if kind == "ccp_slave" else "xcp-builtin")
    bus.connect_bus("virtual", f"vcan_{kind}_tables", 500000, False)
    slave = demo_device(bus, kinds=[kind])[kind].state
    manager.set_ids(*((0x7B0, 0x7B1) if kind == "ccp_slave" else (0x7A0, 0x7A1)), False)
    if kind == "ccp_slave":
        manager.set_station(1)
    hook = "def compute_key(resource, seed, *, ctx):\n    return bytes(b ^ 0xFF for b in seed)\n"
    (workspaces.hooks_dir() / "xcp.py").write_text(hook)
    hooks.reload()
    manager.load_a2l(str(resources.path("demo.a2l")))
    manager.connect_slave()
    wait_until(lambda: manager.is_connected)
    values: dict[str, float] = {}
    manager.value.connect(lambda name, value: values.__setitem__(name, value))
    order = ">" if kind == "ccp_slave" else "<"
    yield manager, slave, values, order
    manager.shutdown()
    bus.disconnect_bus()


def test_a_curve_is_read_with_the_number_of_points_the_slave_keeps(demo):
    manager, _slave, values, _order = demo
    reshaped = []
    manager.reshaped.connect(reshaped.append)
    manager.read("IdleCurve")
    wait_until(lambda: "IdleCurve[5]" in values)
    assert reshaped == ["IdleCurve"]
    assert manager.a2l.parameters["IdleCurve"].sizes == (6,)
    assert [values[f"IdleCurve:x[{i}]"] for i in range(6)] == [-20, 0, 20, 40, 60, 80]
    assert values["IdleCurve[0]"] == 1400 and values["IdleCurve[5]"] == 850


def test_a_map_is_read_with_its_shared_axis_and_a_cell_written(demo):
    manager, slave, values, _order = demo
    manager.read("IgnitionMap")
    wait_until(lambda: "IgnitionMap[4][4]" in values)
    assert [values[f"SpeedAxis[{i}]"] for i in range(5)] == [800, 2000, 3500, 5000, 6500]
    # speed 2, load 3: 8 + 6*2 - 2*3 degrees
    assert values["IgnitionMap[2][3]"] == pytest.approx(14)
    manager.unlock(0x01)
    manager.write("IgnitionMap[2][3]", "20.5")
    wait_until(lambda: slave.memory[0x2300 + 5 * 3 + 2] == 41)


def test_an_axis_point_kept_with_the_curve_is_written_where_it_lives(demo):
    manager, slave, values, order = demo
    manager.read("IdleCurve")
    wait_until(lambda: "IdleCurve[5]" in values)
    manager.unlock(0x01)
    manager.write("IdleCurve:x[1]", "5")
    address = 0x2102 + 2
    wait_until(lambda: struct.unpack_from(f"{order}h", slave.memory, address)[0] == 5)


def test_the_pane_opens_a_map_as_a_table(demo):
    from pycangui.ui.xcp_view import XcpView

    manager, _slave, values, _order = demo
    view = XcpView(manager, manager._ctx)
    item = view._items["IgnitionMap"]
    view._on_double_clicked(item, 0)
    window = view._tables["IgnitionMap"]
    wait_until(lambda: "IgnitionMap[4][4]" in values)
    wait_until(lambda: window.table.rowCount() == 6)
    assert window.table.columnCount() == 6, "five speeds and the load axis"
    wait_until(lambda: float(window.table.item(4, 3).text() or "nan") == 14)
    assert float(window.table.item(0, 1).text()) == 800, "the speeds along the top"
    assert float(window.table.item(1, 0).text()) == 0, "the loads down the side"
    window.close()
    view.deleteLater()
