# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Editing a CAN database: changes held, checked, undone, and saved as a new file."""

from pathlib import Path

import cantools
import pytest

from pycangui.core import dbc_roundtrip
from pycangui.core.dbc_edit import (
    ERROR,
    WARNING,
    AddAttributeDefinition,
    AddMessage,
    AddNode,
    AddSignal,
    AddSignalGroup,
    AddValueTable,
    AttributeSpec,
    DatabaseEdit,
    EditError,
    RemoveAttributeDefinition,
    RemoveMessage,
    RemoveNode,
    RemoveSignal,
    RemoveSignalGroup,
    RemoveValueTable,
    SetAttribute,
    SetAttributeDefinition,
    SetMessage,
    SetNode,
    SetSignal,
    SetSignalGroup,
    SetValueTable,
    parse_values,
    values_text,
)

FILES = Path(__file__).parent / "dbc"


def read(path):
    return cantools.database.load_file(str(path), strict=False, sort_signals=None)


@pytest.fixture
def edit():
    return DatabaseEdit(FILES / "tables_and_comments.dbc")


# --- saving --------------------------------------------------------------------------------------
def test_what_was_changed_is_in_the_file_saved(edit, tmp_path):
    edit.apply(SetSignal("EngineData", "EngineSpeed", "scale", 0.125))
    edit.apply(SetSignal("EngineData", "Throttle", "unit", "pct"))
    edit.apply(SetMessage("GearboxData", "cycle_time", 20))
    target = tmp_path / "saved.dbc"
    assert edit.save_as(target) == []
    saved = read(target)
    engine = saved.get_message_by_name("EngineData")
    assert engine.get_signal_by_name("EngineSpeed").scale == 0.125
    assert engine.get_signal_by_name("Throttle").unit == "pct"
    assert saved.get_message_by_name("GearboxData").cycle_time == 20
    assert b"\r\r\n" not in target.read_bytes(), "each line ended once"


def test_what_was_not_changed_comes_back_as_it_was(tmp_path):
    """Attributes, relation attributes, comments and named values of
    everything else, in the file's own order: one change, one difference."""
    source = FILES / "attributes.dbc"
    edit = DatabaseEdit(source)
    edit.apply(SetSignal("PumpStatus", "Flow", "unit", "l/s"))
    target = tmp_path / "saved.dbc"
    edit.save_as(target)
    original, saved = source.read_text(), target.read_text()
    found = dbc_roundtrip.compare(read(source), read(target), original, saved)
    assert [(f.where, f.what) for f in found] == [("PumpStatus.Flow", "a signal's unit differs")]


def test_it_is_never_saved_over_the_file_it_came_from(edit):
    edit.apply(SetMessage("GearboxData", "cycle_time", 20))
    with pytest.raises(EditError):
        edit.save_as(edit.path)


def test_an_error_stops_a_save_and_a_warning_does_not(edit, tmp_path):
    edit.apply(SetSignal("GearboxData", "Lockup", "start", 7))  # onto Gear's bits
    assert {p.level for p in edit.problems()} == {WARNING}
    assert edit.save_as(tmp_path / "warned.dbc") == []
    edit.apply(SetSignal("GearboxData", "Lockup", "start", 40))  # past the end
    assert ERROR in {p.level for p in edit.problems()}
    with pytest.raises(EditError):
        edit.save_as(tmp_path / "refused.dbc")
    assert not (tmp_path / "refused.dbc").exists()


# --- undo, and changes that cannot be made ---------------------------------------------------
def test_undo_takes_back_the_last_change_only(edit):
    edit.apply(SetMessage("GearboxData", "cycle_time", 20))
    edit.apply(SetMessage("GearboxData", "name", "Gearbox"))
    edit.undo()
    assert [m.name for m in edit.database.messages if m.frame_id == 769] == ["GearboxData"]
    assert edit.message("GearboxData").cycle_time == 20
    edit.undo()
    assert edit.message("GearboxData").cycle_time is None
    assert edit.undo() is None and not edit.changed


def test_a_change_that_cannot_be_made_changes_nothing(edit):
    for change in (
        SetMessage("NoSuchMessage", "cycle_time", 10),
        SetMessage("GearboxData", "name", "EngineData"),  # taken
        SetSignal("GearboxData", "Gear", "name", "Lockup"),  # taken
        SetSignal("GearboxData", "Gear", "colour", "red"),  # not a field
        AddMessage("EngineData", 0x500),
    ):
        with pytest.raises(EditError):
            edit.apply(change)
    assert not edit.changed
    assert edit.message("GearboxData").get_signal_by_name("Gear").name == "Gear"


