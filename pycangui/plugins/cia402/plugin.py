# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Driving a CiA 402 motor controller: state, mode, targets and what it is doing.

Half of this screen could be a custom pane, and it is worth being clear about
which half. The modes, the targets and the actual values are ordinary objects
at standard indices -- point a custom pane at 0x6060, 0x60FF and 0x606C and you
have them, with no code at all.

The other half cannot be. A drive does nothing until it has been walked
through a state machine: 0x06, then 0x07, then 0x0F, with the next write
depending on what the drive answered to the last one, and a fault cleared by a
*rising edge* rather than by a value. The state itself is decoded from
overlapping masks of one word, so it is not a field either. That is the case
for this being a plugin, and it is the whole of the case: everything here that
did not need code was left as objects a pane could have shown.

The one question in front of a button is on **Enable**, which is the moment a
motor becomes able to move. It is asked once per drive per session, the same
as joining a live bus and transmitting onto one -- a dialog on every press
would be dismissed unread, and one that never appeared would be worse.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from pycangui.custom_panes.polling import DEFAULT_HZ, MAX_HZ, MIN_HZ, Poller, rate_text

# Relative, so that the copy of drive.py sitting beside *this* file is the
# one that runs. Named absolutely, an installed plugin would reach back
# into the one pycangui ships and editing your own would do nothing.
from . import drive as cia402
from .drive import Drive, Object

API_VERSION = 1
NAME = "CANopen motor control (CiA 402)"
VERSION = "1.0"
DESCRIPTION = "Drive state machine, modes and targets by CiA 402."

ENABLE_TITLE = "Enable the drive?"
ENABLE_TEXT = (
    "Node {node} is about to be put into Operation enabled, which is the state "
    "in which a motor turns.\n\n"
    "If a target is already set, it may move the moment it is enabled -- and "
    "what is bolted to the shaft moves with it.\n\n"
    "Enable node {node}?"
)

#: Loud on purpose, and both colours given: the pane has to say that a motor is
#: live in a way that survives being glanced at, and a background with no
#: foreground would be unreadable in half the themes it might be shown in.
BANNER_STYLE = (
    "background: #b34700; color: #ffffff; padding: 6px; border-radius: 3px; font-weight: bold;"
)
DEMANDING = "DEMAND ACTIVE - node {node} is enabled and acting on its target."
STALE = (
    "Node {node} was enabled when it was last read. Nothing is being read now, "
    "so what it is doing at this moment is not known here."
)

DISABLE_TOO_TIP = (
    "Off by default. Closing this pane always halts the drive and zeroes a\n"
    "speed or torque demand -- that part is not optional. Removing power on\n"
    "top of that is a decision about the machine rather than about the tool:\n"
    "on a vertical axis it is the load that decides, and whether a brake\n"
    "catches it is not something pycangui can know."
)

STOPPED = "Halted node {node} and zeroed its demand, because this pane was closing."
COULD_NOT_STOP = (
    "Node {node} was left enabled and could NOT be halted: {why}\n"
    "It is still acting on the last target it was given."
)
BUS_GONE = (
    "The bus closed while node {node} was enabled. It is still acting on the "
    "last target it was given, and nothing here can stop it now."
)

#: How the numbers are shown. Counts and per mille are what the profile says;
#: turning them into millimetres or amps needs the gearing and the motor
#: rating, which are the maker's and not ours to assume.
UNITS_NOTE = (
    "Counts, counts per second and per mille of rated torque, which is what the "
    "profile defines. Turning those into millimetres or amps needs the gearing "
    "and the motor rating, which are the drive's business and not pycangui's."
)


class NodeDrive(Drive):
    """A live node, read and written by index rather than through its EDS.

    Deliberately not the manager's ``sdo_read``: that goes through the object
    dictionary and answers with a typed value where the node has an EDS and
    with raw bytes where it has not. The profile already says what type each
    of these is, so packing them here means a drive nobody has an EDS for
    behaves exactly like one that has -- which is most drives, most of the time.
    """

    def __init__(self, node) -> None:
        self.node = node

    def read(self, obj: Object) -> int:
        return cia402.decode(obj, self.node.sdo.upload(obj.index, obj.sub))

    def write(self, obj: Object, value: int) -> None:
        self.node.sdo.download(obj.index, obj.sub, cia402.encode(obj, value))


