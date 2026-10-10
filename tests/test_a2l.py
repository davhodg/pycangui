# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""The A2L reader, on a file written the way real ones are: characteristics
that take their type from a record layout, fields read by position, names for
values, bit masks, byte order, and the things that are listed but not read."""

import time

import pytest
from PySide6.QtCore import QCoreApplication

from pycangui.core.bus import BusManager
from pycangui.core.context import Context
from pycangui.core.hooks import Hooks
from pycangui.core.signals import SignalHub
from pycangui.xcp import ConnectInfo
from pycangui.xcp.a2l import A2l
from pycangui.xcp.manager import XcpManager

REAL = """
ASAP2_VERSION 1 71
/begin PROJECT P "a project"
  /begin HEADER "" VERSION "1" /end HEADER
  /begin MODULE M "a module"
    /begin MOD_COMMON "" BYTE_ORDER MSB_FIRST ALIGNMENT_WORD 2 /end MOD_COMMON

    /* a comment with /begin CHARACTERISTIC in it, and "a quote" */
    // and a line comment: /end MODULE

    /begin RECORD_LAYOUT RL_UWORD FNC_VALUES 1 UWORD COLUMN_DIR DIRECT /end RECORD_LAYOUT
    /begin RECORD_LAYOUT RL_FLOAT FNC_VALUES 1 FLOAT32_IEEE COLUMN_DIR DIRECT /end RECORD_LAYOUT
    /begin RECORD_LAYOUT RL_ODD FNC_VALUES 1 A_UINT128 COLUMN_DIR DIRECT /end RECORD_LAYOUT
    /begin RECORD_LAYOUT RL_ROWS FNC_VALUES 1 UWORD ROW_DIR DIRECT /end RECORD_LAYOUT
    /begin RECORD_LAYOUT RL_COUNTED NO_AXIS_PTS_X 1 UBYTE FNC_VALUES 2 UWORD COLUMN_DIR DIRECT
    /end RECORD_LAYOUT

    /begin COMPU_METHOD rpm "speed" RAT_FUNC "%6.1" "rpm" COEFFS 0 4 0 0 0 1 /end COMPU_METHOD
    /begin COMPU_METHOD volts "volts" LINEAR "%5.2" "V" COEFFS_LINEAR 0.01 -5 /end COMPU_METHOD
    /begin COMPU_METHOD same "same" IDENTICAL "%3.0" "counts" /end COMPU_METHOD
    /begin COMPU_METHOD curve "bent" RAT_FUNC "%6.1" "Nm" COEFFS 1 2 3 0 0 1 /end COMPU_METHOD
    /begin COMPU_METHOD hertz "one over" RAT_FUNC "%6.1" "Hz" COEFFS 0 0 1000 0 1 0
    /end COMPU_METHOD
    /begin COMPU_METHOD sum "formula" FORM "%6.1" "K"
      /begin FORMULA "X1+273" /end FORMULA
    /end COMPU_METHOD
    /begin COMPU_METHOD state "state" TAB_VERB "%2.0" "" COMPU_TAB_REF state_names /end COMPU_METHOD
    /begin COMPU_VTAB state_names "" TAB_VERB 3 0 "Off" 1 "Run" 2 "Fault, see BIT_MASK"
      DEFAULT_VALUE "unknown"
    /end COMPU_VTAB
    /begin COMPU_METHOD gear "gear" TAB_VERB "%2.0" "" COMPU_TAB_REF gear_names /end COMPU_METHOD
    /begin COMPU_VTAB_RANGE gear_names "" 2 0 0 "Neutral" 1 6 "Forward" /end COMPU_VTAB_RANGE

    /begin MEASUREMENT EngineSpeed "Engine speed, said \\"fast\\"" UWORD rpm 0 0 0 8000
      ECU_ADDRESS 0x1000
      /begin IF_DATA XCP /begin DAQ_EVENT FIXED_EVENT_LIST EVENT 1 /end DAQ_EVENT /end IF_DATA
    /end MEASUREMENT
    /begin MEASUREMENT Battery "Battery" UWORD volts 0 0 -5 650 ECU_ADDRESS 0x1002 /end MEASUREMENT
    /begin MEASUREMENT State "State" UBYTE state 0 0 0 2 ECU_ADDRESS 0x1004 /end MEASUREMENT
    /begin MEASUREMENT Gear "Gear" UBYTE gear 0 0 0 6 ECU_ADDRESS 0x1005 /end MEASUREMENT
    /begin MEASUREMENT Enabled "The BIT_MASK ECU_ADDRESS 0x9999 one" UBYTE NO_COMPU_METHOD 0 0 0 1
      BIT_MASK 0x30 ECU_ADDRESS 0x1006 BYTE_ORDER MSB_LAST
    /end MEASUREMENT
    /begin MEASUREMENT Torque "Torque" SWORD curve 0 0 -500 500 ECU_ADDRESS 0x1008 /end MEASUREMENT
    /begin MEASUREMENT Kelvin "Kelvin" SWORD sum 0 0 0 500 ECU_ADDRESS 0x100A /end MEASUREMENT
    /begin MEASUREMENT Wheels "Four of them" UWORD rpm 0 0 0 8000
      ECU_ADDRESS 0x1010 MATRIX_DIM 4
    /end MEASUREMENT
    /begin MEASUREMENT Channels "uint8 table[2][24], as a generator writes it" UBYTE
      NO_COMPU_METHOD 0 0 0 255 ECU_ADDRESS 0x1100 MATRIX_DIM 24 2
    /end MEASUREMENT
    /begin MEASUREMENT Cube "Three ways" UBYTE NO_COMPU_METHOD 0 0 0 255
      ECU_ADDRESS 0x1200 MATRIX_DIM 4 3 2 LAYOUT COLUMN_DIR
    /end MEASUREMENT
    /begin MEASUREMENT sensors[1].raw "Brackets in its own name" UWORD NO_COMPU_METHOD 0 0 0 9
      ECU_ADDRESS 0x1300 MATRIX_DIM 3 2
    /end MEASUREMENT
    /begin MEASUREMENT Nowhere "Computed in the tool" UWORD rpm 0 0 0 8000 /end MEASUREMENT
    /begin MEASUREMENT Period "Stored as a period" UWORD hertz 0 0 0 1000
      ECU_ADDRESS 0x1030
    /end MEASUREMENT
    /begin MEASUREMENT OneOfOne "An array of one" UWORD rpm 0 0 0 8000
      ECU_ADDRESS 0x1032 ARRAY_SIZE 1 MATRIX_DIM 1 1 1
    /end MEASUREMENT
    /begin MEASUREMENT Older "Before ECU_ADDRESS" UBYTE NO_COMPU_METHOD 1 100 0 255
      /begin IF_DATA ETK KP_BLOB 0x1040 EXTERN 0x1 /end IF_DATA
    /end MEASUREMENT
    /begin MEASUREMENT OlderCcp "Before ECU_ADDRESS, over CCP" UBYTE NO_COMPU_METHOD 1 100 0 255
      /begin IF_DATA ASAP1B_CCP KP_BLOB 0x0 0x1041 1 /end IF_DATA
    /end MEASUREMENT
    /begin MEASUREMENT Paged "On another page" UWORD rpm 0 0 0 8000
      ECU_ADDRESS 0x1020 ECU_ADDRESS_EXTENSION 2
    /end MEASUREMENT

    /begin CHARACTERISTIC SpeedLimit "Maximum speed" VALUE 0x2000 RL_UWORD 0 rpm 0 8000
    /end CHARACTERISTIC
    /begin CHARACTERISTIC Gain "A gain" VALUE 0x2004 RL_FLOAT 0 NO_COMPU_METHOD -10 10
      EXTENDED_LIMITS -100 100
    /end CHARACTERISTIC
    /begin CHARACTERISTIC Serial "Set at the factory" VALUE 0x2008 RL_UWORD 0 same 0 65535
      READ_ONLY
    /end CHARACTERISTIC
    /begin CHARACTERISTIC Mode "A field of a byte" VALUE 0x200A RL_UWORD 0 state 0 2
      BIT_MASK 0x0300
    /end CHARACTERISTIC
    /begin CHARACTERISTIC TorqueMap "A map" MAP 0x3000 RL_UWORD 0 rpm 0 8000
      /begin AXIS_DESCR STD_AXIS EngineSpeed rpm 8 0 8000 /end AXIS_DESCR
      /begin AXIS_DESCR STD_AXIS Battery volts 8 0 650 /end AXIS_DESCR
    /end CHARACTERISTIC
    /begin CHARACTERISTIC Ramp "A curve" CURVE 0x3100 RL_UWORD 0 rpm 0 8000
      /begin AXIS_DESCR STD_AXIS EngineSpeed rpm 8 0 8000 /end AXIS_DESCR
    /end CHARACTERISTIC
    /begin CHARACTERISTIC Name "Text" ASCII 0x3200 RL_UWORD 0 NO_COMPU_METHOD 0 255 NUMBER 16
    /end CHARACTERISTIC
    /begin CHARACTERISTIC Limits "A block of values" VAL_BLK 0x3500 RL_UWORD 0 rpm 0 8000
      NUMBER 3
    /end CHARACTERISTIC
    /begin CHARACTERISTIC Grid "Column by column" VAL_BLK 0x3600 RL_UWORD 0 rpm 0 8000
      MATRIX_DIM 4 3
    /end CHARACTERISTIC
    /begin CHARACTERISTIC GridByRow "Row by row" VAL_BLK 0x3640 RL_ROWS 0 rpm 0 8000
      MATRIX_DIM 4 3
    /end CHARACTERISTIC
    /begin CHARACTERISTIC Big "More than is read in one go" VAL_BLK 0x4000 RL_UWORD 0 rpm 0 8000
      MATRIX_DIM 2000
    /end CHARACTERISTIC
    /begin CHARACTERISTIC Unlimited "A generator with nothing to say" VALUE 0x3A00 RL_UWORD
      0 rpm 0 0
    /end CHARACTERISTIC
    /begin CHARACTERISTIC Huge "Too many to read" VAL_BLK 0x3700 RL_UWORD 0 rpm 0 8000
      MATRIX_DIM 5000
    /end CHARACTERISTIC
    /begin CHARACTERISTIC Counted "Values after a count" VAL_BLK 0x3800 RL_COUNTED 0 rpm 0 8000
      NUMBER 3
    /end CHARACTERISTIC
    /begin CHARACTERISTIC Limits[1] "Named like an element, and its own" VALUE 0x3900 RL_UWORD
      0 rpm 0 8000
    /end CHARACTERISTIC
    /begin CHARACTERISTIC Orphan "No layout" VALUE 0x3300 RL_MISSING 0 rpm 0 8000
    /end CHARACTERISTIC
    /begin CHARACTERISTIC Wide "A type not read" VALUE 0x3400 RL_ODD 0 rpm 0 8000
    /end CHARACTERISTIC

    /begin IF_DATA XCP
      /begin XCP_ON_CAN 0x0100
        CAN_ID_MASTER 0x80000701 CAN_ID_SLAVE 0x80000702 BAUDRATE 500000
      /end XCP_ON_CAN
    /end IF_DATA
  /end MODULE