def test_discard_goes_back_to_the_file(edit):
    edit.apply(RemoveMessage("Shared"))
    edit.discard()
    assert not edit.changed and edit.message("Shared")


def test_messages_and_signals_are_added_and_removed(edit, tmp_path):
    edit.apply(AddMessage("Spare", 0x400, length=2))
    edit.apply(AddSignal("Spare", "Flag", 0, 1))
    edit.apply(RemoveSignal("GearboxData", "Lockup"))
    edit.apply(RemoveMessage("Doubled"))
    target = tmp_path / "saved.dbc"
    edit.save_as(target)
    saved = read(target)
    assert saved.get_message_by_name("Spare").get_signal_by_name("Flag").length == 1
    assert "Lockup" not in [s.name for s in saved.get_message_by_name("GearboxData").signals]
    assert "Doubled" not in [m.name for m in saved.messages]


# --- renames, and what follows them --------------------------------------------------------------
def test_renames_are_traced_from_the_first_name_to_the_last(edit):
    """What refers to a message by name -- a transmit row, a plotted signal --
    needs its old name and its new one, however many renames between."""
    edit.apply(SetMessage("GearboxData", "name", "Gearbox"))
    edit.apply(SetMessage("Gearbox", "name", "Transmission"))
    assert edit.renamed_messages() == {"GearboxData": "Transmission"}
    edit.apply(SetMessage("Transmission", "name", "GearboxData"))
    assert edit.renamed_messages() == {}


def test_a_renamed_multiplexer_still_chooses_its_signals(tmp_path):
    edit = DatabaseEdit(FILES / "multiplexed.dbc")
    edit.apply(SetSignal("Simple", "Page", "name", "Selector"))
    target = tmp_path / "saved.dbc"
    edit.save_as(target)
    simple = read(target).get_message_by_name("Simple")
    assert simple.get_signal_by_name("Voltage").multiplexer_signal == "Selector"
    assert simple.decode(bytes([1, 0x10, 0, 0, 0, 0, 0, 0]))["Speed"] == 16


def test_a_multiplexer_with_signals_under_it_is_not_removed():
    edit = DatabaseEdit(FILES / "multiplexed.dbc")
    with pytest.raises(EditError):
        edit.apply(RemoveSignal("Simple", "Page"))


def test_a_renamed_signal_keeps_its_place_in_its_group(tmp_path):
    edit = DatabaseEdit(FILES / "extras.dbc")
    edit.apply(SetSignal("Grouped", "A", "name", "Alpha"))
    target = tmp_path / "saved.dbc"
    edit.save_as(target)
    groups = {
        g.name: g.signal_names for g in read(target).get_message_by_name("Grouped").signal_groups
    }
    assert groups["Pair"] == ["Alpha", "B"]


# --- what is wrong ------------------------------------------------------------------------------
def test_what_is_wrong_is_found(edit):
    edit.apply(SetMessage("Shared", "frame_id", 768))  # EngineData's
    edit.apply(SetSignal("GearboxData", "Gear", "name", "2fast"))
    edit.apply(SetSignal("Doubled", "Big", "is_float", True))
    edit.apply(SetSignal("Doubled", "Big", "length", 48))
    found = {(p.where, p.level) for p in edit.problems()}
    assert ("Shared", ERROR) in found, "two messages on one identifier"
    assert ("GearboxData.2fast", ERROR) in found, "a name the format does not allow"
    assert ("Doubled.Big", ERROR) in found, "a float that is neither 32 nor 64 bits"


def test_signals_that_take_turns_are_not_said_to_share_bits():
    """A multiplexed message's signals overlap by design."""
    assert DatabaseEdit(FILES / "multiplexed.dbc").problems() == []


def test_a_large_frame_is_allowed_only_its_proper_lengths():
    edit = DatabaseEdit(FILES / "extras.dbc")
    assert edit.problems() == []
    edit.apply(SetMessage("FdFrame", "length", 60))
    assert {p.where for p in edit.problems() if p.level == ERROR} >= {"FdFrame"}


# --- multiplexing --------------------------------------------------------------------------------
@pytest.fixture
def muxed():
    return DatabaseEdit(FILES / "multiplexed.dbc")


