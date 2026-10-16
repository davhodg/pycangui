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
    AddMessage,
    AddSignal,
    DatabaseEdit,
    EditError,
    RemoveMessage,
    RemoveSignal,
    SetMessage,
    SetSignal,
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
