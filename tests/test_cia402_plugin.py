# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Driving a CiA 402 motor controller, and the state machine that is the reason
this is a plugin at all.

Most of a drive screen could be a custom pane: the modes, the targets and the
actual values are ordinary objects at standard indices.  What cannot is the
walk to Operation enabled -- a sequence of writes to one object where the next
one depends on what the drive answered to the last -- and the state itself,
which is decoded from overlapping masks of the statusword rather than read out
of a field.  That is what most of this file is about.

Nothing here needs a bus: the state machine is handed a statusword and answers
with what to write.
"""

import struct

import pytest
from PySide6.QtCore import QSettings

from pycangui.core import plugin_package
from pycangui.core.plugins import builtin_dir
from pycangui.plugins.cia402 import drive as cia402
from pycangui.ui.main_window import MainWindow

#: Statuswords as a drive really sends them: the state bits, plus voltage
#: enabled and remote, which are set on anything that is powered and listening.
LIVE = (1 << 4) | (1 << 9)
NOT_READY = 0x0000
SWITCH_ON_DISABLED = 0x0040 | LIVE
READY = 0x0021 | LIVE
SWITCHED_ON = 0x0023 | LIVE
ENABLED = 0x0027 | LIVE
QUICK_STOP_ACTIVE = 0x0007 | LIVE
FAULT_REACTION = 0x000F | LIVE
FAULT = 0x0008 | LIVE


def words(steps):
    return [step.controlword for step in steps]


# --- the state, out of one word --------------------------------------------------------
def test_each_state_is_recognised_through_the_bits_around_it():
    """The masks overlap, and the bits a drive sets for other reasons are not
    part of the state: a real statusword has voltage and remote set too."""
    assert cia402.state_of(NOT_READY) == "Not ready to switch on"
    assert cia402.state_of(SWITCH_ON_DISABLED) == "Switch on disabled"
    assert cia402.state_of(READY) == "Ready to switch on"
    assert cia402.state_of(SWITCHED_ON) == "Switched on"
    assert cia402.state_of(ENABLED) == "Operation enabled"
    assert cia402.state_of(QUICK_STOP_ACTIVE) == "Quick stop active"
    assert cia402.state_of(FAULT_REACTION) == "Fault reaction active"
    assert cia402.state_of(FAULT) == "Fault"


def test_ready_to_switch_on_and_switched_on_differ_in_one_bit():
    """Which is exactly why this cannot be a field on a form."""
    assert cia402.state_of(0x0021) != cia402.state_of(0x0023)


def test_a_statusword_the_profile_does_not_describe_is_not_guessed_at():
    """0x0041 says switch on disabled and ready to switch on at once, which is
    a contradiction.  Naming it anyway would put a confident word on screen
    with nothing behind it."""
    assert cia402.state_of(0x0041) == cia402.UNKNOWN


def test_the_flags_beside_the_state_are_the_ones_that_are_set():
    assert cia402.flags_of(ENABLED) == ["Voltage enabled", "Remote"]
    assert "Warning" in cia402.flags_of(ENABLED | (1 << 7))
    assert "Target reached" in cia402.flags_of(ENABLED | (1 << 10))


def test_the_quick_stop_bit_is_not_shown_as_a_flag():
    """It reads the other way up -- set means not active -- and the state table
    already says so plainly."""
    assert "Quick stop" not in " ".join(cia402.flags_of(ENABLED | (1 << 5)))


# --- and the walk to Operation enabled ------------------------------------------------------
def test_enabling_from_switch_on_disabled_is_the_three_writes():
    assert words(cia402.steps_to_enable(SWITCH_ON_DISABLED)) == [0x06, 0x07, 0x0F]


def test_it_starts_from_wherever_the_drive_already_is():
    """The whole reason it is code: which writes are needed is an answer to
    what the drive said, not a fixed list."""
    assert words(cia402.steps_to_enable(READY)) == [0x07, 0x0F]
    assert words(cia402.steps_to_enable(SWITCHED_ON)) == [0x0F]


def test_a_drive_already_enabled_needs_nothing_written_to_it():
    assert cia402.steps_to_enable(ENABLED) == []


def test_a_fault_is_cleared_on_the_way_through():
    assert words(cia402.steps_to_enable(FAULT)) == [0x00, 0x80, 0x06, 0x07, 0x0F]


def test_clearing_a_fault_is_two_writes_because_bit_seven_is_an_edge():
    """A single write of 0x80 works on a drive whose controlword happened to
    have the bit clear, and does nothing at all on one where it did not."""
    assert words(cia402.clear_fault()) == [0x0000, 0x0080]


def test_a_quick_stop_is_released_the_long_way_round():
    """The direct route back to Operation enabled exists, but only for drives
    whose quick stop option code says so.  The long way works everywhere."""
    assert words(cia402.steps_to_enable(QUICK_STOP_ACTIVE)) == [0x00, 0x06, 0x07, 0x0F]


def test_every_step_says_what_it_is_doing():
    """A sequence that fails half way should say which half."""
    said = [step.what for step in cia402.steps_to_enable(FAULT)]
    assert said[-1] == "Enabling operation"
    assert any("fault" in what.lower() for what in said)


# --- what it will not command ------------------------------------------------------------------
def test_a_drive_that_is_still_starting_up_is_not_commanded():
    """It leaves that state by itself, and a controlword written now is ignored
    rather than refused -- which looks exactly like the tool doing nothing."""
    with pytest.raises(ValueError, match="starting up"):
        cia402.steps_to_enable(NOT_READY)


def test_a_drive_still_reacting_to_a_fault_is_left_to_finish():
    with pytest.raises(ValueError, match="reacting to a fault"):
        cia402.steps_to_enable(FAULT_REACTION)


def test_a_statusword_that_means_nothing_here_is_not_acted_on():
    with pytest.raises(ValueError, match="none of the states"):
        cia402.steps_to_enable(0x0041)


def test_quick_stop_is_only_offered_to_a_drive_that_is_running():
    """A drive that is not enabled is not turning under its own power, and a
    button that appears to work and does nothing is worse than one that says
    why it will not."""
    assert words(cia402.steps_to_quick_stop(ENABLED)) == [0x02]
    with pytest.raises(ValueError, match="a drive that is running"):
        cia402.steps_to_quick_stop(READY)


def test_disabling_is_one_write_from_anywhere_it_matters():
    assert words(cia402.steps_to_disable(ENABLED)) == [0x00]
    assert cia402.steps_to_disable(SWITCH_ON_DISABLED) == [], "already there"


# --- the mode, and what a target means in it ------------------------------------------------
def test_the_modes_are_named():
    assert cia402.mode_name(3) == "Profile velocity"
    assert cia402.mode_name(-5) == "Manufacturer specific (-5)"
    assert cia402.mode_name(50) == "Reserved (50)"


def test_each_mode_uses_the_target_it_uses():
    assert cia402.TARGET_FOR[1] is cia402.TARGET_POSITION
    assert cia402.TARGET_FOR[3] is cia402.TARGET_VELOCITY
    assert cia402.TARGET_FOR[4] is cia402.TARGET_TORQUE


def test_profile_position_has_to_be_told_to_take_its_target():
    """The mode where writing the target is not enough: the drive takes it on
    the rising edge of bit 4 and ignores it until then."""
    assert words(cia402.steps_to_apply_target(1, ENABLED)) == [0x0F, 0x1F, 0x0F]


def test_no_other_mode_is_offered_that_button():
    with pytest.raises(ValueError, match="as it is written"):
        cia402.steps_to_apply_target(3, ENABLED)


def test_applying_a_target_will_not_enable_a_drive_on_the_way_past():
    """The edge is made by writing the enable controlword with bit 4 added, so
    from Switched on this would enable the drive -- which is the one thing here
    that gets asked about first.  A button that quietly does what another button
    asks permission for is a hole in the permission."""
    with pytest.raises(ValueError, match="without asking"):
        cia402.steps_to_apply_target(1, SWITCHED_ON)


def test_a_cyclic_mode_will_not_take_a_target_over_sdo():
    """It expects a new one every cycle, over a PDO, from something keeping
    time.  One written here would be stale before it arrived."""
    allowed, why = cia402.can_set_target(9)
    assert not allowed
    assert "every cycle" in why


def test_a_mode_with_no_target_says_so_rather_than_offering_a_box():
    allowed, why = cia402.can_set_target(6)  # homing
    assert not allowed
    assert "no target" in why


# --- the values themselves ------------------------------------------------------------------
def test_values_are_packed_as_the_profile_types_them():
    assert cia402.encode(cia402.CONTROLWORD, 0x0F) == b"\x0f\x00"
    assert cia402.encode(cia402.TARGET_POSITION, -1) == b"\xff\xff\xff\xff"
    assert cia402.encode(cia402.MODE, 3) == b"\x03"


def test_a_value_too_big_for_its_object_is_refused_rather_than_wrapped():
    """struct would happily wrap it, and the drive would do exactly what the
    wrapped number says -- which is the sort of mistake that moves machinery."""
    with pytest.raises(ValueError, match="will not fit"):
        cia402.encode(cia402.TARGET_TORQUE, 70000)


def test_what_comes_back_is_read_as_the_type_it_is():
    assert cia402.decode(cia402.STATUSWORD, b"\x37\x06") == 0x0637
    assert cia402.decode(cia402.VELOCITY_ACTUAL, struct.pack("<i", -1234)) == -1234
    # Signed, so one of a maker's own modes comes back negative rather than 253.
    assert cia402.decode(cia402.MODE_DISPLAY, b"\xfd") == -3


def test_a_drive_that_answers_with_fewer_bytes_than_asked_is_not_an_error():
    """A drive answering a 32-bit object with two bytes is being terse, not
    wrong."""
    assert cia402.decode(cia402.POSITION_ACTUAL, b"\x10\x00") == 16


# --- and as a plugin -----------------------------------------------------------------------------
def install(window) -> None:
    plugin_package.install_folder(builtin_dir() / "cia402", window.ctx.workspace_dir / "plugins")
    window._reload_plugins()


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    win = MainWindow()
    win.show()
    app.processEvents()
    install(win)
    app.processEvents()
    yield win
    win.close()


@pytest.fixture
def view(window):
    return window.panes.view("cia402:main")


def test_it_installs_and_brings_a_pane(app, window, view):
    assert "CANopen motor control (CiA 402)" in [r.label for r in window.plugins.working()]
    assert window.plugins.errors() == {}
    assert window.panes.docks["cia402:main"].windowTitle() == "CANopen motor control"


def test_it_says_nothing_has_been_read_rather_than_inventing_a_state(app, view):
    """A statusword of zero is a real state -- Not ready to switch on -- and
    showing it before anything has been read would be news that never arrived."""
    assert "Not read" in view.state.text()


def test_the_state_on_screen_is_the_one_the_word_means(app, view):
    view._took(cia402.STATUSWORD, ENABLED)
    assert view.state.text() == "Operation enabled"
    assert "Voltage enabled" in view.flags.text()
    assert view.raw.text().endswith(f"0x{ENABLED:04X}")


def test_the_buttons_follow_the_state(app, view):
    view._took(cia402.STATUSWORD, ENABLED)
    assert not view.enable.isEnabled(), "already enabled"
    assert view.quick_stop.isEnabled()
    assert not view.reset.isEnabled()

    view._took(cia402.STATUSWORD, FAULT)
    assert view.reset.isEnabled()
    assert not view.quick_stop.isEnabled(), "it is not running"


def test_the_target_box_follows_the_mode(app, view):
    view._took(cia402.MODE_DISPLAY, 3)
    assert view.target.isEnabled()
    assert not view.apply_target.isEnabled(), "profile velocity acts on the target as written"

    view._took(cia402.MODE_DISPLAY, 1)
    assert not view.apply_target.isEnabled(), "not until the drive is enabled"
    view._took(cia402.STATUSWORD, ENABLED)
    assert view.apply_target.isEnabled()

    view._took(cia402.MODE_DISPLAY, 9)
    assert not view.target.isEnabled()
    assert "every cycle" in view.target_note.text()


class FakeDrive:
    """Somewhere to write, and a record of what was written."""

    def __init__(self, statusword):
        self.statusword = statusword
        self.written: list[tuple] = []

    def read(self, obj):
        return self.statusword if obj.where == cia402.STATUSWORD.where else 0

    def write(self, obj, value):
        self.written.append((obj.index, value))


def wired(view, monkeypatch, statusword):
    """The pane against a drive, with the worker thread taken out of it."""
    drive = FakeDrive(statusword)
    monkeypatch.setattr(view, "_drive", lambda: drive)
    monkeypatch.setattr(view, "_quietly", lambda: drive)
    monkeypatch.setattr(view.app, "run_in_background", lambda job, done: done(job(), None))
    return drive


def test_a_command_is_planned_from_the_drive_rather_than_from_the_last_poll(app, view, monkeypatch):
    """Which writes are needed *is* the state, so planning from a value read
    thirty seconds ago -- or never read at all -- is planning from a guess."""
    drive = wired(view, monkeypatch, FAULT)
    view._command(lambda: cia402.steps_to_enable(view.statusword))

    assert [value for _index, value in drive.written] == [0x00, 0x80, 0x06, 0x07, 0x0F]
    assert all(index == cia402.CONTROLWORD.index for index, _value in drive.written)


def test_a_refused_command_says_why_instead_of_writing_anything(app, window, view, monkeypatch):
    drive = wired(view, monkeypatch, FAULT_REACTION)
    view._command(lambda: cia402.steps_to_enable(view.statusword))

    assert "reacting to a fault" in window.log.toPlainText()
    assert drive.written == []


def test_a_drive_already_where_it_was_asked_to_be_is_left_alone(app, window, view, monkeypatch):
    drive = wired(view, monkeypatch, ENABLED)
    view._command(lambda: cia402.steps_to_enable(view.statusword))

    assert drive.written == []
    assert "nothing to write" in window.log.toPlainText()


def test_enabling_asks_first(app, window, view, monkeypatch):
    """The moment a motor becomes able to move, which is the same class of
    thing as joining a live bus or transmitting onto one."""
    asked = []
    monkeypatch.setattr(
        window.confirm, "ask", lambda _p, key, title, text: asked.append((key, text)) or False
    )
    view.node.addItem("Node 5", 5)
    view.node.setCurrentIndex(view.node.findData(5))
    view._enable()

    assert asked and asked[0][0] == "cia402.enable.5", "and once per drive, not once ever"
    assert "may move the moment it is enabled" in asked[0][1]


def test_with_no_drive_selected_it_says_so_rather_than_failing(app, window, view):
    view._enable()
    view.poll.setChecked(True)
    assert "No drive selected" in window.log.toPlainText()
    assert not view.poll.isChecked(), "and it does not sit there polling nothing"


def test_a_pane_nobody_can_see_stops_polling(app, window, view):
    """Every read is a round trip on somebody's equipment, and a pane put away
    is a pane with no reader."""
    view.node.addItem("Node 5", 5)
    window.panes.show("cia402:main")
    app.processEvents()
    view.poll.setChecked(True)
    assert view.poller.running

    window.panes.docks["cia402:main"].hide()
    app.processEvents()
    assert not view.poller.running


def test_unloading_it_leaves_nothing_listening(app, window):
    """The relay it registered to hear about its pane is disconnected with
    everything else it added, rather than being left pointing at code that is
    no longer there."""
    plugin_app = window.plugins.loaded["cia402"].app
    assert plugin_app._watchers, "it asked to be told about its pane"

    window.plugin_actions.set_active("cia402", False)
    app.processEvents()
    assert "cia402:main" not in window.panes.docks
    assert plugin_app._watchers == []


# --- and putting it down again -------------------------------------------------------------------
def test_a_drive_that_is_not_running_needs_nothing_taken_back():
    assert cia402.stop_writes(3, READY) == []


def test_stopping_halts_first_and_then_zeroes_a_rate_demand():
    """Halt because it means stop in every mode; the zero afterwards so that
    switching it back on later does not start from the old demand."""
    writes = cia402.stop_writes(3, ENABLED)
    assert writes == [
        (cia402.CONTROLWORD, cia402.ENABLE_OPERATION | cia402.HALT),
        (cia402.TARGET_VELOCITY, 0),
    ]


def test_a_position_target_is_never_zeroed():
    """Zero is a *place*.  Writing it to 0x607A does not stop a drive part way
    through a move -- it sends it to position zero, which may be the longest
    move it has been asked for all day."""
    writes = cia402.stop_writes(1, ENABLED)
    assert writes == [(cia402.CONTROLWORD, cia402.ENABLE_OPERATION | cia402.HALT)]


def test_removing_power_as_well_is_asked_for_rather_than_assumed():
    """On a vertical axis it is the load that decides, and whether a brake
    catches it is not something pycangui can know."""
    assert cia402.stop_writes(3, ENABLED, disable=True)[-1] == (
        cia402.CONTROLWORD,
        cia402.DISABLE_VOLTAGE,
    )


def running(view, monkeypatch, mode=3):
    """A pane that believes it has a drive turning."""
    drive = wired(view, monkeypatch, ENABLED)
    view.node.addItem("Node 5", 5)
    view._took(cia402.MODE_DISPLAY, mode)
    view._took(cia402.STATUSWORD, ENABLED)
    drive.written.clear()
    return drive


def test_putting_the_pane_away_stops_the_drive(app, window, view, monkeypatch):
    """A drive holds the last controlword and target it was given.  A window
    that commanded motion and then went away has left a motor turning with
    nobody watching the screen that says so."""
    drive = running(view, monkeypatch)
    view.set_visible_to_user(False)

    assert drive.written == [
        (cia402.CONTROLWORD.index, cia402.ENABLE_OPERATION | cia402.HALT),
        (cia402.TARGET_VELOCITY.index, 0),
    ]
    assert "Halted node 5" in window.log.toPlainText()


def test_closing_the_pane_for_good_stops_it_too(app, window, view, monkeypatch):
    drive = running(view, monkeypatch)
    window.panes.unregister("cia402:main")
    app.processEvents()
    assert drive.written, "the pane went, and took the demand with it"


def test_closing_the_window_stops_it(app, window, view, monkeypatch):
    """The one the pane facade cannot report: a pane is not removed when the
    tool closes, it goes with the window."""
    drive = running(view, monkeypatch)
    window.close()
    app.processEvents()
    assert drive.written


def test_a_stop_that_could_not_be_sent_is_said_loudly(app, window, view, monkeypatch):
    """Silence here would read as success, and what it would mean is a motor
    still turning."""
    running(view, monkeypatch)
    monkeypatch.setattr(view, "_quietly", lambda: None)
    view.stop_demand(background=False)
    assert "could NOT be halted" in window.log.toPlainText()


def test_the_pane_says_loudly_when_a_motor_is_live(app, view, monkeypatch):
    running(view, monkeypatch)
    view.poller.start(2.0)
    view._show_banner()
    assert view.banner.isVisibleTo(view)
    assert "DEMAND ACTIVE" in view.banner.text()

    view.poller.stop()
    view._show_banner()
    assert "not known here" in view.banner.text(), "and stops asserting it once nothing is read"


def test_no_banner_when_nothing_is_being_driven(app, view):
    view._took(cia402.STATUSWORD, READY)
    assert not view.banner.isVisibleTo(view)


def test_losing_the_bus_while_it_is_running_is_reported(app, window, view, monkeypatch):
    """There is no write to make -- the bus is what has gone -- so the only
    honest response is to say so."""
    running(view, monkeypatch)
    window.bus.disconnected.emit()
    app.processEvents()
    assert "nothing here can stop it now" in window.log.toPlainText()
