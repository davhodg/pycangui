# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Driving a CiA 402 motor controller, and the state machine that is the reason
this is a plugin at all.

Most of a drive screen could be a custom pane: the modes, the targets and the
actual values are ordinary objects at standard indices. What cannot is the
walk to Operation enabled -- a sequence of writes to one object where the next
one depends on what the drive answered to the last -- and the state itself,
which is decoded from overlapping masks of the statusword rather than read out
of a field. That is what most of this file is about.

Nothing here needs a bus: the state machine is handed a statusword and answers
with what to write.
"""

import struct

import pytest
from PySide6.QtCore import QSettings

from pycangui.core import plugin_package
from pycangui.core.plugins import builtin_dir
from pycangui.plugins.cia402 import drive as cia402
from pycangui.plugins.cia402.plugin import MAKER_NONE_SET, maker_bits
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
    a contradiction. Naming it anyway would put a confident word on screen
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
    whose quick stop option code says so. The long way works everywhere."""
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
    that gets asked about first. A button that quietly does what another button
    asks permission for is a hole in the permission."""
    with pytest.raises(ValueError, match="without asking"):
        cia402.steps_to_apply_target(1, SWITCHED_ON)


def test_a_cyclic_mode_will_not_take_a_target_over_sdo():
    """It expects a new one every cycle, over a PDO, from something keeping
    time. One written here would be stale before it arrived."""
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

    #: 0x6502 with every profiled mode set, so the mode list is not
    #: restricted to No mode in tests that are about something else.
    ALL_MODES = 0x03FF

    def __init__(self, statusword, mode: int = 0):
        self.statusword = statusword
        #: What it answers for 0x6061, which need not be what was asked
        #: for: a drive is free to refuse a mode.
        self.mode = mode
        self.supported = self.ALL_MODES
        self.written: list[tuple] = []

    def read(self, obj):
        if obj.where == cia402.STATUSWORD.where:
            return self.statusword
        if obj.where == cia402.MODE_DISPLAY.where:
            return self.mode
        return self.supported if obj.where == cia402.SUPPORTED_MODES.where else 0

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
    """Zero is a *place*. Writing it to 0x607A does not stop a drive part way
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
    """A drive holds the last controlword and target it was given. A window
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
    before = window.log.toPlainText()
    view.stop_demand(background=False)
    assert window.log.toPlainText() != before


def test_the_pane_says_loudly_when_a_motor_is_live(app, view, monkeypatch):
    running(view, monkeypatch)
    view.poller.start(2.0)
    view._show_banner()
    assert view.banner.isVisibleTo(view)
    view.poller.stop()


def test_no_banner_when_nothing_is_being_driven(app, view):
    view._took(cia402.STATUSWORD, READY)
    assert not view.banner.isVisibleTo(view)


def test_losing_the_bus_while_it_is_running_is_reported(app, window, view, monkeypatch):
    """There is no write to make -- the bus is what has gone -- so the only
    honest response is to say so."""
    running(view, monkeypatch)
    before = window.log.toPlainText()
    window.bus.disconnected.emit()
    app.processEvents()
    assert window.log.toPlainText() != before


# --- what a heartbeat must not do --------------------------------------------------------
def test_a_heartbeat_does_not_wipe_the_values(app, window, view):
    """node_seen arrives with every heartbeat -- once a second on a quiet
    bus -- and rebuilding the list cleared the values that had just been
    read: they appeared and blanked, appeared and blanked."""
    view._took(cia402.STATUSWORD, ENABLED)
    view._took(cia402.POSITION_ACTUAL, 12345)
    shown = view.actuals[cia402.POSITION_ACTUAL.where].text()
    assert "12,345" in shown

    window.canopen.node_seen.emit(5, "operational")  # as a heartbeat does
    window.canopen.node_seen.emit(5, "operational")

    assert view.actuals[cia402.POSITION_ACTUAL.where].text() == shown
    assert view.state.text() == "Operation enabled", "and the state is still known"


def test_a_heartbeat_after_the_pane_has_gone_does_not_reach_for_it(app, window, view, monkeypatch):
    """The manager outlives the pane, so the connection has to go when the
    pane does. Connected through a lambda it did not: the next heartbeat
    called into a pane whose widgets Qt had already destroyed, and the tool
    reported a bug in itself once a second.

    Through the excepthook, because PySide sends an exception raised inside a
    slot there rather than back to whoever emitted the signal.
    """
    import sys

    from PySide6.QtCore import QEvent

    assert view is not None
    window._reload_plugins()  # what updating a plugin does: unload, load again
    app.processEvents()
    app.sendPostedEvents(None, QEvent.DeferredDelete)  # the old pane, destroyed

    blew_up: list = []
    monkeypatch.setattr(sys, "excepthook", lambda *what: blew_up.append(what))
    # A node the old pane had never listed, so anything still connected to
    # the manager gets as far as its widgets rather than stopping at "the
    # list has not changed".
    monkeypatch.setattr(window.canopen, "nodes", lambda: [5])
    window.canopen.node_seen.emit(5, "operational")
    app.processEvents()
    assert not blew_up, blew_up


# --- limits ------------------------------------------------------------------------------
def test_the_limits_are_there_to_be_read_and_written(app, view):
    """Not targets: a drive in any mode is held to these."""
    assert set(view.limits) == {obj.where for obj in cia402.LIMITS}
    assert cia402.MAX_TORQUE.where in view.limits
    assert cia402.MAX_PROFILE_VELOCITY.where in view.limits
    assert cia402.MAX_MOTOR_SPEED.where in view.limits


def test_the_speed_limits_are_two_different_objects_in_two_units(app):
    """0x607F caps the profile in the same units as the velocity objects;
    0x6080 is about the motor and is in rpm by the standard."""
    assert cia402.MAX_PROFILE_VELOCITY.unit == "counts/s"
    assert cia402.MAX_MOTOR_SPEED.unit == "rpm"


# --- only what this drive has ------------------------------------------------------------
def test_modes_are_restricted_to_the_ones_the_drive_reports(app, view):
    view._show_modes(cia402.modes_in(0b000111))  # profile position, velocity, profile velocity

    offered = {view.mode_box.itemData(i) for i in range(view.mode_box.count())}
    assert offered == {0, 1, 2, 3}, "and No mode, which 0x6502 does not list"

    view._show_modes(None)
    assert len(offered) < view.mode_box.count(), "unknown means offer them all"


def test_the_supported_modes_bits_are_read_the_way_the_standard_numbers_them(app):
    """Bit 0 is mode 1, so the numbering is off by one -- worth a table
    rather than working it out in the head each time."""
    assert cia402.modes_in(1 << 0) == {1}
    assert cia402.modes_in(1 << 5) == {6}, "homing is bit 5"
    assert cia402.modes_in(0) == set()


def test_an_object_the_eds_does_not_have_is_switched_off(app, view, monkeypatch):
    """An EDS is what says which objects exist; a control for one that does
    not would fail with an abort code nobody reads."""
    monkeypatch.setattr(view, "_object_dictionary", lambda: {0x6040: object(), 0x6041: object()})

    view._fit_to_drive()

    assert not view.limits[cia402.MAX_TORQUE.where].isEnabled()
    assert "EDS" in view.limits[cia402.MAX_TORQUE.where].toolTip()


def test_without_an_eds_everything_is_offered(app, view, monkeypatch):
    """Most drives have these, and greying a control because pycangui was
    never given a file would be worse than an SDO coming back refused."""
    monkeypatch.setattr(view, "_object_dictionary", lambda: None)

    view._fit_to_drive()

    assert view.limits[cia402.MAX_TORQUE.where].isEnabled()


# --- which object is behind each control ---------------------------------------------------
def test_every_value_says_which_object_it_is(app, view):
    """The first thing anybody comparing this screen with a drive manual
    needs, and the spin boxes are not obviously 0x6072 rather than 0x6080."""
    for obj in cia402.LIMITS:
        assert f"0x{obj.index:04X}" in view.limits[obj.where].toolTip()
    for obj in (cia402.POSITION_ACTUAL, cia402.VELOCITY_ACTUAL, cia402.TORQUE_ACTUAL):
        assert f"0x{obj.index:04X}" in view.actuals[obj.where].toolTip()
    for obj in cia402.WATCHED:
        assert f"0x{obj.index:04X}" in view.poll.toolTip(), "Poll says what it is reading"


def test_the_object_is_still_named_once_an_eds_has_been_looked_at(app, view, monkeypatch):
    """Fitting the pane to a drive replaced the tooltip rather than adding
    to it, so having an EDS was the one case where the pane stopped saying
    what it was writing to."""
    monkeypatch.setattr(
        view, "_object_dictionary", lambda: {obj.index: object() for obj in cia402.LIMITS}
    )

    view._fit_to_drive()

    tip = view.limits[cia402.MAX_TORQUE.where].toolTip()
    assert f"0x{cia402.MAX_TORQUE.index:04X}" in tip
    assert view.limits[cia402.MAX_TORQUE.where].isEnabled()


def test_an_eds_loaded_after_the_pane_is_open_still_decides_what_is_offered(
    app, window, view, monkeypatch
):
    """Otherwise nothing changed until the drive was picked again, and the
    controls stayed live for objects the file says are not there."""
    monkeypatch.setattr(view, "_object_dictionary", lambda: {0x6040: object(), 0x6041: object()})
    monkeypatch.setattr(view.node, "currentData", lambda: 5)
    assert view.limits[cia402.MAX_TORQUE.where].isEnabled(), "nothing has said otherwise yet"

    window.canopen.eds_loaded.emit(5, "drive.eds", "A drive")

    assert not view.limits[cia402.MAX_TORQUE.where].isEnabled()


def test_an_object_the_eds_does_not_have_is_not_polled_for(app, view, monkeypatch):
    """Asking anyway is an abort code many times a second, and the line on
    screen already says the file has no such object."""
    monkeypatch.setattr(view, "_object_dictionary", lambda: {0x6041: object()})
    asked: list = []
    monkeypatch.setattr(view.app, "run_in_background", lambda job, done: asked.append(job))
    monkeypatch.setattr(view, "_quietly", lambda: object())

    view._read_one(*cia402.POSITION_ACTUAL.where)
    assert not asked

    view._read_one(*cia402.STATUSWORD.where)
    assert asked, "0x6041 is in this EDS, so it is read"


# --- the bits the profile leaves to the maker ----------------------------------------------
def test_which_bits_belong_to_the_maker_in_each_word():
    """Statusword bits 12 and 13 are defined per mode by the standard, so
    calling them manufacturer bits would mislead somebody reading a manual."""
    assert cia402.MANUFACTURER_STATUS_BITS == (8, 14, 15)
    assert cia402.MANUFACTURER_CONTROL_BITS == (11, 12, 13, 14, 15)
    assert cia402.bits_set(1 << 15 | 1 << 8) == [8, 15]
    assert cia402.bits_set(ENABLED) == []
    assert cia402.mask_of([11, 15]) == 0x8800
    assert cia402.mask_of([]) == 0


def test_a_ticked_bit_goes_out_with_every_controlword(app, view, monkeypatch):
    """Some drives will not act on anything without one of these set, so a
    bit that only reached the first write would look like an intermittent
    drive rather than a missing bit."""
    drive = wired(view, monkeypatch, FAULT)
    view.maker_control[15].setChecked(True)

    view._command(lambda: cia402.steps_to_enable(view.statusword))

    assert [value for _index, value in drive.written] == [
        0x8000,
        0x8080,
        0x8006,
        0x8007,
        0x800F,
    ]


def test_the_closing_halt_carries_them_too(app, window, view, monkeypatch):
    """The one write that stops the machine is the last place to drop a bit
    the drive may need before it will act on anything."""
    drive = running(view, monkeypatch)
    view.maker_control[11].setChecked(True)

    view.set_visible_to_user(False)

    assert (
        cia402.CONTROLWORD.index,
        cia402.ENABLE_OPERATION | cia402.HALT | 0x0800,
    ) in drive.written
    assert (cia402.TARGET_VELOCITY.index, 0) in drive.written, "and the target is not touched"


def test_the_maker_bits_in_the_statusword_are_shown(app, view):
    view._took(cia402.STATUSWORD, ENABLED | (1 << 14))
    assert view.maker_status.text() == "statusword bit 14"

    view._took(cia402.STATUSWORD, ENABLED)
    assert view.maker_status.text() == MAKER_NONE_SET
    assert "0x0237" in view.raw.text(), "the statusword itself is beside it, in hex"


def test_a_bit_number_is_never_shown_as_though_it_were_the_statusword():
    """ "statusword: 14" promised the statusword and gave a bit number. The
    two are different things, and the statusword itself is on the line
    above in hex."""
    assert maker_bits([14]) == "statusword bit 14"
    assert maker_bits([8, 15]) == "statusword bits 8, 15"
    assert maker_bits([]) == MAKER_NONE_SET
    assert ":" not in maker_bits([14]), "a colon there promises a value"


# --- a drive that will not answer ---------------------------------------------------
class RefusingDrive(FakeDrive):
    """Aborts every read, the way a drive in the wrong state does."""

    def read(self, obj):
        raise RuntimeError("abort 0x06010000, Unsupported access to an object")


def catching(view, monkeypatch, drive):
    """The pane against a drive, with the worker's try/except but no thread."""
    monkeypatch.setattr(view, "_drive", lambda: drive)
    monkeypatch.setattr(view, "_quietly", lambda: drive)

    def run(job, done):
        try:
            value, error = job(), None
        except Exception as exc:  # what the real worker hands back as `error`
            value, error = None, exc
        done(value, error)

    monkeypatch.setattr(view.app, "run_in_background", run)
    return drive


