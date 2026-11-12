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

from pycangui.canopen.emcy import describe_code
from pycangui.custom_panes.polling import DEFAULT_HZ, MAX_HZ, MIN_HZ, Poller, rate_text
from pycangui.ui import icons

# Relative, so that the copy of drive.py sitting beside *this* file is the
# one that runs. Named absolutely, an installed plugin would reach back
# into the one pycangui ships and editing your own would do nothing.
from . import drive as cia402
from .drive import Drive, Object

API_VERSION = 1


def _fixed_pitch() -> QFont:
    """pycangui's fixed-pitch font, which follows the system's text size.

    Asked for by name on a pycangui from before it had one to give, since a
    copy of this plugin in a workspace can meet an older pycangui.
    """
    try:
        from pycangui.ui import fonts
    except ImportError:
        return QFont("Consolas", 9)
    return fonts.mono()


NAME = "CANopen motor control (CiA 402)"
VERSION = "1.12"
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
    "speed or torque demand -- that part is not optional -- and so do Stop\n"
    "all and pausing pycangui's own traffic. Removing power on\n"
    "top of that is a decision about the machine rather than about the tool:\n"
    "on a vertical axis it is the load that decides, and whether a brake\n"
    "catches it is not something pycangui can know."
)

STOPPED = "Halted node {node} and zeroed its demand, because {why}."
#: Why the demand was taken away, for STOPPED.
CLOSING = "this pane was closing"
STOPPING_ALL = "everything that repeats was stopped"
PAUSING = "pycangui's own traffic was paused, and nothing is watching the drive"
COULD_NOT_STOP = (
    "Node {node} was left enabled and could NOT be halted: {why}\n"
    "It is still acting on the last target it was given."
)
BUS_GONE = (
    "The bus closed while node {node} was enabled. It is still acting on the "
    "last target it was given, and nothing here can stop it now."
)

#: How the numbers are shown. Position and velocity are in whatever units the
#: drive was set up in, which only the drive can say.
UNITS_NOTE = (
    "Position and velocity are in the drive's own units, as 0x60A8 and 0x60A9 "
    "say when it has them, and torque in per mille of rated. Nothing is "
    "converted: that needs the gearing and the motor rating, which are the "
    "drive's business and not pycangui's. Velocity mode's own objects are in rpm."
)


def about(obj: Object, unit: str | None = None) -> str:
    """Which object a control reads or writes, for its tooltip.

    Every number on this pane is an index in somebody's object dictionary,
    and which one is the first thing anybody comparing this screen with a
    drive manual needs. It was only on the limits, and not even there once
    an EDS had been looked at.
    """
    return f"0x{obj.index:04X} sub {obj.sub}: {obj.name}, in {unit or obj.unit}."


DEMAND_TIP = (
    "What the drive is asking of itself, before the motor has followed.\n"
    "Still at zero with a target set: the drive has not taken it -- halted,\n"
    "or no acceleration to ramp with. Moving while the actual value is\n"
    "not: the motor is not following."
)
NO_DEMAND_TIP = "{mode} has no demand to show."
ERROR_CODE_TIP = "0x603F, read when the drive went to Fault: why it did."


def error_text(code: int) -> str:
    """0x603F as a line beside the state: the code, and what CiA 301 calls it."""
    return f"Error code 0x{code:04X}: {describe_code(code)}"


WRITE_LIMITS_TIP = (
    "Write all three to the drive. A limit is what the machine cannot\n"
    "exceed, so raising one lets it do more than it could a moment ago."
)
MISSING_TIP = "This drive's EDS does not have this object, so there is nothing to write to."
MODE_TIP = "Written to 0x6060. What the drive is actually in is beside it."
MODE_SUPPORTED_TIP = (
    "Written to 0x6060. Only the modes 0x6502 says this drive has are\n"
    "listed, and No mode, which is how a drive is told to be in none of\n"
    "them."
)
MAKER_CONTROL_TIP = (
    "Held in every controlword this pane writes, the commands and the\n"
    "closing halt included. A bit some drives need before they will act\n"
    "on anything would be worse than useless if it were dropped from the\n"
    "one write that stops the machine."
)
MAKER_STATUS_TIP = "The maker's bits in the statusword the drive last answered with."
MAKER_NONE_SET = "no maker bits in the statusword"
MAKER_NOT_READ = "statusword not read yet"
LIMITS_TITLE = "Change the drive's limits?"
LIMITS_WARNING = (
    "These are the limits the drive holds itself to in every mode: the\n"
    "most torque it will apply, and the fastest it will go. Raising one\n"
    "lets the machine do more than it could before, whatever is asking.\n\n"
    "Write them?"
)


def maker_bits(bits) -> str:
    """Which of the maker's statusword bits are set, in words.

    "statusword: 14" promised the statusword and gave a bit number, which
    are two different things and one of them is already on the line above
    in hex. So this names the bit rather than appearing to equal it, and
    the number is a position counted from zero, in decimal.
    """
    if not bits:
        return MAKER_NONE_SET
    said = ", ".join(str(bit) for bit in bits)
    return f"statusword bit {said}" if len(bits) == 1 else f"statusword bits {said}"


#: Beside Go in a mode that acts on its target as soon as it is written.
GO_ELSEWHERE = "(Profile position only)"