/end PROJECT
"""


@pytest.fixture(scope="module")
def a2l():
    return A2l.parse(REAL)


# --- what is read ---------------------------------------------------------------------------
def test_a_characteristic_takes_its_type_from_its_record_layout(a2l):
    limit = a2l.parameters["SpeedLimit"]
    assert (limit.kind, limit.datatype, limit.address) == ("CHARACTERISTIC", "UWORD", 0x2000)
    assert limit.writable and (limit.lower, limit.upper) == (0, 8000)
    assert a2l.parameters["Gain"].datatype == "FLOAT32_IEEE"
    assert a2l.parameters["Gain"].conversion is None


def test_fields_are_read_by_position_and_not_by_what_they_look_like(a2l):
    """A description that names a keyword, a data type or an address is still
    a description."""
    enabled = a2l.parameters["Enabled"]
    assert enabled.address == 0x1006 and enabled.bit_mask == 0x30
    assert enabled.description == "The BIT_MASK ECU_ADDRESS 0x9999 one"
    assert a2l.parameters["EngineSpeed"].description == 'Engine speed, said "fast"'


def test_comments_hide_what_is_in_them(a2l):
    assert len(a2l.measurements()) == 17 and len(a2l.characteristics()) == 17


def test_linear_conversions_are_worked_both_ways(a2l):
    rpm = a2l.parameters["EngineSpeed"].conversion
    assert rpm.to_phys(4000) == pytest.approx(1000) and rpm.to_raw(1000) == pytest.approx(4000)
    volts = a2l.parameters["Battery"].conversion
    assert volts.to_phys(1820) == pytest.approx(13.2) and volts.unit == "V"
    assert a2l.parameters["Serial"].conversion.to_phys(7) == 7


def test_a_conversion_that_is_not_a_straight_line_is_shown_raw_and_says_so(a2l):
    for name in ("Torque", "Kelvin"):
        parameter = a2l.parameters[name]
        assert parameter.readable, "a true raw number beats a wrong converted one"
        assert not parameter.conversion.exact
        assert parameter.conversion.to_phys(123) == 123


def test_values_are_named_from_a_table_or_a_table_of_ranges(a2l):
    assert a2l.parameters["State"].choices == {0: "Off", 1: "Run", 2: "Fault, see BIT_MASK"}
    gears = a2l.parameters["Gear"].choices
    assert gears[0] == "Neutral" and {gears[n] for n in range(1, 7)} == {"Forward"}


def test_the_files_byte_order_and_a_parameters_own(a2l):
    assert a2l.big_endian is True
    assert a2l.parameters["Enabled"].big_endian is False
    assert a2l.parameters["EngineSpeed"].big_endian is None


def test_a_read_only_characteristic_is_read_and_not_written(a2l):
    serial = a2l.parameters["Serial"]
    assert serial.readable and not serial.writable


def test_the_xcp_on_can_identifiers_are_found(a2l):
    ids = a2l.xcp_on_can
    assert (ids.command_id, ids.response_id, ids.extended, ids.bitrate) == (
        0x701,
        0x702,
        True,
        500000,
    )


def test_one_over_a_straight_line_is_worked_both_ways(a2l):
    """How a period is stored for something shown as a frequency."""
    hertz = a2l.parameters["Period"].conversion
    assert hertz.exact
    assert hertz.to_phys(20) == pytest.approx(50) and hertz.to_raw(50) == pytest.approx(20)
    assert hertz.to_phys(0) == float("inf"), "a period of nothing is not an error"


def test_an_array_of_one_is_one_value(a2l):
    assert a2l.parameters["OneOfOne"].readable


def test_an_older_files_address_is_found_in_its_if_data(a2l):
    assert a2l.parameters["Older"].address == 0x1040 and a2l.parameters["Older"].readable
    assert a2l.parameters["OlderCcp"].address == 0x1041


def test_an_address_extension_is_kept_with_the_address(a2l):
    assert a2l.parameters["Paged"].extension == 2 and a2l.parameters["Paged"].readable
    assert a2l.parameters["EngineSpeed"].extension == 0


# --- a row of values -----------------------------------------------------------------------
def test_an_array_is_a_row_of_values_each_with_its_own_address(a2l):
    wheels = a2l.parameters["Wheels"]
    assert wheels.is_array and wheels.count == 4 and not wheels.writable
    elements = a2l.elements(wheels)
    assert [e.name for e in elements] == ["Wheels[0]", "Wheels[1]", "Wheels[2]", "Wheels[3]"]
    assert [e.address for e in elements] == [0x1010, 0x1012, 0x1014, 0x1016], "two bytes each"
    assert all(e.conversion is wheels.conversion and not e.is_array for e in elements)


def test_a_block_of_values_is_written_one_value_at_a_time(a2l):
    limits = a2l.parameters["Limits"]
    assert limits.is_array and limits.count == 3
    assert not limits.writable, "the row as a whole is not typed into"
    assert all(e.writable for e in a2l.elements(limits))


# --- more than one dimension -----------------------------------------------------------------
def test_the_names_count_up_in_the_order_the_values_are_stored(a2l):
    """Whatever the file calls its dimensions, the values are read in the
    order they lie, and named so that the last index is the one that moves."""
    for name in ("Channels", "Cube", "Grid", "GridByRow", "sensors[1].raw"):
        array = a2l.parameters[name]
        elements = a2l.elements(array)
        size = elements[1].address - elements[0].address
        assert len(elements) == array.count
        assert [e.address for e in elements] == [
            array.address + i * size for i in range(array.count)
        ], name
        assert [e.index for e in elements] == list(range(array.count))
        assert elements[1].name.endswith("[1]"), "the last index is the one that moves"


def test_stored_row_by_row_the_first_size_is_the_one_that_changes_fastest(a2l):
    """``MATRIX_DIM 24 2`` is two rows of twenty-four: what a generator
    working from ``uint8 table[2][24]`` writes."""
    channels = a2l.parameters["Channels"]
    assert channels.sizes == (2, 24) and channels.written == (24, 2)
    assert a2l.find("Channels[0][23]").address == 0x1100 + 23
    assert a2l.find("Channels[1][0]").address == 0x1100 + 24
    assert a2l.find("Channels[2][0]") is None and a2l.find("Channels[0][24]") is None
    assert a2l.find("Channels[5]") is None, "one index is not enough for two dimensions"
    assert a2l.parameters["GridByRow"].sizes == (3, 4)


def test_stored_column_by_column_it_is_the_last(a2l):
    """Said by the measurement itself, or by a block of values' record layout."""
    cube = a2l.parameters["Cube"]
    assert cube.sizes == (4, 3, 2) and cube.column_dir
    assert a2l.find("Cube[1][0][0]").address == 0x1200 + 6
    assert a2l.find("Cube[3][2][1]").address == 0x1200 + 23
    grid = a2l.parameters["Grid"]
    assert grid.sizes == (4, 3) and a2l.find("Grid[1][0]").address == 0x3600 + 3 * 2