def refusing(view, monkeypatch):
    return catching(view, monkeypatch, RefusingDrive(0))


def test_an_sdo_that_aborts_while_polling_is_said_once(app, window, view, monkeypatch):
    """It used to be dropped in silence: the value stopped changing, which
    reads as a drive holding steady rather than as one not answering."""
    refusing(view, monkeypatch)
    view.node.addItem("Node 5", 5)

    view._read_one(*cia402.POSITION_ACTUAL.where)
    said = window.log.toPlainText()
    assert f"{cia402.POSITION_ACTUAL.name} (0x6064 sub 0)" in said
    assert "abort 0x06010000" in said

    before = window.log.toPlainText()
    for _ in range(5):
        view._read_one(*cia402.POSITION_ACTUAL.where)
    assert window.log.toPlainText() == before, "ten a second is one fact told over and over"


def test_it_is_said_again_after_the_object_answers(app, window, view, monkeypatch):
    refusing(view, monkeypatch)
    view.node.addItem("Node 5", 5)
    view._read_one(*cia402.POSITION_ACTUAL.where)

    catching(view, monkeypatch, FakeDrive(READY))  # it starts answering again
    view._read_one(*cia402.POSITION_ACTUAL.where)

    refusing(view, monkeypatch)
    before = window.log.toPlainText()
    view._read_one(*cia402.POSITION_ACTUAL.where)
    assert window.log.toPlainText() != before, "a new failure, so worth saying again"