#: Beside the node: its NMT state, which is not the drive's CiA 402 state.
NMT_TIP = (
    "The node's NMT state, from its heartbeat -- the CANopen state, not the\n"
    "drive's. SDO, which is all this pane writes, works in Pre-operational and\n"
    "Operational; PDOs only flow in Operational, so a controlword or target\n"
    "mapped in an RPDO only arrives that way while it is."
)
#: Under the node, when an RPDO carries something this pane writes.
RPDO_CONFLICT = (
    "{lines}. Whatever sends {which} overrides what this pane writes {when}: a "
    "write from here can appear to do nothing, or flick back."
)
RPDO_CONFLICT_TIP = (
    "From the node's PDO configuration as pycangui holds it: from the EDS, or\n"
    "from the node itself after Read PDO config in the CANopen pane. An RPDO\n"
    "is the drive being written to by whoever sends it -- CAN Transmit here,\n"
    "or another master -- and it only arrives while the node is Operational."
)
RPDO_WARNING_STYLE = "color: #e07000; font-weight: bold;"
START_TITLE = "Start the node?"
START_TEXT = (
    "Node {node} is about to be made Operational. Its PDOs then flow both ways: "
    "an RPDO that carries the controlword or a target is acted on the moment it "
    "arrives, from whoever sends it.\n\n"
    "Start node {node}?"
)
NMT_UNKNOWN_TIP = (
    "This node sends no heartbeat, so its NMT state cannot be seen: pycangui\n"
    "knows a node's state only from its heartbeat. Nothing is changed on the\n"
    "node to find out."
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
        #: What the node list holds, so a heartbeat that says nothing new
        #: does not rebuild it. See _fill_nodes.
        self._nodes_listed: list[int] = []
        #: Objects whose last read failed and has been complained about. A
        #: poll asks ten times a second, so an abort said every time would
        #: fill the Event Log with one fact. See _went_wrong.
        self._complained: set[tuple[int, int]] = set()
        #: The drive's own units, "position" and "velocity", from 0x60A8 and
        #: 0x60A9: empty until read, or when it has neither.
        self._units: dict[str, str] = {}
        #: Each unit on screen, with the object it is the unit of and the
        #: widgets that say which object in their tooltip.
        self._unit_labels: list[tuple[Object, QLabel, tuple]] = []

        self.node = QComboBox()
        self.node.setToolTip("Which drive.")
        self.node.currentIndexChanged.connect(lambda _i: self._forget())
        #: The node's NMT state, from its heartbeat: None when it sends none.
        self.nmt = QLabel("")
        self._nmt_state: str | None = None
        #: The selected node's NMT state, set from here: the two a drive is
        #: worked in. Start is asked about first, as Enable is.
        self.pre_op_btn = icons.beside(QPushButton("Pre-operational"), icons.PAUSE)
        self.pre_op_btn.setToolTip(
            "Put the selected node in Pre-operational (NMT): SDO goes on working,\n"
            "and its PDOs stop -- an RPDO no longer overrides this pane."
        )
        self.pre_op_btn.clicked.connect(lambda: self._nmt("PRE-OPERATIONAL"))
        self.start_btn = icons.beside(QPushButton("Start"), icons.START)
        self.start_btn.setToolTip(
            "Make the selected node Operational (NMT): its PDOs flow. Asked first,\n"
            "once per node, since an RPDO it is then sent may move it."
        )
        self.start_btn.clicked.connect(lambda: self._nmt("OPERATIONAL"))
        #: Said when an RPDO carries the controlword, the mode or the target.
        self.rpdo_warning = QLabel("")
        self.rpdo_warning.setWordWrap(True)
        self.rpdo_warning.setStyleSheet(RPDO_WARNING_STYLE)
        self.rpdo_warning.setToolTip(RPDO_CONFLICT_TIP)
        self.rpdo_warning.hide()

        self.state = QLabel("Not polling")
        font = self.state.font()
        font.setPointSize(font.pointSize() + 3)
        font.setWeight(QFont.DemiBold)
        self.state.setFont(font)
        self.flags = QLabel("")
        self.flags.setWordWrap(True)
        self.raw = QLabel("")
        #: Why it is in Fault: 0x603F, read once as it gets there.
        self.error_code = QLabel("")
        self.error_code.setToolTip(ERROR_CODE_TIP)
        self.error_code.hide()
        #: Whether 0x603F has been asked for this fault, so a poll does not ask
        #: again every round while it lasts.
        self._asked_why = False
        self.raw.setToolTip("The statusword itself, for when the state is not the whole story.")

        # --- the bits the profile leaves to the maker -----------------------------------
        # Inside State rather than in a box of their own: this is the rest of
        # the statusword, and the controlword bits beside it are what the
        # commands below will carry. By number and unnamed, because one
        # drive's bit 15 is a brake release and another's is a spindle
        # orientation request. The manual says which; nothing here can.
        self.maker_control: dict[int, QCheckBox] = {}
        maker_row = QHBoxLayout()
        heading = QLabel("Manufacturer bits:")
        heading.setToolTip(MAKER_CONTROL_TIP)
        maker_row.addWidget(heading)
        for bit in cia402.MANUFACTURER_CONTROL_BITS:
            box = QCheckBox(str(bit))
            box.setToolTip(f"Controlword bit {bit}.\n{MAKER_CONTROL_TIP}")
            self.maker_control[bit] = box
            maker_row.addWidget(box)
        self.maker_status = QLabel(MAKER_NOT_READ)
        self.maker_status.setToolTip(MAKER_STATUS_TIP)
        maker_row.addWidget(self.maker_status)
        maker_row.addStretch()

        self.enable = icons.beside(QPushButton("Enable"), icons.ENABLE)
        self.enable.setToolTip(
            "Walk the drive to Operation enabled: shutdown, switch on, enable.\n"
            "Asks first -- this is the state in which a motor turns."
        )
        self.enable.clicked.connect(self._enable)
        self.disable = icons.beside(QPushButton("Disable"), icons.DISABLE)
        self.disable.setToolTip("Back to Switch on disabled. The motor is no longer driven.")
        self.disable.clicked.connect(
            lambda: self._command(lambda: cia402.steps_to_disable(self.statusword))
        )
        self.quick_stop = icons.beside(QPushButton("Quick stop"), icons.STOP)
        self.quick_stop.setToolTip(
            "Stop it the way the drive was configured to stop, which is not the\n"
            "same as removing power and is not a safety function."
        )
        self.quick_stop.clicked.connect(
            lambda: self._command(lambda: cia402.steps_to_quick_stop(self.statusword))
        )
        self.reset = icons.beside(QPushButton("Reset fault"), icons.RESET)
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
        self.mode_box.setToolTip(MODE_TIP)
        set_mode = icons.beside(QPushButton("Set mode"), icons.SWITCH)
        set_mode.clicked.connect(self._set_mode)
        self.mode_now = QLabel("")

        # --- target -------------------------------------------------------------------
        self.target = QSpinBox()
        self.target.setRange(-2_000_000_000, 2_000_000_000)
        self.target.setGroupSeparatorShown(True)
        self.set_target = icons.beside(QPushButton("Set target"), icons.PICK)
        self.set_target.clicked.connect(self._write_target)
        self.apply_target = icons.beside(QPushButton("Go"), icons.START)
        self.apply_target.setToolTip(
            "Profile position only: the drive takes the target on the rising\n"
            "edge of bit 4 and ignores it until then."
        )
        self.apply_target.clicked.connect(
            lambda: self._command(lambda: cia402.steps_to_apply_target(self.mode, self.statusword))
        )
        #: Beside Go, in a mode that moves as soon as its target is written:
        #: a greyed button alone left people looking for what it was for.
        self.go_note = QLabel("")
        self.go_note.setEnabled(False)
        self.target_note = QLabel("")
        self.target_note.setWordWrap(True)
        self.target_note.setEnabled(False)

        # --- limits -------------------------------------------------------------------
        # Not targets: a drive in any mode is held to its maximum torque, and
        # the speed limits cap whatever a profile or a controller asks for.
        # Read when a drive is chosen, written when somebody asks. A box of
        # their own, one to a line: three limits along a single row read as
        # one setting, and they are three separate promises about a machine.
        self.limits: dict[tuple[int, int], QSpinBox] = {}
        limits_grid = QGridLayout()
        for row, obj in enumerate(cia402.LIMITS):
            box = QSpinBox()
            box.setRange(0, 2_000_000_000)
            box.setGroupSeparatorShown(True)
            self.limits[obj.where] = box
            name = QLabel(f"{obj.name}:")
            unit = QLabel(obj.unit)
            for widget in (name, box, unit):
                widget.setToolTip(about(obj))
            self._unit_labels.append((obj, unit, (name, unit)))
            limits_grid.addWidget(name, row, 0)
            limits_grid.addWidget(box, row, 1)
            limits_grid.addWidget(unit, row, 2)
        limits_grid.setColumnStretch(3, 1)

        self.read_limits_btn = icons.beside(QPushButton("Read limits"), icons.READ)
        self.read_limits_btn.setToolTip("Read all three from the drive.")
        self.read_limits_btn.clicked.connect(self._read_limits)
        self.write_limits_btn = icons.beside(QPushButton("Write limits"), icons.WRITE)
        self.write_limits_btn.setToolTip(WRITE_LIMITS_TIP)
        self.write_limits_btn.clicked.connect(self._write_limits)
        limits_buttons = QHBoxLayout()
        limits_buttons.addStretch()
        limits_buttons.addWidget(self.read_limits_btn)
        limits_buttons.addWidget(self.write_limits_btn)

        settings = QGridLayout()
        settings.addWidget(QLabel("Mode:"), 0, 0)
        settings.addWidget(self.mode_box, 0, 1)
        settings.addWidget(set_mode, 0, 2)
        settings.addWidget(self.mode_now, 0, 3)
        settings.addWidget(QLabel("Target:"), 1, 0)
        settings.addWidget(self.target, 1, 1)
        settings.addWidget(self.set_target, 1, 2)
        settings.addWidget(self.apply_target, 1, 3)
        settings.addWidget(self.go_note, 1, 4)
        settings.setColumnStretch(5, 1)

        # --- what it is doing -----------------------------------------------------------
        self.actuals: dict[tuple[int, int], QLabel] = {}
        watched = QGridLayout()
        for row, obj in enumerate(
            (cia402.POSITION_ACTUAL, cia402.VELOCITY_ACTUAL, cia402.TORQUE_ACTUAL)
        ):
            value = QLabel("--")
            # Fixed pitch, so the digits do not move sideways as they change,
            # and at the size of the text around them.
            value.setFont(_fixed_pitch())
            value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.actuals[obj.where] = value
            name = QLabel(f"{obj.name}:")
            unit = QLabel(obj.unit)
            for widget in (name, value, unit):
                widget.setToolTip(about(obj))
            if obj.where == cia402.VELOCITY_ACTUAL.where:
                #: Relabelled in velocity mode, which reads 0x6044 in its place.
                self._velocity_row = (name, value, unit)
            else:
                self._unit_labels.append((obj, unit, (name, value, unit)))
            watched.addWidget(name, row, 0)
            watched.addWidget(value, row, 1)
            watched.addWidget(unit, row, 2)
        #: The mode's demand, under what the drive is actually doing: the
        #: object changes with the mode, so the row is relabelled with it.
        demand = (QLabel(""), QLabel("--"), QLabel(""))
        demand[1].setFont(_fixed_pitch())
        demand[1].setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._demand_row = demand
        for column, widget in enumerate(demand):
            watched.addWidget(widget, 3, column)
        watched.setColumnStretch(3, 1)

        self.banner = QLabel("")
        self.banner.setWordWrap(True)
        self.banner.setStyleSheet(BANNER_STYLE)
        self.banner.hide()

        self.disable_too = QCheckBox("Disable the drive as well when this pane closes")
        self.disable_too.setToolTip(DISABLE_TOO_TIP)

        # A tick, as on a custom pane: polling is a state the pane is in, and a
        # button that stays down did not say which way it was.
        self.poll = QCheckBox("Poll")
        self.poll.setToolTip(
            "Read these, over and over:\n"
            + "\n".join(f"    {about(obj)}" for obj in cia402.WATCHED)
            + "\nand the demand of the mode the drive is in."
        )
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
        where.addWidget(self.nmt)
        where.addWidget(self.pre_op_btn)
        where.addWidget(self.start_btn)
        where.addStretch()

        status_box = QGroupBox("State")
        inside = QVBoxLayout(status_box)
        inside.addWidget(self.banner)
        inside.addWidget(self.state)
        inside.addWidget(self.flags)
        inside.addWidget(self.error_code)
        inside.addWidget(self.raw)
        inside.addLayout(maker_row)
        inside.addLayout(commands)

        run_box = QGroupBox("Mode and target")
        run_inside = QVBoxLayout(run_box)
        run_inside.addLayout(settings)
        run_inside.addWidget(self.target_note)
        run_inside.addWidget(self.disable_too)

        limits_box = QGroupBox("Limits")
        limits_inside = QVBoxLayout(limits_box)
        limits_inside.addLayout(limits_grid)
        limits_inside.addLayout(limits_buttons)

        watch_box = QGroupBox("What it is doing")
        watch_inside = QVBoxLayout(watch_box)
        watch_inside.addLayout(watched)
        watch_inside.addLayout(polling)

        units = QLabel(UNITS_NOTE)
        units.setWordWrap(True)
        units.setEnabled(False)

        layout = QVBoxLayout(self)
        layout.addLayout(where)
        layout.addWidget(self.rpdo_warning)
        layout.addWidget(status_box)
        layout.addWidget(run_box)
        layout.addWidget(limits_box)
        layout.addWidget(watch_box)
        layout.addWidget(units)
        layout.addStretch()

        self.poller = Poller(self)
        self.poller.read.connect(self._read_one)
        self.poller.rate.connect(self._on_rate)
        self.hz.valueChanged.connect(self.poller.set_rate)
        self.poller.set_rate(self.hz.value())
        if getattr(app, "api_version", 1) >= 2:  # an older pycangui has no pause
            self.poller.set_paused(app.paused)

        self._fill_nodes()
        # A bound method rather than a lambda, and that is not a style choice.
        # The manager outlives this pane, so a connection to it has to end
        # when the pane does: Qt drops a bound method of a widget as the
        # widget is destroyed, and holds on to a lambda for ever. The lambda
        # was still being called after the pane closed, reaching for a combo
        # box whose C++ half had already gone.
        app.canopen.node_seen.connect(self._on_node_seen)
        app.canopen.eds_loaded.connect(self._on_eds_loaded)
        app.canopen.node_lost.connect(self._on_node_lost)
        app.canopen.pdo_config.connect(self._on_pdo_config)
        app.bus.disconnected.connect(self._on_bus_lost)
        self._show_state()
        self._show_target()

    # --- which drive ---------------------------------------------------------------------
    def _on_node_seen(self, node_id: int, _state: str) -> None:
        self._fill_nodes()
        if node_id == self.node.currentData():
            self._show_nmt()

    def _on_node_lost(self, node_id: int) -> None:
        if node_id == self.node.currentData():
            self._show_nmt()

    def _nmt(self, command: str) -> None:
        """Send the selected node an NMT command; its heartbeat says what came of it."""
        node_id = self.node.currentData()
        if node_id is None:
            self.app.warn("No drive selected, or it is not on the bus.")
            return
        if command == "OPERATIONAL" and not self.app.confirm.ask(
            self, f"cia402.start.{node_id}", START_TITLE, START_TEXT.format(node=node_id)
        ):
            return
        self.app.canopen.nmt(node_id, command)
        self.app.log(f"Node {node_id}: NMT {command.lower()} sent")

    def _on_pdo_config(self, node_id: int) -> None:
        if node_id == self.node.currentData():
            self._show_rpdo_conflicts()

    def _show_rpdo_conflicts(self) -> None:
        """Warn when an enabled RPDO carries what this pane writes.

        Only said, never acted on: which of the two should win -- the RPDO's
        sender or this pane -- is a decision about the machine.
        """
        node_id = self.node.currentData()
        configs = [] if node_id is None else self.app.canopen.pdo_configs(node_id)
        lines = cia402.rpdo_conflicts(configs, self.mode)
        if not lines:
            self.rpdo_warning.hide()
            return
        numbers = sorted({line.split()[0] for line in lines})
        operational = self._nmt_state == "OPERATIONAL"
        self.rpdo_warning.setText(
            RPDO_CONFLICT.format(
                lines="; ".join(lines),
                which=" and ".join(numbers),
                when="now, while the node is Operational"
                if operational
                else "whenever the node is Operational",
            )
        )
        self.rpdo_warning.show()

    def _show_nmt(self) -> None:
        """The selected node's NMT state, as its heartbeat last gave it.

        From the CANopen manager, which hears every heartbeat: nothing is
        sent to find it out. A node with no heartbeat says so, rather than
        showing a state somebody might take for the drive's.
        """
        node_id = self.node.currentData()
        manager = self.app.canopen
        state = None if node_id is None else manager.nmt_states.get(node_id)
        self._nmt_state = state
        for button in (self.pre_op_btn, self.start_btn):
            button.setEnabled(node_id is not None)
        if node_id is None:
            self.nmt.setText("")
            self.nmt.setToolTip("")
        elif state is None:
            self.nmt.setText("NMT: not known (no heartbeat)")
            self.nmt.setToolTip(NMT_UNKNOWN_TIP)
        else:
            lost = node_id in getattr(manager, "lost_nodes", ())
            said = state.capitalize()
            self.nmt.setText(f"NMT: lost (was {said})" if lost else f"NMT: {said}")
            self.nmt.setToolTip(NMT_TIP)
        self._show_rpdo_conflicts()

    def _fill_nodes(self) -> None:
        """Rebuild the list, but only when the list has actually changed.

        This is called on node_seen, which arrives with every heartbeat --
        once a second on a quiet bus. Rebuilding then cleared the combo,
        which changed the current index, which forgot everything read so
        far: the values appeared and blanked, appeared and blanked.
        """
        nodes = self.app.canopen.nodes()
        if nodes == self._nodes_listed:
            return
        self._nodes_listed = nodes
        chosen = self.node.currentData()
        self.node.blockSignals(True)  # rebuilding is not somebody choosing
        self.node.clear()
        for node_id in nodes:
            self.node.addItem(f"Node {node_id}", node_id)
        at = self.node.findData(chosen)
        self.node.setCurrentIndex(max(at, 0))
        self.node.blockSignals(False)
        if self.node.currentData() != chosen:
            self._forget()

    def _on_eds_loaded(self, node_id: int, _path: str, _product: str) -> None:
        """An EDS says which objects a drive has, so it decides what is offered.

        Loaded after the pane was opened it would otherwise change nothing
        until the drive was picked again: the controls stayed live for
        objects the file says are not there.
        """
        if node_id == self.node.currentData():
            self._fit_to_drive()

    def _forget(self) -> None:
        """A different drive knows nothing about the last one's state.

        Its mode goes too: leaving the last drive's would offer a target box
        for a mode this one may not even be in.
        """
        self._read_something = False
        self.statusword = 0
        self.mode = 0
        self._asked_why = False
        self._complained.clear()  # another drive's refusals are not this one's
        self._units.clear()  # nor its units
        self.mode_now.setText("")
        for value in self.actuals.values():
            value.setText("--")
        self._show_velocity_row()
        self._show_demand_row()
        self._show_nmt()
        self._show_state()
        self._show_target()
        # What this drive has is this drive's business: its EDS says which
        # objects exist, and 0x6502 which modes it implements.
        self._fit_to_drive()

    # --- what this drive actually has ---------------------------------------------------
    def _object_dictionary(self):
        """The selected node's EDS, if it has one loaded."""
        node_id = self.node.currentData()
        node = self.app.canopen.node(node_id) if node_id is not None else None
        od = getattr(node, "object_dictionary", None)
        return od if od else None  # an empty one is "no EDS", not "no objects"

    def _has(self, obj: Object) -> bool:
        """Whether this drive has an object, as far as anything here knows.

        With no EDS the answer is yes: the profile says these exist, most
        drives have them, and grey controls because pycangui was never
        given a file would be worse than an SDO that comes back refused.
        With an EDS, the file is believed -- that is what it is for.
        """
        od = self._object_dictionary()
        return True if od is None else obj.index in od

    def _fit_to_drive(self) -> None:
        """Offer what this drive has, and no more.

        Two sources, answering different questions. The EDS says which
        objects exist, so a control for one that does not is switched off
        rather than left to fail with an abort code nobody reads. 0x6502
        says which modes the drive implements, which an EDS cannot: every
        drive's EDS lists 0x6060, and none of them says that this one
        cannot do torque.
        """
        for where, box in self.limits.items():
            obj = next(o for o in cia402.LIMITS if o.where == where)
            has = self._has(obj)
            box.setEnabled(has)
            # Which object it is stays in the tooltip either way. It used to
            # be replaced by the note, so looking at a drive with an EDS was
            # the one time the pane stopped saying what it was writing to.
            said = about(obj, self._unit(obj))
            box.setToolTip(said if has else f"{said}\n{MISSING_TIP}")
        for where, label in self.actuals.items():
            if not self._has(self._shown_in(where)):
                label.setText("not in the EDS")
            elif label.text() == "not in the EDS":
                label.setText("--")  # a different drive, or an EDS since loaded
        self._show_units()
        self._read_supported_modes()
        self._read_units()

    # --- the drive's own units ---------------------------------------------------------
    def _unit(self, obj: Object) -> str:
        """The unit to show beside an object: the drive's, once it has said."""
        return self._units.get(obj.measures, obj.unit)

    def _read_units(self) -> None:
        """Ask 0x60A8 and 0x60A9 what units position and velocity are in.

        Asked once, when a drive is chosen. A drive without them -- older
        ones -- keeps the plain "velocity units": it may be set up in counts
        per second, rpm or millimetres per second, and nothing on the bus
        says which.
        """
        drive = self._quietly()
        if drive is None:
            return
        for measures, obj in cia402.SI_UNITS.items():
            if not self._has(obj):
                continue

            def done(value, error, measures=measures) -> None:
                if error:
                    return  # an older drive without it: the plain label stands
                if (unit := cia402.si_unit(int(value))) is not None:
                    self._units[measures] = unit
                    self._show_units()

            self.app.run_in_background(lambda o=obj: drive.read(o), done)

    def _show_units(self) -> None:
        for obj, label, widgets in self._unit_labels:
            label.setText(self._unit(obj))
            for widget in widgets:
                widget.setToolTip(about(obj, self._unit(obj)))
        self._show_velocity_row()
        self._show_demand_row()
        self._show_target()

    def _read_supported_modes(self) -> None:
        """Ask 0x6502 which modes this drive has, and offer only those."""
        self._show_modes(None)  # until it answers, offer them all
        drive = self._quietly()
        if drive is None or not self._has(cia402.SUPPORTED_MODES):
            return

        def done(value, error) -> None:
            if not self._went_wrong(cia402.SUPPORTED_MODES, error):
                self._show_modes(cia402.modes_in(int(value)))

        self.app.run_in_background(lambda: drive.read(cia402.SUPPORTED_MODES), done)

    def _show_modes(self, supported: set[int] | None) -> None:
        """Rebuild the mode list, keeping what was chosen if it survives."""
        chosen = self.mode_box.currentData()
        self.mode_box.blockSignals(True)
        self.mode_box.clear()
        for number, name in sorted(cia402.MODES.items()):
            # No mode is always offered: writing 0 is how a drive is told
            # to be in none of them, which is not something 0x6502 lists.
            if supported is None or number == 0 or number in supported:
                self.mode_box.addItem(f"{number}  {name}", number)
        at = self.mode_box.findData(chosen)
        self.mode_box.setCurrentIndex(max(at, 0))
        self.mode_box.blockSignals(False)
        self.mode_box.setToolTip(MODE_TIP if supported is None else MODE_SUPPORTED_TIP)

    # --- limits ----------------------------------------------------------------------
    def _read_limits(self) -> None:
        """Read all three, so the boxes show what the drive is actually held to."""
        drive = self._drive()
        if drive is None:
            return
        for obj in cia402.LIMITS:
            if not self._has(obj):
                continue

            def done(value, error, obj=obj) -> None:
                if not self._went_wrong(obj, error):
                    self.limits[obj.where].setValue(int(value))

            self.app.run_in_background(lambda o=obj: drive.read(o), done)

    def _write_limits(self) -> None:
        """Write all three. A question first, because a limit is a promise
        about what the machine cannot do, and raising one lets it do more."""
        drive = self._drive()
        if drive is None:
            return
        node_id = self.node.currentData()
        if not self.app.confirm.ask(self, f"cia402.limits.{node_id}", LIMITS_TITLE, LIMITS_WARNING):
            return
        for obj in cia402.LIMITS:
            if not self._has(obj):
                continue
            self._write_limit(drive, obj, self.limits[obj.where].value())

    def _write_limit(self, drive: NodeDrive, obj: Object, value: int) -> None:
        """One limit, with the value bound rather than read again later."""

        def done(_result, error) -> None:
            if error:
                self.app.warn(f"{obj.name}: {error}")
            else:
                self.app.log(f"{obj.name} <- {value:,}")

        self.app.run_in_background(lambda: drive.write(obj, value), done)

    # --- the bits the profile leaves to the maker ----------------------------------------
    def _maker_bits(self) -> int:
        """The ticked manufacturer bits, as a word to hold in a controlword.

        Held in every controlword this pane writes, the closing halt
        included. Some drives will not act on anything without one of these
        set, so a bit dropped from the one write that stops the machine
        would be worse than a bit that was never offered.
        """
        return cia402.mask_of(bit for bit, box in self.maker_control.items() if box.isChecked())

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
            self.poller.set_objects(obj.where for obj in cia402.watched(self.mode))
            self.poller.start(self.hz.value())
        else:
            self.poller.stop()
            self._show_state()

    def _read_one(self, index: int, sub: int) -> None:
        drive = self._quietly()
        obj = next((o for o in cia402.watched(self.mode) if o.where == (index, sub)), None)
        # An object this drive's EDS does not list is not asked for: the
        # answer would be an abort code, many times a second, and the line
        # on screen already says the file has no such object.
        if drive is None or obj is None or not self._has(obj):
            self.poller.answered(index, sub)
            return

        def done(value, error) -> None:
            if not self._went_wrong(obj, error):
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

    def _went_wrong(self, obj: Object, error) -> bool:
        """Say that a read failed, once, and answer whether it did.

        An SDO that aborts while polling used to be dropped in silence:
        the value on screen simply stopped changing, which reads as a
        drive holding steady rather than as a drive not answering. Now it
        is reported -- but once, and again only after that object has
        answered, because ten aborts a second is one fact told over and
        over.
        """
        if error is None:
            self._complained.discard(obj.where)
            return False
        if obj.where not in self._complained:
            self._complained.add(obj.where)
            self.app.warn(f"{obj.name} (0x{obj.index:04X} sub {obj.sub}): {error}")
        return True

    def _read_back(self, obj: Object) -> None:
        """Read one object again after writing to the drive.

        So the screen says what happened rather than what was asked: a
        drive is free to refuse a mode, or to take a moment over it, and
        either way what it reports is the answer.
        """
        drive = self._quietly()
        if drive is None:
            return

        def done(value, error) -> None:
            if not self._went_wrong(obj, error):
                self._took(obj, value)

        self.app.run_in_background(lambda: drive.read(obj), done)

    def _took(self, obj: Object, value: int) -> None:
        self._read_something = True
        # By where the object is rather than by which object it is. This file
        # can be loaded twice over -- once as part of pycangui, once as the
        # installed copy of itself -- and two constants describing 0x6041 are
        # equal without being the same one.
        if obj.where == cia402.STATUSWORD.where:
            self.statusword = int(value)
            self._show_state()
            self._ask_why()
        elif obj.where == cia402.MODE_DISPLAY.where:
            was, self.mode = self.mode, int(value)
            self.mode_now.setText(f"in {cia402.mode_name(self.mode)}")
            self._show_target()
            if (was == 2) != (self.mode == 2):
                self._show_velocity_row()
            if was != self.mode:
                self._show_demand_row()
                self._show_rpdo_conflicts()  # the target it writes changed
        elif obj.where == cia402.VL_VELOCITY_ACTUAL.where:
            self._velocity_row[1].setText(f"{int(value):,}")
        elif (demand := cia402.DEMAND_FOR.get(self.mode)) is not None and obj.where == demand.where:
            self._demand_row[1].setText(f"{int(value):,}")
        elif (label := self.actuals.get(obj.where)) is not None:
            label.setText(f"{int(value):,}")

    def _shown_in(self, where: tuple[int, int]) -> Object:
        """The object a row of actual values shows in the present mode."""
        if where == cia402.VELOCITY_ACTUAL.where and self.mode == 2:
            return cia402.VL_VELOCITY_ACTUAL
        return next(obj for obj in cia402.WATCHED if obj.where == where)

    def _show_velocity_row(self) -> None:
        """Velocity mode's own speed (0x6044, rpm) in velocity mode, 0x606C otherwise.

        Shown in the units the target is set in, from the object a drive in
        that mode keeps: one may not have 0x606C at all.
        """
        obj = self._shown_in(cia402.VELOCITY_ACTUAL.where)
        name, value, unit = self._velocity_row
        name.setText(f"{obj.name}:")
        unit.setText(self._unit(obj))
        for widget in (name, value, unit):
            widget.setToolTip(about(obj, self._unit(obj)))
        value.setText("--" if self._has(obj) else "not in the EDS")
        poller = getattr(self, "poller", None)  # not yet, while the pane is being built
        if poller is not None and poller.running:
            poller.set_objects(o.where for o in cia402.watched(self.mode))

    def _show_demand_row(self) -> None:
        """The demand of the mode the drive is in, or that it has none."""
        obj = cia402.DEMAND_FOR.get(self.mode)
        name, value, unit = self._demand_row
        if obj is None:
            name.setText("Demand:")
            value.setText("--")
            unit.setText("")
            tip = NO_DEMAND_TIP.format(mode=cia402.mode_name(self.mode))
        else:
            name.setText(f"{obj.name}:")
            value.setText("--" if self._has(obj) else "not in the EDS")
            unit.setText(self._unit(obj))
            tip = f"{about(obj, self._unit(obj))}\n{DEMAND_TIP}"
        for widget in (name, value, unit):
            widget.setToolTip(tip)
        poller = getattr(self, "poller", None)  # not yet, while the pane is being built
        if poller is not None and poller.running:
            poller.set_objects(o.where for o in cia402.watched(self.mode))

    # --- why it faulted ----------------------------------------------------------------
    def _ask_why(self) -> None:
        """Read 0x603F once as the drive gets to Fault, and say what it says.

        Once per fault rather than every poll: the code does not change while
        the fault lasts, and it is cleared from the screen when the fault is.
        """
        if not cia402.is_faulted(self.statusword):
            self._asked_why = False
            self.error_code.hide()
            return
        if self._asked_why or not self._has(cia402.ERROR_CODE):
            return
        drive = self._quietly()
        if drive is None:
            return
        self._asked_why = True

        def done(value, error) -> None:
            if self._went_wrong(cia402.ERROR_CODE, error):
                return
            if cia402.is_faulted(self.statusword):  # not reset while it was asked
                self.error_code.setText(error_text(int(value)))
                self.error_code.show()

        self.app.run_in_background(lambda: drive.read(cia402.ERROR_CODE), done)

    def _on_rate(self, requested: float, achieved: float) -> None:
        # Polling starting or stopping changes what the banner is entitled to
        # say, not only what the rate label says.
        self._show_banner()
        self.rate.setText(rate_text(requested, achieved, self.poller.running, self.poller.paused))

    # --- saying what it is ------------------------------------------------------------------
    def _show_state(self) -> None:
        if not self._read_something:
            self.state.setText("Not read yet")
            self.flags.setText("Tick Poll, or press one of the commands below.")
            self.raw.setText("")
            self.maker_status.setText(MAKER_NOT_READ)
            for button in (self.enable, self.disable, self.quick_stop, self.reset):
                button.setEnabled(True)
            return
        state = cia402.state_of(self.statusword)
        self.state.setText(state)
        flags = cia402.flags_of(self.statusword)
        self.flags.setText(", ".join(flags) if flags else "no flags set")
        self.raw.setText(f"Statusword 0x{self.statusword:04X}")
        maker = cia402.bits_set(self.statusword)
        self.maker_status.setText(maker_bits(maker))
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
            self.target_note.setText(f"{obj.name} (0x{obj.index:04X}), in {self._unit(obj)}.")
        else:
            self.target_note.setText(why)
        acts_at_once = allowed and self.mode != 1
        self.go_note.setText(GO_ELSEWHERE if acts_at_once else "")

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

        held = self._maker_bits() | cia402.operation_bits(self.mode)

        def job():
            for step in steps:
                drive.write(cia402.CONTROLWORD, step.controlword | held)
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
        self._read_back(cia402.STATUSWORD)

    def _set_mode(self) -> None:
        """Write 0x6060, then read 0x6061 to see whether the drive took it.

        The mode on screen comes from 0x6061, which only polling was
        reading -- so with polling off, setting the mode changed nothing
        visible and the target box went on offering whatever the last mode
        allowed. A drive is also free to refuse a mode or to take a moment
        over it, and the mode display is the only thing that knows.
        """
        self._write(
            cia402.MODE,
            self.mode_box.currentData(),
            then=lambda: self._read_back(cia402.MODE_DISPLAY),
        )

    def _write_target(self) -> None:
        obj = cia402.TARGET_FOR.get(self.mode)
        if obj is None:
            self.app.warn(cia402.can_set_target(self.mode)[1])
            return
        word = cia402.word_after_target(self.mode, self.statusword)
        if word is None:
            self._write(obj, self.target.value())
            return
        # Halt off, and the ramp on in velocity mode: see word_after_target.
        word |= self._maker_bits()

        def release() -> None:
            self._write(cia402.CONTROLWORD, word, then=self._read_statusword)

        self._write(obj, self.target.value(), then=release)

    def _write(self, obj: Object, value: int, then=None) -> None:
        """One write, and whatever has to be read back afterwards.

        ``then`` runs on the GUI thread once the drive has taken the
        write, and not at all if it refused.
        """
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
            if then is not None:
                then()

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

    def stop_all(self) -> None:
        """Stop all: the demand taken away and polling unticked, for good."""
        self.stop_demand(why=STOPPING_ALL)
        self.poll.setChecked(False)

    def set_paused(self, on: bool) -> None:
        """pycangui's own traffic paused -- a flash, most likely -- or going again.

        Polling waits, and with nothing reading the drive a demand running is
        a motor turning with nobody watching it, so the drive is put in the
        same safe state as when the pane closes. Not given back when the
        pause lifts: setting a drive moving again is asked for, never assumed.
        """
        self.poller.set_paused(on)
        if on:
            self.stop_demand(why=PAUSING)

    def stop_demand(self, background: bool = True, why: str = CLOSING) -> None:
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

        held = self._maker_bits()

        def job():
            for obj, value in writes:
                drive.write(obj, value | held if obj.where == cia402.CONTROLWORD.where else value)

        def done(_result, error) -> None:
            if error:
                self.app.error(COULD_NOT_STOP.format(node=node, why=error))
                return
            self.app.log(STOPPED.format(node=node, why=why))
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
    if getattr(app, "api_version", 1) < 2:
        return  # a pycangui from before pausing and Stop all

    def paused(on: bool) -> None:
        if (view := app.panes.view(f"{app.plugin}:main")) is not None:
            view.set_paused(on)

    def stop_all() -> None:
        if (view := app.panes.view(f"{app.plugin}:main")) is not None:
            view.stop_all()

    app.on_pause(paused)
    app.on_stop_all(stop_all)