def test_an_array_with_brackets_in_its_own_name_is_still_found_by_element(a2l):
    assert a2l.parameters["sensors[1].raw"].sizes == (2, 3)
    assert a2l.find("sensors[1].raw[1][2]").address == 0x1300 + 5 * 2
    assert a2l.find("sensors[1].raw[2][0]") is None


def test_an_element_is_found_by_name_and_one_past_the_end_is_not(a2l):
    assert a2l.find("Wheels[3]").address == 0x1016
    assert a2l.find("Wheels[4]") is None
    assert a2l.find("EngineSpeed[0]") is None, "not an array"
    assert a2l.find("NoSuchThing") is None


def test_a_parameter_the_file_names_like_an_element_is_itself(a2l):
    """Files made from C structures are full of names with brackets in."""
    assert a2l.find("Limits[1]").address == 0x3900
    assert a2l.find("Limits[2]").address == 0x3504


def test_the_elements_are_not_counted_as_parameters_of_the_file(a2l):
    a2l.elements(a2l.parameters["Wheels"])
    assert "Wheels[0]" not in a2l.parameters


# --- what is listed and not read ------------------------------------------------------------
@pytest.mark.parametrize(
    "name", ["TorqueMap", "Ramp", "Name", "Orphan", "Wide", "Nowhere", "Huge", "Counted"]
)
def test_what_cannot_be_read_is_listed_and_says_why(a2l, name):
    parameter = a2l.parameters[name]
    assert not parameter.readable and not parameter.writable
    assert parameter.unreadable