def test_a_signal_put_under_a_multiplexer_is_sent_only_for_its_values(muxed, tmp_path):
    muxed.apply(SetSignal("Simple", "Counter", "multiplexed_by", ("Page", (0, 2))))
    target = tmp_path / "saved.dbc"
    assert muxed.save_as(target) == []
    simple = read(target).get_message_by_name("Simple")
    assert simple.get_signal_by_name("Counter").multiplexer_ids == [0, 2]
    assert "Counter" in simple.decode(bytes([2, 0, 0, 0, 0, 0, 0, 9]))
    assert "Counter" not in simple.decode(bytes([1, 0, 0, 0, 0, 0, 0, 9]))


def test_a_new_multiplexer_can_choose_under_another(muxed, tmp_path):
    muxed.apply(SetSignal("Simple", "Voltage", "is_multiplexer", True))
    muxed.apply(SetSignal("Simple", "Current", "multiplexed_by", ("Voltage", (1,))))
    target = tmp_path / "saved.dbc"
    muxed.save_as(target)
    simple = read(target).get_message_by_name("Simple")
    voltage, current = simple.get_signal_by_name("Voltage"), simple.get_signal_by_name("Current")
    assert voltage.is_multiplexer and voltage.multiplexer_signal == "Page"
    assert current.multiplexer_signal == "Voltage" and current.multiplexer_ids == [1]


def test_a_signal_taken_out_of_multiplexing_is_in_every_frame(muxed, tmp_path):
    muxed.apply(SetSignal("Simple", "Speed", "multiplexed_by", (None, ())))
    speed = muxed.message("Simple").get_signal_by_name("Speed")
    assert speed.multiplexer_signal is None and not speed.multiplexer_ids


@pytest.mark.parametrize(
    "change",
    [
        SetSignal("Simple", "Page", "multiplexed_by", ("Page", (0,))),  # under itself
        SetSignal("Simple", "Counter", "multiplexed_by", ("Voltage", (0,))),  # not a multiplexer
        SetSignal("Simple", "Counter", "multiplexed_by", ("Page", ())),  # for no value
        SetSignal("Simple", "Counter", "multiplexed_by", ("Gone", (0,))),
        SetSignal("Simple", "Page", "is_multiplexer", False),  # still choosing
        SetSignal("Extended", "Group", "multiplexed_by", ("Channel", (0,))),  # under its own
    ],
)
def test_multiplexing_that_cannot_be_is_refused(muxed, change):
    with pytest.raises(EditError):
        muxed.apply(change)
    assert muxed.changes == []


def test_a_value_its_multiplexer_cannot_hold_is_a_warning(muxed):
    muxed.apply(SetSignal("Simple", "Speed", "multiplexed_by", ("Page", (1, 300))))
    assert [p.level for p in muxed.problems()] == [WARNING]


def test_multiplexer_values_are_written_as_they_are_typed():
    assert values_text([4, 0, 2, 3]) == "0, 2-4"
    assert parse_values("0, 2-4") == [0, 2, 3, 4]
    assert parse_values(values_text([1, 5, 6, 7, 9])) == [1, 5, 6, 7, 9]
    for bad in ("x", "4-2", "-1"):
        with pytest.raises(ValueError):
            parse_values(bad)


# --- value tables --------------------------------------------------------------------------------
def test_value_tables_are_added_renamed_changed_and_removed(edit, tmp_path):
    edit.apply(AddValueTable("Modes", {0: "Idle", 1: "Run"}))
    edit.apply(SetValueTable("OnOff", "values", {1: "Yes", 0: "No"}))
    edit.apply(SetValueTable("GearTable", "name", "Gears"))
    edit.apply(RemoveValueTable("Modes"))
    edit.apply(AddValueTable("Faults", {3: "Open"}))
    target = tmp_path / "saved.dbc"
    assert edit.save_as(target) == []
    tables = {k: dict(v) for k, v in read(target).dbc.value_tables.items()}
    assert tables == {
        "OnOff": {1: "Yes", 0: "No"},
        "Gears": {7: "Reverse", 6: "Neutral", 1: "First", 2: "Second", 3: "Third"},
        "Faults": {3: "Open"},
    }
    assert list(edit.value_tables())[:2] == ["OnOff", "Gears"], "a rename keeps its place"
    assert edit.touched() == set(), "no message was changed"