def test_another_drive_does_not_inherit_the_silence(app, window, view, monkeypatch):
    refusing(view, monkeypatch)
    view.node.addItem("Node 5", 5)
    view._read_one(*cia402.POSITION_ACTUAL.where)

    view._forget()  # a different drive chosen

    before = window.log.toPlainText()
    view._read_one(*cia402.POSITION_ACTUAL.where)
    assert window.log.toPlainText() != before


# --- setting the mode -----------------------------------------------------------------
def test_setting_the_mode_reads_back_what_the_drive_took(app, view, monkeypatch):
    """The mode on screen comes from 0x6061, which only polling read -- so
    with polling off, setting the mode changed nothing visible."""
    drive = catching(view, monkeypatch, FakeDrive(READY))
    drive.mode = 3  # what it reports for 0x6061 once the write lands
    view.node.addItem("Node 5", 5)
    view.mode_box.setCurrentIndex(view.mode_box.findData(3))
    assert view.mode_box.currentData() == 3

    view._set_mode()

    assert (cia402.MODE.index, 3) in drive.written, "asked for"
    assert view.mode == 3, "and read back, rather than assumed"
    assert "Profile velocity" in view.mode_now.text()


def test_a_refused_mode_is_not_shown_as_taken(app, view, monkeypatch):
    """A drive is free to refuse a mode, and the mode display is the only
    thing that knows."""
    drive = catching(view, monkeypatch, FakeDrive(READY))
    drive.mode = 0  # it stayed in No mode
    view.node.addItem("Node 5", 5)
    view.mode_box.setCurrentIndex(view.mode_box.findData(4))
    assert view.mode_box.currentData() == 4

    view._set_mode()

    assert view.mode == 0, "what the drive says, not what was asked"