def test_the_readable_ones_are_all_the_rest(a2l):
    assert {p.name for p in a2l.unreadable()} == {
        "TorqueMap",
        "Ramp",
        "Name",
        "Orphan",
        "Wide",
        "Nowhere",
        "Huge",
        "Counted",
    }


# --- files ------------------------------------------------------------------------------------
def test_an_included_file_is_read_in_place(tmp_path):
    (tmp_path / "layouts.a2l").write_text(
        "/begin RECORD_LAYOUT RL_UBYTE FNC_VALUES 1 UBYTE COLUMN_DIR DIRECT /end RECORD_LAYOUT",
        encoding="utf-8",
    )
    main = tmp_path / "main.a2l"
    main.write_text(
        '/begin PROJECT P "" /begin MODULE M ""\n'
        '/include "layouts.a2l"\n'
        '/include "not there.a2l"\n'
        '/begin CHARACTERISTIC Trim "" VALUE 0x10 RL_UBYTE 0 NO_COMPU_METHOD 0 255\n'
        "/end CHARACTERISTIC /end MODULE /end PROJECT",
        encoding="utf-8",
    )
    loaded = A2l.load(str(main))
    assert loaded.parameters["Trim"].datatype == "UBYTE" and loaded.parameters["Trim"].writable


def test_a_file_that_includes_itself_is_read_once_and_not_for_ever(tmp_path):
    main = tmp_path / "loop.a2l"
    main.write_text(
        '/include "loop.a2l"\n/begin MEASUREMENT A "" UBYTE NO_COMPU_METHOD 0 0 0 1\n'
        "ECU_ADDRESS 1 /end MEASUREMENT",
        encoding="utf-8",
    )
    assert "A" in A2l.load(str(main)).parameters