def test_a_value_table_change_is_undone_and_refused_like_the_rest(edit):
    with pytest.raises(EditError):
        edit.apply(AddValueTable("OnOff", {}))
    with pytest.raises(EditError):
        edit.apply(SetValueTable("OnOff", "name", "GearTable"))
    with pytest.raises(EditError):
        edit.apply(RemoveValueTable("Gone"))
    edit.apply(RemoveValueTable("OnOff"))
    assert "OnOff" not in edit.value_tables()
    edit.undo()
    assert edit.value_tables()["OnOff"] == {1: "On", 0: "Off"}


def test_a_value_table_name_the_format_does_not_allow_stops_a_save(edit):
    edit.apply(AddValueTable("two words", {0: "Zero"}))
    assert [p.level for p in edit.problems()] == [ERROR]


# --- nodes ---------------------------------------------------------------------------------------
@pytest.fixture
def attributed():
    return DatabaseEdit(FILES / "attributes.dbc")


def test_a_renamed_node_is_renamed_wherever_it_is_named(attributed, tmp_path):
    attributed.apply(SetNode("Valve", "name", "ValveUnit"))
    target = tmp_path / "saved.dbc"
    assert attributed.save_as(target) == []
    saved = read(target)
    assert [n.name for n in saved.nodes] == ["Tester", "Pump", "ValveUnit"]
    assert (
        "ValveUnit"
        in saved.get_message_by_name("PumpStatus").get_signal_by_name("Pressure").receivers
    )
    text = target.read_text()
    assert "BU_SG_REL_ ValveUnit SG_ 256 Pressure" in text, "in its relation attributes too"
    assert "BU_BO_REL_ ValveUnit 512" in text


def test_nodes_are_added_with_a_comment_and_removed_when_unused(attributed, tmp_path):
    attributed.apply(AddNode("Logger", 'keeps "everything"'))
    with pytest.raises(EditError):
        attributed.apply(RemoveNode("Pump"))  # it sends PumpStatus
    with pytest.raises(EditError):
        attributed.apply(AddNode("Pump"))
    target = tmp_path / "saved.dbc"
    attributed.save_as(target)
    comments = {n.name: n.comment for n in read(target).nodes}
    assert comments["Logger"] == 'keeps "everything"'
    attributed.apply(RemoveNode("Logger"))
    assert "Logger" not in [n.name for n in attributed.nodes()]


def test_a_sender_or_receiver_that_is_not_a_node_is_a_warning(attributed):
    attributed.apply(SetMessage("PumpStatus", "senders", ["Somebody"]))
    assert [p.level for p in attributed.problems()] == [WARNING]


# --- signal groups -------------------------------------------------------------------------------
def test_signal_groups_are_added_changed_and_removed(attributed, tmp_path):
    attributed.apply(AddSignalGroup("PumpStatus", "Readings", 1, ("Pressure", "Flow")))
    attributed.apply(SetSignalGroup("PumpStatus", "Readings", "repetitions", 2))
    attributed.apply(SetSignalGroup("PumpStatus", "Readings", "signals", ("Flow",)))
    attributed.apply(SetSignalGroup("PumpStatus", "Readings", "name", "Measured"))
    attributed.apply(AddSignalGroup("PumpStatus", "Spare"))
    attributed.apply(RemoveSignalGroup("PumpStatus", "Spare"))
    target = tmp_path / "saved.dbc"
    attributed.save_as(target)
    (group,) = read(target).get_message_by_name("PumpStatus").signal_groups
    assert (group.name, group.repetitions, group.signal_names) == ("Measured", 2, ["Flow"])


def test_a_signal_group_naming_what_is_not_there_is_refused(attributed):
    for change in (
        AddSignalGroup("PumpStatus", "Readings", 1, ("Gone",)),
        SetSignalGroup("PumpStatus", "Nothing", "repetitions", 2),
        SetSignalGroup("PumpStatus", "Nothing", "name", "x"),
    ):
        with pytest.raises(EditError):
            attributed.apply(change)
    assert attributed.changes == []


# --- attributes ----------------------------------------------------------------------------------
def test_attributes_are_set_and_taken_back_to_their_default(attributed, tmp_path):
    attributed.apply(SetAttribute(("node", "Valve"), "NodeLayer", "Gateway"))
    attributed.apply(SetAttribute(("message", "ValveCommand"), "GenMsgSendType", "Cyclic"))
    attributed.apply(SetAttribute(("signal", "PumpStatus", "Flow"), "SigUnitText", None))
    attributed.apply(SetAttribute((), "ProtocolVersion", 9))
    target = tmp_path / "saved.dbc"
    assert attributed.save_as(target) == [], "the send type read back is the one set"
    saved = read(target)
    valve = next(n for n in saved.nodes if n.name == "Valve")
    assert valve.dbc.attributes["NodeLayer"].value == 1
    assert saved.get_message_by_name("ValveCommand").send_type == "Cyclic"
    flow = saved.get_message_by_name("PumpStatus").get_signal_by_name("Flow")
    assert "SigUnitText" not in flow.dbc.attributes
    assert saved.dbc.attributes["ProtocolVersion"].value == 9