class MotorView(QWidget):
    def __init__(self, app) -> None:
        super().__init__()
        self.app = app
        self.statusword = 0
        self.mode = 0
        self._busy = False
        #: Nothing has been read yet, so the state is not "Not ready to switch
        #: on" -- it is unknown, and saying the first would be inventing news.
        self._read_something = False

        self.node = QComboBox()
        self.node.setToolTip("Which drive.")
        self.node.currentIndexChanged.connect(lambda _i: self._forget())

        self.state = QLabel("Not polling")
        font = self.state.font()
        font.setPointSize(font.pointSize() + 3)
        font.setWeight(QFont.DemiBold)
        self.state.setFont(font)
        self.flags = QLabel("")
        self.flags.setWordWrap(True)
        self.raw = QLabel("")
        self.raw.setToolTip("The statusword itself, for when the state is not the whole story.")

        self.enable = QPushButton("Enable")
        self.enable.setToolTip(
            "Walk the drive to Operation enabled: shutdown, switch on, enable.\n"
            "Asks first -- this is the state in which a motor turns."
        )
        self.enable.clicked.connect(self._enable)
        self.disable = QPushButton("Disable")
        self.disable.setToolTip("Back to Switch on disabled. The motor is no longer driven.")
        self.disable.clicked.connect(
            lambda: self._command(lambda: cia402.steps_to_disable(self.statusword))
        )
        self.quick_stop = QPushButton("Quick stop")
        self.quick_stop.setToolTip(
            "Stop it the way the drive was configured to stop, which is not the\n"
            "same as removing power and is not a safety function."
        )
        self.quick_stop.clicked.connect(
            lambda: self._command(lambda: cia402.steps_to_quick_stop(self.statusword))
        )
        self.reset = QPushButton("Reset fault")
        self.reset.setToolTip(
            "Two writes, because the drive acts on the rising edge of bit 7\n"
            "rather than on its value."
        )
        self.reset.clicked.connect(lambda: self._command(cia402.clear_fault))

        commands = QHBoxLayout()
        for button in (self.enable, self.disable, self.quick_stop, self.reset):
            commands.addWidget(button)
        commands.addStretch()

        # --- mode ---------------------------------------------------------------------
        self.mode_box = QComboBox()
        for number, name in sorted(cia402.MODES.items()):
            self.mode_box.addItem(f"{number}  {name}", number)
        self.mode_box.setToolTip("Written to 0x6060. What the drive is actually in is beside it.")
        set_mode = QPushButton("Set mode")
        set_mode.clicked.connect(self._set_mode)
        self.mode_now = QLabel("")

        # --- target -------------------------------------------------------------------
        self.target = QSpinBox()
        self.target.setRange(-2_000_000_000, 2_000_000_000)
        self.target.setGroupSeparatorShown(True)
        self.set_target = QPushButton("Set target")
        self.set_target.clicked.connect(self._write_target)
        self.apply_target = QPushButton("Go")
        self.apply_target.setToolTip(
            "Profile position only: the drive takes the target on the rising\n"
            "edge of bit 4 and ignores it until then."
        )
        self.apply_target.clicked.connect(
            lambda: self._command(lambda: cia402.steps_to_apply_target(self.mode, self.statusword))
        )
        self.target_note = QLabel("")
        self.target_note.setWordWrap(True)
        self.target_note.setEnabled(False)

        settings = QGridLayout()
        settings.addWidget(QLabel("Mode:"), 0, 0)
        settings.addWidget(self.mode_box, 0, 1)
        settings.addWidget(set_mode, 0, 2)
        settings.addWidget(self.mode_now, 0, 3)
        settings.addWidget(QLabel("Target:"), 1, 0)
        settings.addWidget(self.target, 1, 1)
        settings.addWidget(self.set_target, 1, 2)
        settings.addWidget(self.apply_target, 1, 3)
        settings.setColumnStretch(4, 1)

        # --- what it is doing -----------------------------------------------------------
        self.actuals: dict[tuple[int, int], QLabel] = {}
        watched = QGridLayout()
        for row, obj in enumerate(
            (cia402.POSITION_ACTUAL, cia402.VELOCITY_ACTUAL, cia402.TORQUE_ACTUAL)
        ):
            value = QLabel("--")
            value.setFont(QFont("Consolas", 9))
            value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.actuals[obj.where] = value
            watched.addWidget(QLabel(f"{obj.name}:"), row, 0)
            watched.addWidget(value, row, 1)
            watched.addWidget(QLabel(obj.unit), row, 2)
        watched.setColumnStretch(3, 1)

        self.banner = QLabel("")
        self.banner.setWordWrap(True)
        self.banner.setStyleSheet(BANNER_STYLE)
        self.banner.hide()

        self.disable_too = QCheckBox("Disable the drive as well when this pane closes")
        self.disable_too.setToolTip(DISABLE_TOO_TIP)

        self.poll = QPushButton("Poll")
        self.poll.setCheckable(True)
        self.poll.setToolTip("Read the statusword, the mode and the actual values, over and over.")
        self.poll.toggled.connect(self._set_polling)
        self.hz = QDoubleSpinBox()
        self.hz.setRange(MIN_HZ, MAX_HZ)
        self.hz.setValue(DEFAULT_HZ)
        self.hz.setSuffix(" Hz")
        self.hz.setToolTip("A ceiling, not a promise: what is being achieved is beside it.")
        self.rate = QLabel("")

        polling = QHBoxLayout()
        polling.addWidget(self.poll)
        polling.addWidget(self.hz)
        polling.addWidget(self.rate)
        polling.addStretch()

        # --- the whole thing ----------------------------------------------------------
        where = QHBoxLayout()
        where.addWidget(QLabel("Node:"))
        where.addWidget(self.node)
        where.addStretch()

        status_box = QGroupBox("State")
        inside = QVBoxLayout(status_box)
        inside.addWidget(self.banner)
        inside.addWidget(self.state)
        inside.addWidget(self.flags)
        inside.addWidget(self.raw)
        inside.addLayout(commands)

        run_box = QGroupBox("Mode and target")
        run_inside = QVBoxLayout(run_box)
        run_inside.addLayout(settings)
        run_inside.addWidget(self.target_note)
        run_inside.addWidget(self.disable_too)

        watch_box = QGroupBox("What it is doing")
        watch_inside = QVBoxLayout(watch_box)
        watch_inside.addLayout(watched)
        watch_inside.addLayout(polling)

        units = QLabel(UNITS_NOTE)
        units.setWordWrap(True)
        units.setEnabled(False)

        layout = QVBoxLayout(self)
        layout.addLayout(where)
        layout.addWidget(status_box)
        layout.addWidget(run_box)
        layout.addWidget(watch_box)
        layout.addWidget(units)
        layout.addStretch()

        self.poller = Poller(self)
        self.poller.read.connect(self._read_one)
        self.poller.rate.connect(self._on_rate)
        self.hz.valueChanged.connect(self.poller.set_rate)
        self.poller.set_rate(self.hz.value())

        self._fill_nodes()
        app.canopen.node_seen.connect(lambda *_a: self._fill_nodes())
        app.bus.disconnected.connect(self._on_bus_lost)
        self._show_state()
        self._show_target()

    # --- which drive ---------------------------------------------------------------------
    def _fill_nodes(self) -> None:
        chosen = self.node.currentData()
        self.node.clear()
        for node_id in self.app.canopen.nodes():
            self.node.addItem(f"Node {node_id}", node_id)
        at = self.node.findData(chosen)
        self.node.setCurrentIndex(max(at, 0))

    def _forget(self) -> None:
        """A different drive knows nothing about the last one's state.

        Its mode goes too: leaving the last drive's would offer a target box
        for a mode this one may not even be in.
        """
        self._read_something = False
        self.statusword = 0
        self.mode = 0
        self.mode_now.setText("")
        for value in self.actuals.values():
            value.setText("--")
        self._show_state()
        self._show_target()

    def _drive(self) -> NodeDrive | None:
        node_id = self.node.currentData()
        node = self.app.canopen.node(node_id) if node_id is not None else None
        if node is None:
            self.app.warn("No drive selected, or it is not on the bus.")
            return None
        return NodeDrive(node)

    # --- reading it ------------------------------------------------------------------------
    def _set_polling(self, on: bool) -> None:
        if on and self.node.currentData() is None:
            self.app.warn("No drive selected, or it is not on the bus.")
            self.poll.setChecked(False)
            return
        if on:
            self.poller.set_objects(obj.where for obj in cia402.WATCHED)
            self.poller.start(self.hz.value())
        else:
            self.poller.stop()
            self._show_state()

    def _read_one(self, index: int, sub: int) -> None:
        drive = self._quietly()
        obj = next((o for o in cia402.WATCHED if o.where == (index, sub)), None)
        if drive is None or obj is None:
            self.poller.answered(index, sub)
            return

        def done(value, error) -> None:
            if error is None:
                self._took(obj, value)
            self.poller.answered(index, sub)

        self.app.run_in_background(lambda: drive.read(obj), done)

    def _quietly(self) -> NodeDrive | None:
        """The drive, without saying anything if there is not one.

        Polling asks many times a second, and a warning per attempt would fill
        the Event Log with one fact.
        """
        node_id = self.node.currentData()
        node = self.app.canopen.node(node_id) if node_id is not None else None
        return NodeDrive(node) if node is not None else None

    def _took(self, obj: Object, value: int) -> None:
        self._read_something = True
        # By where the object is rather than by which object it is. This file
        # can be loaded twice over -- once as part of pycangui, once as the
        # installed copy of itself -- and two constants describing 0x6041 are
        # equal without being the same one.
        if obj.where == cia402.STATUSWORD.where:
            self.statusword = int(value)
            self._show_state()
        elif obj.where == cia402.MODE_DISPLAY.where:
            self.mode = int(value)
            self.mode_now.setText(f"in {cia402.mode_name(self.mode)}")
            self._show_target()
        elif (label := self.actuals.get(obj.where)) is not None:
            label.setText(f"{int(value):,}")

    def _on_rate(self, requested: float, achieved: float) -> None:
        # Polling starting or stopping changes what the banner is entitled to
        # say, not only what the rate label says.
        self._show_banner()
        self.rate.setText(rate_text(requested, achieved, self.poller.running))

    # --- saying what it is ------------------------------------------------------------------
    def _show_state(self) -> None:
        if not self._read_something:
            self.state.setText("Not read yet")
            self.flags.setText("Press Poll, or one of the commands below.")
            self.raw.setText("")
            for button in (self.enable, self.disable, self.quick_stop, self.reset):
                button.setEnabled(True)
            return
        state = cia402.state_of(self.statusword)
        self.state.setText(state)
        flags = cia402.flags_of(self.statusword)
        self.flags.setText(", ".join(flags) if flags else "no flags set")
        self.raw.setText(f"Statusword 0x{self.statusword:04X}")
        faulted = cia402.is_faulted(self.statusword)
        self.enable.setEnabled(not cia402.is_enabled(self.statusword))
        self.quick_stop.setEnabled(cia402.is_enabled(self.statusword))
        self.reset.setEnabled(faulted)
        self.disable.setEnabled(state != "Switch on disabled")
        self._show_target()  # whether a target can be applied depends on this too
        self._show_banner()

    def _show_banner(self) -> None:
        """Say loudly when a motor is live, and say when that is only a memory.

        The distinction is the point. A drive that was enabled when it was last
        read may have been stopped by something else since, and a banner that
        went on asserting the old answer would be worse than no banner: it would
        be a confident statement about equipment nobody is watching.
        """
        node = self.node.currentData()
        if not cia402.is_enabled(self.statusword):
            self.banner.hide()
            return
        said = DEMANDING if self.poller.running else STALE
        self.banner.setText(said.format(node=node))
        self.banner.show()

    def _show_target(self) -> None:
        allowed, why = cia402.can_set_target(self.mode)
        obj = cia402.TARGET_FOR.get(self.mode)
        self.target.setEnabled(allowed)
        self.set_target.setEnabled(allowed)
        # Only in profile position, and only while the drive is enabled:
        # making the edge writes the enable controlword, so from anywhere
        # else this button would enable the drive without asking.
        self.apply_target.setEnabled(self.mode == 1 and cia402.is_enabled(self.statusword))
        if allowed and obj is not None:
            self.target_note.setText(f"{obj.name} (0x{obj.index:04X}), in {obj.unit}.")
        else:
            self.target_note.setText(why)

    # --- doing something to it -----------------------------------------------------------------
    def _enable(self) -> None:
        """The one command with a question in front of it."""
        node_id = self.node.currentData()
        if node_id is None:
            self.app.warn("No drive selected, or it is not on the bus.")
            return
        agreed = self.app.confirm.ask(
            self,
            f"cia402.enable.{node_id}",
            ENABLE_TITLE,
            ENABLE_TEXT.format(node=node_id),
        )
        if agreed:
            self._command(lambda: cia402.steps_to_enable(self.statusword))

    def _command(self, plan) -> None:
        """Read where the drive actually is, then work out what to write.

        The statusword is read again rather than taken from the last poll.
        Which writes a command needs *is* the state, so planning from a value
        read thirty seconds ago -- or, with polling switched off, from one
        never read at all -- is planning from a guess, and the guess would be
        that a drive nobody has asked is in the state whose statusword is zero.

        The refusals are the interesting half: a drive still reacting to a
        fault, or one whose statusword matches no state the profile has, is a
        drive that would ignore the command rather than obey it -- and a button
        that appears to work and does nothing is worse than one that says why
        it will not.
        """
        drive = self._drive()
        if drive is None or self._busy:
            return

        def planned(value, error) -> None:
            self._busy = False
            if error:
                self.app.warn(f"Reading the statusword failed: {error}")
                return
            self._took(cia402.STATUSWORD, value)
            try:
                steps = plan()
            except ValueError as exc:
                self.app.warn(str(exc))
                return
            if not steps:
                self.app.log(f"{cia402.state_of(self.statusword)} already; nothing to write.")
                return
            self._run(steps)

        self._busy = True
        self.app.run_in_background(lambda: drive.read(cia402.STATUSWORD), planned)

    def _run(self, steps) -> None:
        """Write a sequence of controlwords, off the GUI thread.

        The steps are worked out from the statusword this pane last read, so a
        drive that has moved on since will be walked from where it was. That is
        the case the state machine copes with by design: every one of these
        writes is a command rather than a transition, and a drive already in the
        state a command asks for stays in it.
        """
        drive = self._drive()
        if drive is None or self._busy or not steps:
            return

        def job():
            for step in steps:
                drive.write(cia402.CONTROLWORD, step.controlword)
            return steps[-1].what

        def done(_last, error) -> None:
            self._busy = False
            if error:
                self.app.warn(f"{steps[0].what} failed: {error}")
                return
            self.app.log(", ".join(step.what for step in steps))
            self._read_statusword()

        self._busy = True
        self.app.run_in_background(job, done)

    def _read_statusword(self) -> None:
        """After a command, so the screen says what happened rather than what was asked."""
        drive = self._quietly()
        if drive is None:
            return

        def done(value, error) -> None:
            if error is None:
                self._took(cia402.STATUSWORD, value)

        self.app.run_in_background(lambda: drive.read(cia402.STATUSWORD), done)

    def _set_mode(self) -> None:
        self._write(cia402.MODE, self.mode_box.currentData())

    def _write_target(self) -> None:
        obj = cia402.TARGET_FOR.get(self.mode)
        if obj is None:
            self.app.warn(cia402.can_set_target(self.mode)[1])
            return
        self._write(obj, self.target.value())

    def _write(self, obj: Object, value: int) -> None:
        drive = self._drive()
        if drive is None or self._busy:
            return
        try:
            cia402.encode(obj, value)  # refused here rather than wrapped by the drive
        except ValueError as exc:
            self.app.warn(str(exc))
            return

        def done(_result, error) -> None:
            self._busy = False
            if error:
                self.app.warn(f"Writing {obj.name} failed: {error}")
                return
            self.app.log(f"{obj.name} = {value}")

        self._busy = True
        self.app.run_in_background(lambda: drive.write(obj, value), done)

    # --- and stopping when nobody is looking ------------------------------------------------
    def set_visible_to_user(self, on: bool) -> None:
        """Put the drive down when the pane is put away.

        Two things, and the second is the one that matters. Polling stops
        because every read is a round trip on somebody's bus and a pane nobody
        can see is a pane with no reader. The *demand* stops because it would
        not otherwise: a drive holds the last controlword and target it was
        given and goes on acting on them, so a window that commanded motion and
        then went away has left a motor turning with nobody watching the screen
        that says so.

        The same rule the transmit panes follow. Out of sight is not a reason
        to go on sending.
        """
        if on:
            return
        if self.poller.running:
            self.poll.setChecked(False)
        self.stop_demand()

    def stop_demand(self, background: bool = True) -> None:
        """Halt the drive and take back a rate demand, if it is running at all.

        ``background`` is false when the tool itself is closing. The writes then
        go on the GUI thread, because the worker is about to be shut down and a
        safety stop handed to a queue that never runs is worse than none: it
        would look like one. A close that pauses for an SDO timeout is a fair
        price.
        """
        node = self.node.currentData()
        writes = cia402.stop_writes(self.mode, self.statusword, self.disable_too.isChecked())
        if not writes:
            return  # not driving anything, as far as anything here knows
        drive = self._quietly()
        if drive is None:
            self.app.error(COULD_NOT_STOP.format(node=node, why="it is no longer on the bus"))
            return

        def job():
            for obj, value in writes:
                drive.write(obj, value)

        def done(_result, error) -> None:
            if error:
                self.app.error(COULD_NOT_STOP.format(node=node, why=error))
                return
            self.app.log(STOPPED.format(node=node))
            # What it is doing now is no longer known: the pane is going, and
            # the next thing to read it should read it rather than trust this.
            self.statusword = 0
            self._read_something = False
            self._show_state()

        if background:
            self.app.run_in_background(job, done)
            return
        try:
            job()
        except Exception as exc:  # a bus that has already gone, most likely
            done(None, exc)
        else:
            done(None, None)

    def _on_bus_lost(self) -> None:
        """Say the thing that cannot be done anything about.

        There is no write to make -- the bus is what has gone -- so the only
        honest response is to state it: the drive is still doing what it was
        told, and nothing in this window can reach it now.
        """
        if cia402.is_enabled(self.statusword):
            self.app.error(BUS_GONE.format(node=self.node.currentData()))
        if self.poller.running:
            self.poll.setChecked(False)


def register(app) -> None:
    """Every way this pane can go away has to take the demand with it.

    Four of them, and missing any one leaves a motor turning with nothing on
    screen to say so: put away, closed for good, unloaded with the plugin, and
    the whole tool closing. The first three are the pane facade's business and
    the last is the window's, which is why the plugin API grew a hook for it.
    """
    app.add_pane(
        "main",
        "CANopen motor control",
        lambda _name: MotorView(app),
        area="right",
        shutdown=lambda view: view.stop_demand(background=False),
    )

    def shown(pane: str, on: bool) -> None:
        if (view := app.panes.view(pane)) is not None:
            view.set_visible_to_user(on)

    def closing() -> None:
        if (view := app.panes.view(f"{app.plugin}:main")) is not None:
            view.stop_demand(background=False)

    app.on_pane_shown(shown)
    app.on_closing(closing)