def test_a_file_in_latin_1_keeps_its_units(tmp_path):
    path = tmp_path / "latin.a2l"
    text = (
        '/begin COMPU_METHOD temp "" LINEAR "%4.1" "°C" COEFFS_LINEAR 1 0 /end COMPU_METHOD\n'
        '/begin MEASUREMENT T "" SWORD temp 0 0 -40 150 ECU_ADDRESS 0x10 /end MEASUREMENT'
    )
    path.write_bytes(text.encode("latin-1"))
    assert A2l.load(str(path)).parameters["T"].unit == "°C"


def test_a_file_cut_short_gives_what_it_has():
    text = REAL[: REAL.index("/begin CHARACTERISTIC TorqueMap")]
    cut = A2l.parse(text)
    assert "SpeedLimit" in cut.parameters and "TorqueMap" not in cut.parameters


def test_something_that_is_not_an_a2l_at_all_is_an_empty_one():
    assert A2l.parse("this is a shopping list, /end of").parameters == {}


# --- through the manager, to memory -----------------------------------------------------------
class Memory:
    """An engine that is a block of memory: what was read and written, and where."""

    info = ConnectInfo(resources=0, big_endian=False, max_cto=8, max_dto=8)

    def __init__(self) -> None:
        self.bytes = bytearray(0x6000)
        self.writes: list[tuple[int, bytes]] = []
        #: The address extension of each read, None for a read made without one.
        self.spaces: list[int | None] = []

    def read(self, address: int, size: int, **space) -> bytes:
        self.spaces.append(space.get("extension"))
        return bytes(self.bytes[address : address + size])

    def write(self, address: int, data: bytes, **_space) -> None:
        self.writes.append((address, bytes(data)))
        self.bytes[address : address + len(data)] = data

    def close(self) -> None:
        pass


