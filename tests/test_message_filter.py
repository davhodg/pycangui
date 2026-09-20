# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Acceptance filtering: the one thing pycangui does that loses frames.

Everything else that narrows a view hides rows and keeps the data, so the
tests for those check what is shown. These check what is *gone*, because
that is the difference: a filtered frame is not in the trace, not in the
recording, and not in the count, and nothing can bring it back.
"""

import time

import can
import pytest

from pycangui.core.bus import BusManager
from pycangui.core.channels import Channels
from pycangui.core.context import Context
from pycangui.core.filters import (
    EXTENDED_MASK,
    STANDARD_MASK,
    Rule,
    accepts,
    as_can_filters,
    describe,
    from_saved,
    to_saved,
)
from pycangui.ui import message_filter
from pycangui.ui.bus_status import FILTERED_COLOUR, BusStatus
from pycangui.ui.message_filter import MessageFilterDialog, parse_try


def pump(app, bus, times=4):
    for _ in range(times):
        bus._drain()
        app.processEvents()
        time.sleep(0.01)


# --- the rules themselves --------------------------------------------------------------
def test_a_rule_with_a_full_mask_is_one_id_exactly():
    rule = Rule(0x581, STANDARD_MASK, False)
    assert rule.matches(0x581, False)
    assert not rule.matches(0x580, False)
    assert not rule.matches(0x582, False)


def test_a_mask_widens_a_rule_to_a_block():
    """0x180/0x780 is TPDO1 from any node, which is why anyone uses a mask."""
    rule = Rule(0x180, 0x780, False)
    assert rule.matches(0x180, False), "node 0"
    assert rule.matches(0x1FF, False), "node 127"
    assert not rule.matches(0x200, False), "that is RPDO1"


def test_standard_and_extended_never_match_each_other():
    """A bus carrying both needs a rule for each, and the dialog says so."""
    assert not Rule(0x100, STANDARD_MASK, False).matches(0x100, True)
    assert not Rule(0x100, EXTENDED_MASK, True).matches(0x100, False)


def test_no_rules_accepts_everything():
    """The normal state, and the one that must never be mistaken for a block."""
    assert accepts([], 0x123, False)
    assert accepts([], 0x18FF50E5, True)


def test_any_rule_matching_is_enough():
    rules = [Rule(0x581), Rule(0x701)]
    assert accepts(rules, 0x701, False)
    assert not accepts(rules, 0x601, False)


def test_no_rules_means_none_rather_than_an_empty_list():
    """python-can takes None for accept everything; [] would block the lot."""
    assert as_can_filters([]) is None
    assert as_can_filters([Rule(0x100)]) == [
        {"can_id": 0x100, "can_mask": STANDARD_MASK, "extended": False}
    ]


def test_the_description_is_readable_in_a_status_bar():
    assert describe([]) == ""
    assert describe([Rule(0x581)]) == "only 0x581"
    assert describe([Rule(0x581), Rule(0x701)]) == "only 2 ids"


# --- keeping them ----------------------------------------------------------------------
def test_rules_survive_being_saved_and_read_back():
    rules = [Rule(0x180, 0x780, False), Rule(0x18FF50E5, EXTENDED_MASK, True)]
    assert from_saved(to_saved(rules)) == rules


def test_nonsense_in_the_settings_file_is_dropped_not_raised():
    """It is hand editable, and the safe direction is letting more through."""
    saved = [
        {"id": "not a number", "mask": "0x7FF", "extended": False},
        {"id": "0x9999", "mask": "0x7FF", "extended": False},  # too big for 11 bits
        "a string where a rule should be",
        {"id": "0x123", "mask": "0x7FF", "extended": False},
    ]
    assert from_saved(saved) == [Rule(0x123, STANDARD_MASK, False)]


def test_an_older_workspace_has_no_filter_at_all():
    assert from_saved(None) == []
    assert from_saved([]) == []


# --- what it does to a real bus ---------------------------------------------------------
@pytest.fixture
def bus(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    manager = BusManager()
    yield manager
    manager.disconnect_bus()


def test_a_filtered_frame_never_arrives(app, bus):
    """The whole claim, and the thing no other filter in pycangui does."""
    bus.set_filters([Rule(0x581)])
    bus.connect_bus("virtual", "vcan_filter", 500000, False)
    seen = []
    bus.frames.connect(seen.extend)

    sender = can.Bus(interface="virtual", channel="vcan_filter")
    sender.send(can.Message(arbitration_id=0x581, data=b"\x01", is_extended_id=False))
    sender.send(can.Message(arbitration_id=0x123, data=b"\x02", is_extended_id=False))
    pump(app, bus)
    sender.shutdown()

    assert [f.can_id for f in seen] == [0x581], "the filtered frame got through"


def test_taking_the_filter_off_brings_the_traffic_back(app, bus):
    bus.set_filters([Rule(0x581)])
    bus.connect_bus("virtual", "vcan_filter_off", 500000, False)
    seen = []
    bus.frames.connect(seen.extend)
    bus.set_filters([])

    sender = can.Bus(interface="virtual", channel="vcan_filter_off")
    sender.send(can.Message(arbitration_id=0x123, data=b"\x02", is_extended_id=False))
    pump(app, bus)
    sender.shutdown()

    assert [f.can_id for f in seen] == [0x123]


def test_a_filter_set_while_disconnected_is_on_by_the_time_there_is_a_bus(app, bus):
    """Otherwise it would be off at exactly the moment somebody set it."""
    bus.set_filters([Rule(0x581)])
    assert bus.filters == [Rule(0x581)]
    bus.connect_bus("virtual", "vcan_filter_later", 500000, False)
    seen = []
    bus.frames.connect(seen.extend)

    sender = can.Bus(interface="virtual", channel="vcan_filter_later")
    sender.send(can.Message(arbitration_id=0x123, data=b"\x02", is_extended_id=False))
    pump(app, bus)
    sender.shutdown()

    assert seen == []


def test_applying_a_filter_is_a_warning_in_the_log(app, bus):
    """The record of when the traffic started disappearing."""
    said = []
    bus.warning.connect(said.append)
    bus.connect_bus("virtual", "vcan_filter_says", 500000, False)
    bus.set_filters([Rule(0x581)])
    assert any("message filter applied" in text for text in said), said
    assert any("0x581" in text for text in said), said


def test_reconnecting_says_it_again(app, bus):
    """A filtered channel that has just been reconnected is still filtered,
    and the log should not have to be read from an hour ago to know."""
    bus.set_filters([Rule(0x581)])
    said = []
    bus.warning.connect(said.append)
    bus.connect_bus("virtual", "vcan_filter_again", 500000, False)
    assert any("message filter applied" in text for text in said), said


def test_an_unfiltered_channel_says_nothing_about_filters(app, bus):
    """Every connect announcing the absence of a filter is noise."""
    said = []
    bus.note.connect(said.append)
    bus.warning.connect(said.append)
    bus.connect_bus("virtual", "vcan_no_filter", 500000, False)
    assert not any("filter" in text for text in said), said


# --- saying so, loudly -------------------------------------------------------------------
def test_the_status_bar_says_filtered_and_blinks(app, tmp_path, monkeypatch):
    """The condition for having this at all: it cannot be missed."""
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    ctx = Context(log=print)
    channels = Channels()
    status = BusStatus(channels, ctx=ctx)
    name = channels.names()[0]

    status.refresh()
    assert "FILTERED" not in status.indicators[name].text()
    assert not status._blink.isActive(), "blinking with nothing to blink about"

    channels.get(name).set_filters([Rule(0x581)])
    status.refresh()
    assert "FILTERED" in status.indicators[name].text()
    assert status._blink.isActive()
    assert "0x581" in status.indicators[name].toolTip()

    channels.get(name).set_filters([])
    status.refresh()
    assert "FILTERED" not in status.indicators[name].text()
    assert not status._blink.isActive()
    assert status.indicators[name].blink_on is False, "left on the blink colour"


def test_the_blink_alternates_the_dot(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    channels = Channels()
    status = BusStatus(channels, ctx=Context(log=print))
    name = channels.names()[0]
    channels.get(name).set_filters([Rule(0x581)])
    status.refresh()

    was = status.indicators[name].blink_on
    status._blink.timeout.emit()
    assert status.indicators[name].blink_on is not was
    assert FILTERED_COLOUR  # the blink colour is its own, not a health colour


# --- kept in the workspace ---------------------------------------------------------------
def test_a_filter_is_remembered_and_put_back_on_the_channel(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    ctx = Context(log=print)
    channels = Channels()
    name = channels.names()[0]
    message_filter.remember(ctx, name, [Rule(0x180, 0x780, False)])

    again = Channels()
    message_filter.restore(ctx, again)
    assert again.get(name).filters == [Rule(0x180, 0x780, False)]


def test_a_channel_with_nothing_saved_is_unfiltered(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    channels = Channels()
    message_filter.restore(Context(log=print), channels)
    assert channels.get(channels.names()[0]).filters == []


# --- the dialog ---------------------------------------------------------------------------
def test_what_somebody_types_into_the_try_box():
    assert parse_try("581") == (0x581, False)
    assert parse_try("0x581") == (0x581, False)
    assert parse_try("18FF50E5 x") == (0x18FF50E5, True)
    assert parse_try("18FF50E5x") == (0x18FF50E5, True)
    assert parse_try("  701  ") == (0x701, False)


def test_a_half_typed_id_is_not_an_error():
    assert parse_try("") is None
    assert parse_try("zz") is None
    assert parse_try("9999") is None, "too big for 11 bits, and no x to say otherwise"


def test_the_dialog_gives_back_the_rules_it_was_given(app):
    rules = [Rule(0x581), Rule(0x180, 0x780, False)]
    dialog = MessageFilterDialog("CAN 1", rules)
    assert dialog.rules() == rules


def test_the_dialog_answers_whether_an_id_would_get_through(app):
    dialog = MessageFilterDialog("CAN 1", [Rule(0x581)])
    dialog.trial.setText("581")
    assert "gets through" in dialog.verdict.text()
    dialog.trial.setText("601")
    assert "dropped" in dialog.verdict.text()


def test_an_empty_table_is_described_as_the_normal_state(app):
    """ "No rules" must never read as "nothing gets through"."""
    dialog = MessageFilterDialog("CAN 1", [])
    assert "every frame" in dialog.summary.text()
    dialog.trial.setText("601")
    assert "gets through" in dialog.verdict.text()


def test_a_row_being_typed_into_is_skipped_rather_than_guessed_at(app):
    dialog = MessageFilterDialog("CAN 1", [Rule(0x581)])
    dialog._add_clicked()
    dialog.table.item(1, 0).setText("")
    assert dialog.rules() == [Rule(0x581)]


def test_renaming_a_channel_keeps_its_filter(app, tmp_path, monkeypatch):
    """The same bus with the same rules on the driver. Reading the new name's
    saved rules instead would quietly take the filter off."""
    from pycangui.ui.main_window import MainWindow

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    window = MainWindow()
    name = window.channels.names()[0]
    window.channels.get(name).set_filters([Rule(0x581)])

    window.channels.rename(name, "Drive bus")
    assert window.channels.get("Drive bus").filters == [Rule(0x581)]
    assert message_filter.saved_rules(window.ctx, "Drive bus") == [Rule(0x581)], "not written down"
    window.close()


def test_a_new_channel_gets_whatever_was_saved_under_its_name(app, tmp_path, monkeypatch):
    from pycangui.ui.main_window import MainWindow

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    window = MainWindow()
    message_filter.remember(window.ctx, "CAN 2", [Rule(0x701)])

    window.channels.add("CAN 2")
    assert window.channels.get("CAN 2").filters == [Rule(0x701)]
    window.close()


def test_a_recovered_controller_is_still_filtered(app, bus):
    """A restart is the driver's own, and what it does to acceptance
    filtering is the driver's business."""
    bus.set_filters([Rule(0x581)])
    bus.connect_bus("virtual", "vcan_filter_recover", 500000, False)
    assert bus.recover()
    seen = []
    bus.frames.connect(seen.extend)

    sender = can.Bus(interface="virtual", channel="vcan_filter_recover")
    sender.send(can.Message(arbitration_id=0x123, data=b"\x02", is_extended_id=False))
    pump(app, bus)
    sender.shutdown()

    assert seen == [], "the filter came off with the restart"