def test_the_attributes_the_format_keeps_are_set_as_what_they_are(attributed, tmp_path):
    """A cycle time and an initial value, as those; the frame format and the
    bit rate not at all."""
    attributed.apply(SetAttribute(("message", "ValveCommand"), "GenMsgCycleTime", 40))
    attributed.apply(SetAttribute(("signal", "PumpStatus", "Flow"), "GenSigStartValue", 12))
    target = tmp_path / "saved.dbc"
    attributed.save_as(target)
    saved = read(target)
    assert saved.get_message_by_name("ValveCommand").cycle_time == 40
    assert saved.get_message_by_name("PumpStatus").get_signal_by_name("Flow").raw_initial == 12
    rows = {r.name for r in attributed.attributes(("message", "ValveCommand"))}
    assert "GenMsgCycleTime" not in rows, "the message dialog's cycle time"


@pytest.mark.parametrize(
    "change",
    [
        SetAttribute(("message", "PumpStatus"), "NodeLayer", "Gateway"),  # a node's
        SetAttribute(("node", "Valve"), "NodeLayer", "Sideways"),  # not a choice
        SetAttribute(("message", "PumpStatus"), "GenMsgStartDelayTime", "soon"),
        SetAttribute(("message", "PumpStatus"), "Nothing", 1),
        SetAttribute(("node", "Nobody"), "NodeLayer", "Gateway"),
    ],
)
def test_an_attribute_it_cannot_have_is_refused(attributed, change):
    with pytest.raises(EditError):
        attributed.apply(change)
    assert attributed.changes == []


def test_a_value_outside_its_range_is_a_warning(attributed):
    attributed.apply(SetAttribute((), "ProtocolVersion", 300))
    assert [p.level for p in attributed.problems()] == [WARNING]


def test_the_rows_a_dialog_shows_say_values_by_name(attributed):
    rows = {r.name: r for r in attributed.attributes(("node", "Tester"))}
    assert rows["NodeLayer"].value == "Diagnostic" and rows["ILUsed"].value is None
    assert rows["ILUsed"].default == "No" and rows["ILUsed"].choices == ("No", "Yes")
    blank = attributed.attributes(("signal",))
    assert blank and all(r.value is None for r in blank), "for a signal not made yet"


# --- attribute definitions -----------------------------------------------------------------------
def test_definitions_are_added_renamed_changed_and_removed(attributed, tmp_path):
    attributed.apply(
        AddAttributeDefinition(AttributeSpec("Owner", "message", "STRING", default="nobody"))
    )
    attributed.apply(SetAttribute(("message", "PumpStatus"), "Owner", "rig team"))
    attributed.apply(
        SetAttributeDefinition(
            "Owner", AttributeSpec("Responsible", "message", "STRING", default="none")
        )
    )
    attributed.apply(RemoveAttributeDefinition("SigUnitText"))
    target = tmp_path / "saved.dbc"
    attributed.save_as(target)
    saved = read(target)
    definitions = saved.dbc.attribute_definitions
    assert "Owner" not in definitions and definitions["Responsible"].default_value == "none"
    assert "SigUnitText" not in definitions
    status = saved.get_message_by_name("PumpStatus")
    assert status.dbc.attributes["Responsible"].value == "rig team", "the value went with it"
    flow = status.get_signal_by_name("Flow")
    assert "SigUnitText" not in flow.dbc.attributes, "and the removed one's values with it"


def test_a_definition_in_use_keeps_its_kind_and_type(attributed):
    used = AttributeSpec("NodeLayer", "message", "ENUM", choices=("Application", "Gateway"))
    with pytest.raises(EditError):
        attributed.apply(SetAttributeDefinition("NodeLayer", used))
    assert attributed.uses("NodeLayer") == 2


def test_the_definitions_the_format_keeps_are_not_removed(attributed):
    with pytest.raises(EditError):
        attributed.apply(RemoveAttributeDefinition("GenMsgCycleTime"))
    with pytest.raises(EditError):
        attributed.apply(AddAttributeDefinition(AttributeSpec("Choice", "node", "ENUM")))