@pytest.fixture
def calibrating(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    ctx = Context(log=print)
    manager = XcpManager(BusManager(), Hooks(ctx), SignalHub(), ctx)
    manager.engine = memory = Memory()
    path = tmp_path / "real.a2l"
    path.write_text(REAL, encoding="utf-8")
    manager.load_a2l(str(path))
    said: list[str] = []
    values: dict[str, float] = {}
    manager.result.connect(said.append)
    manager.value.connect(values.__setitem__)

    def settle(count: int) -> None:
        deadline = time.monotonic() + 5.0
        while len(said) < count:
            assert time.monotonic() < deadline, said
            QCoreApplication.processEvents()
            time.sleep(0.002)

    yield manager, memory, said, values, settle
    manager.shutdown()


def test_the_files_byte_order_wins_over_the_slaves(calibrating):
    manager, memory, _said, values, settle = calibrating
    memory.bytes[0x1000:0x1002] = bytes([0x0F, 0xA0])  # 4000, most significant first
    manager.read("EngineSpeed")
    settle(1)
    assert values["EngineSpeed"] == pytest.approx(1000)


def test_an_address_extension_goes_out_with_the_address_and_only_then(calibrating):
    """Only when there is one, so that an engine written before extensions
    still works for every slave that has a single address space."""
    manager, memory, _said, _values, settle = calibrating
    manager.read("EngineSpeed")
    manager.read("Paged")
    settle(2)
    assert memory.spaces == [None, 2]


def test_reading_an_array_reads_every_value_of_it(calibrating):
    manager, memory, said, values, settle = calibrating
    for index in range(4):  # 4000, 8000, 12000, 16000 raw, most significant first
        raw = 4000 * (index + 1)
        memory.bytes[0x1010 + 2 * index : 0x1012 + 2 * index] = raw.to_bytes(2, "big")
    manager.read("Wheels")
    settle(1)
    assert [values[f"Wheels[{i}]"] for i in range(4)] == pytest.approx([1000, 2000, 3000, 4000])
    assert len(said) == 1, "one line for the row, not one for each value"


def test_a_two_dimensional_array_is_read_in_the_order_it_is_stored(calibrating):
    manager, memory, said, values, settle = calibrating
    memory.bytes[0x1100 : 0x1100 + 48] = bytes(range(48))
    manager.read("Channels")
    settle(1)
    assert values["Channels[0][0]"] == 0 and values["Channels[0][23]"] == 23
    assert values["Channels[1][0]"] == 24 and values["Channels[1][23]"] == 47
    assert len(values) == 48 and len(said) == 1


def test_one_value_of_a_block_is_written_where_it_lives(calibrating):
    manager, memory, _said, _values, settle = calibrating
    manager.write("Limits[2]", "1000")
    manager.write("Limits", "1000")  # the row as a whole: refused
    settle(2)
    assert memory.writes == [(0x3504, (4000).to_bytes(2, "big"))]


def test_a_masked_value_is_read_as_its_own_bits(calibrating):
    manager, memory, _said, values, settle = calibrating
    memory.bytes[0x1006] = 0b1110_1111  # the field, 0x30, holds 0b10
    manager.read("Enabled")
    settle(1)
    assert values["Enabled"] == 2


def test_writing_a_masked_value_leaves_the_other_bits_alone(calibrating):
    manager, memory, _said, _values, settle = calibrating
    memory.bytes[0x200A:0x200C] = bytes([0xFC, 0xFF])  # field 0x0300 is 0, the rest all set
    manager.write("Mode", "Run")
    settle(1)
    assert memory.writes == [(0x200A, bytes([0xFD, 0xFF]))]


def test_a_named_value_is_written_by_name_number_or_both(calibrating):
    manager, memory, _said, _values, settle = calibrating
    for count, typed in enumerate(("Run", "1", "Run (1)"), start=1):
        memory.bytes[0x200A:0x200C] = bytes(2)
        manager.write("Mode", typed)
        settle(count)
        assert memory.bytes[0x200A:0x200C] == bytes([0x01, 0x00]), typed


def test_what_is_not_read_is_refused_without_touching_the_slave(calibrating):
    manager, memory, _said, values, settle = calibrating
    manager.read("TorqueMap")
    manager.write("TorqueMap", "1")
    manager.write("Serial", "1")
    settle(3)
    assert memory.writes == [] and values == {}


# --- the limits the file gives ----------------------------------------------------------------
def test_a_value_outside_the_limits_is_not_written_unless_it_is_meant(calibrating):
    manager, memory, _said, _values, settle = calibrating
    assert manager.beyond_limits("SpeedLimit", "9000") == (9000, 0, 8000)
    assert manager.beyond_limits("SpeedLimit", "8000") is None, "the limit itself is inside"
    assert manager.beyond_limits("SpeedLimit", "-1") == (-1, 0, 8000)

    manager.write("SpeedLimit", "9000")
    settle(1)
    assert memory.writes == [], "refused, and the slave never asked"

    manager.write("SpeedLimit", "9000", beyond_limits=True)
    settle(2)
    assert memory.writes == [(0x2000, (36000).to_bytes(2, "big"))]


def test_limits_that_say_nothing_stop_nothing(calibrating):
    """0 and 0 is a generator with no limits to give, not a characteristic
    that may only ever be nought."""
    manager, memory, _said, _values, settle = calibrating
    assert manager.beyond_limits("Unlimited", "123") is None
    manager.write("Unlimited", "123")
    settle(1)
    assert len(memory.writes) == 1


def test_something_that_is_not_a_number_is_for_the_write_to_refuse(calibrating):
    manager, _memory, _said, _values, _settle = calibrating
    assert manager.beyond_limits("SpeedLimit", "fast") is None


# --- an array too big to read in one go -------------------------------------------------------
def test_a_big_array_is_opened_and_read_a_value_at_a_time(calibrating, a2l):
    manager, memory, _said, values, settle = calibrating
    big = a2l.parameters["Big"]
    assert big.is_array and big.count == 2000
    assert a2l.find("Big[1999]").address == 0x4000 + 1999 * 2

    manager.read("Big")  # all two thousand: refused
    settle(1)
    assert values == {}

    memory.bytes[0x4000 + 1999 * 2 : 0x4000 + 2000 * 2] = (4000).to_bytes(2, "big")
    manager.read("Big[1999]")
    settle(2)
    assert values == {"Big[1999]": pytest.approx(1000)}


# --- a big file says how far it has got ---------------------------------------------------------
def many(count: int) -> str:
    """An A2L with this many measurements: enough for more than one report."""
    blocks = "".join(
        f'/begin MEASUREMENT m{n} "" UWORD NO_COMPU_METHOD 0 0 0 65535 '
        f"ECU_ADDRESS 0x{0x1000 + 2 * n:X} /end MEASUREMENT\n"
        for n in range(count)
    )
    return f'/begin PROJECT p "" /begin MODULE m ""\n{blocks}/end MODULE /end PROJECT\n'


def test_reading_says_how_far_it_has_got(tmp_path):
    from pycangui.xcp import a2l as reader

    path = tmp_path / "many.a2l"
    path.write_text(many(3 * reader.BLOCKS_A_REPORT), encoding="utf-8")
    said: list[float] = []
    read = A2l.load(str(path), said.append)
    assert len(read.parameters) == 3 * reader.BLOCKS_A_REPORT
    assert len(said) > 3
    assert said == sorted(said), "it only goes forward"
    assert said[0] == 0.0 and said[-1] < 1.0


def test_reading_is_the_same_with_nobody_listening(tmp_path):
    text = many(50)
    assert (
        A2l.parse(text).parameters.keys()
        == A2l.parse(text, progress=lambda _f: None).parameters.keys()
    )


@pytest.fixture
def pane(app, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings

    from pycangui.ui.main_window import MainWindow

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    window = MainWindow()
    yield window.panes.view("xcp"), window.xcp, tmp_path
    window.close()


def test_the_pane_loads_a_file_and_leaves_no_box_behind(app, pane):
    from PySide6.QtWidgets import QProgressDialog

    view, manager, folder = pane
    path = folder / "many.a2l"
    path.write_text(many(1200), encoding="utf-8")
    assert view.load_a2l(str(path)) is True
    assert len(manager.a2l.parameters) == 1200
    assert view.tree.topLevelItemCount() == 1200
    app.processEvents()
    assert not [box for box in view.findChildren(QProgressDialog) if box.isVisible()]
    assert view._listing_progress is None


def test_cancelling_keeps_the_a2l_there_was(app, pane, monkeypatch):
    from PySide6.QtWidgets import QProgressDialog

    view, manager, folder = pane
    first = folder / "first.a2l"
    first.write_text(many(3), encoding="utf-8")
    assert view.load_a2l(str(first))
    before = manager.a2l

    monkeypatch.setattr(QProgressDialog, "wasCanceled", lambda _box: True)
    second = folder / "second.a2l"
    second.write_text(many(7), encoding="utf-8")
    assert view.load_a2l(str(second)) is False
    assert manager.a2l is before
    assert view.tree.topLevelItemCount() == 3


def test_a_file_that_cannot_be_read_is_not_loaded(app, pane):
    view, manager, folder = pane
    before = manager.a2l
    assert view.load_a2l(str(folder / "not_there.a2l")) is False
    assert manager.a2l is before
