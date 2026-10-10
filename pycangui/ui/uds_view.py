# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""UDS pane: the ECU and how to reach it, session and security, ECU control,
then tabs for DIDs, routines and raw requests, DTCs, and transfers. Results
go to the pane's own log, shared by every tab."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Slot
from PySide6.QtGui import QAction, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from pycangui.core.components import COMPONENTS
from pycangui.core.context import Context
from pycangui.uds import (
    CAN_DL,
    FUNCTIONAL_SERVICES,
    NO_ID,
    TIMING_AT_LEAST,
    TIMING_ECU,
    TIMING_FORCED,
    UdsConfig,
    fixed_addressing,
    images,
)
from pycangui.uds.dtc import (
    DEFAULT_STANDARD,
    DTC,
    EXTENDED,
    GROUP,
    MEMORY,
    RECORDS,
    REPORTS,
    SEVERITY,
    SNAPSHOT,
    STANDARDS,
    STATUS,
    STATUS_BITS,
)
from pycangui.uds.manager import (
    CHECK_MEMORY,
    COMM_CONTROLS,
    COMM_MESSAGES,
    ERASE_MEMORY,
    FILE_MODES,
    FILE_MODES_SENDING,
    LINK_BITRATES,
    RESETS,
    UdsManager,
    parse_bytes,
)
from pycangui.uds.standard import MAX_SECURITY_LEVEL, security_pair, seed_subfunction
from pycangui.ui import folders, fonts, seedkey_view
from pycangui.ui.confirm import Confirmations
from pycangui.ui.field_widgets import PENDING
from pycangui.ui.persist import remember

#: Operations that change what is on the ECU, and so are worth a question
#: before the first one of a session.
DESTRUCTIVE = ("download", 1, 2, 3, 6)


ADDRESS_TIP = (
    "The identifier the tester sends on, and the one the ECU answers\n"
    "with. ISO 15765-4 says 7E0 and 7E8 for the first ECU; a maker\n"
    "may use others. Empty means this bus has none: nothing is sent\n"
    "and nothing in the trace is called UDS."
)
FUNCTIONAL_TIP = (
    "The address every ECU listens to, for a request meant for all of\n"
    "them. 7DF by the standard. Empty if this bus does not use one."
)
#: What the identifier boxes say once they are worked out rather than
#: typed. A tooltip telling somebody to type into a box they cannot type
#: into is worse than none, and this is the place to say which part of
#: the identifier is which.
FIXED_TX_TIP = (
    "Worked out from the addresses on the left: 18 DA <ecu> <tester>.\n"
    "Priority 6, then ISO 15765-2's physical PDU format, then who it is\n"
    "for and who it is from. Change the ECU or tester address to change it."
)
FIXED_RX_TIP = (
    "Worked out: 18 DA <tester> <ecu> -- the same pair the other way\n"
    "round, because the ECU is the one sending. Nothing to fill in."
)
FIXED_FUNC_TIP = (
    "Worked out: 18 DB <target> <tester>. DB is the functional PDU\n"
    "format, and the target is Func TA -- 33 for OBD, or whatever a\n"
    "manufacturer's own diagnostics use."
)
WORKED_OUT_TIP = (
    "The identifiers the addresses work out to: request / response /\n"
    "functional.\n"
    "Request is 18 DA <ecu> <tester>: priority 6, ISO 15765-2's physical\n"
    "PDU format, then who it is for and who it is from.\n"
    "Response is the same pair the other way round.\n"
    "Functional is 18 DB <target> <tester>, the target being Func TA.\n"
    "Change an address to change them; they can be selected and copied."
)
ADDRESSING_TIP = (
    "How the identifiers are arrived at. Identifiers: type them, which is\n"
    "what an 11-bit bus wants. J1939: give the ECU's 8-bit\n"
    "address and your own, and ISO 15765-2 normal fixed addressing works\n"
    "the identifiers out -- 18DA<ecu><tester> for a request, the two\n"
    "addresses the other way round for the answer."
)
ECU_ADDRESS_TIP = "The controller's 8-bit address on the bus, as J1939 names it."
TESTER_ADDRESS_TIP = (
    "The address pycangui sends from. F9 is the usual one for a service\n"
    "tool; if the J1939 pane has claimed an address, that one is used so\n"
    "the two panes are not two different testers on one bus."
)
FUNCTIONAL_TARGET_TIP = (
    "Who a functional request is addressed to. 33 is the OBD functional\n"
    "address; a manufacturer's own diagnostics may use another."
)
FUNCTIONAL_SERVICES_TIP = (
    "Which services go to every ECU at once, on the functional address\n"
    "beside this, rather than to the one ECU above. Each can be ticked on\n"
    "its own: the usual mixture is tester present, CommunicationControl and\n"
    "DTC setting to all of them and the rest to the ECU being worked on --\n"
    "and a baud rate change that only one ECU makes breaks the bus.\n"
    "\n"
    "Sent as one frame, with the positive answer suppressed where the service\n"
    "allows, so what comes back is which ECU objected. Security, DIDs, DTC\n"
    "reports, routines and transfers always go to the one ECU."
)
OPEN_TIP = (
    "Open an ISO-TP session with the ECU at the addresses above.\n"
    "Nothing else on this pane works until it is open."
)
NO_ADDRESS_TIP = "Fill in the Tx and Rx identifiers first."
TRANSPORT_TIP = (
    "The ISO-TP transport, which is a replaceable component. Add one of\n"
    "your own, or replace this one, with a file in the folder Tools > Open\n"
    "custom components folder opens."
)
LEVEL_TIP = (
    "Which security level to unlock. SecurityAccess sends a pair of\n"
    "sub-functions for each: an odd one asking for the seed and the even\n"
    "one after it carrying the key, so level 2 is 03 and 04. The pair is\n"
    "shown beside the number, and both go in the log when it is used.\n"
    "\n"
    "An ECU document that quotes a sub-function rather than a level is\n"
    "naming the request half: 11 and 12 is level 9."
)
TIMING_TIP = (
    "How long a request waits for its answer.\n"
    "ECU's: the P2 and P2* the ECU gives when a session starts, which is\n"
    "the one to test an ECU with -- an answer later than it promised is a\n"
    "timeout. Until it gives them, the two boxes to the right.\n"
    "At least: the longer of the ECU's and the boxes. For a bootloader\n"
    "that takes longer than it says, finishing a flash write.\n"
    "Forced: the boxes, whatever the ECU says.\n"
    "Applies from the next request, on a session already open too."
)
P2_TIP = (
    "P2: how long to wait for an answer. P2*: how long to wait after the\n"
    "ECU says it is still working (0x78, response pending). How they are\n"
    "used depends on Timing to the left."
)
PADDING_TIP = (
    "The byte every frame is padded out to 8 bytes with. Some ECUs require\n"
    "padding and ignore anything shorter; others do not mind either way.\n"
    "Leave it empty to send frames at the length they are. Taken up when a\n"
    "session is opened, since it belongs to the transport rather than to\n"
    "one request."
)

SEED_KEY_TIP = (
    "The standard seed and key DLL, which is how an ECU's unlock algorithm\n"
    "ships. Used when hooks/uds.py::security_key returns None, so a hook\n"
    "always wins where there is one.\n"
    "\n"
    "The same DLL serves XCP unlocking: one ECU ships one algorithm, and\n"
    "naming the file twice would be pycangui's filing system showing."
)


def _id_text(value: int) -> str:
    """An address as it is typed, and empty for one that is not set."""
    return "" if value == NO_ID else f"{value:X}"


def _hex_edit(text: str, digits: int = 8) -> QLineEdit:
    """A box for a number in hex, wide enough for this many digits."""
    e = QLineEdit(text)
    e.setFont(fonts.mono())
    e.setFixedWidth(fonts.width_for(e, "F" * digits))
    return e


def _picker(known: dict[int, str], digits: int, describe=None) -> QComboBox:
    """A dropdown of the numbers somebody has a name for, that can still be typed in.

    Editable on purpose. A shortlist that will not let you send anything else
    would be worse than no list at all: most of the identifiers and all of the
    interesting routines on a real ECU are manufacturer specific and will
    never be on it.
    """
    combo = QComboBox()
    combo.setEditable(True)
    combo.setInsertPolicy(QComboBox.NoInsert)
    combo.lineEdit().setFont(fonts.mono())
    for number, name in sorted(known.items()):
        combo.addItem(f"{number:0{digits}X}  {name}", number)
        if describe and (text := describe(number)):
            combo.setItemData(combo.count() - 1, text, Qt.ToolTipRole)
    return combo


def _narrow(combo: QComboBox, characters: int) -> QComboBox:
    """Size a combo box to about this many characters, not to its longest entry.

    The list still opens as wide as it needs to. What this stops is one long
    entry -- a DTC report's full name -- setting the width of the whole pane.
    """
    combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
    combo.setMinimumContentsLength(characters)
    # The open list is otherwise only as wide as the box, and cuts each
    # entry off -- the name after a DID, the end of a report's title.
    view = combo.view()
    view.setMinimumWidth(view.sizeHintForColumn(0) + view.verticalScrollBar().sizeHint().width())
    return combo


def _row(*items) -> QHBoxLayout:
    """A row packed to the left: labels as text, widgets as they are, ints as space."""
    row = QHBoxLayout()
    for item in items:
        if isinstance(item, str):
            row.addWidget(QLabel(item))
        elif isinstance(item, int):
            row.addSpacing(item)
        else:
            row.addWidget(item)
    row.addStretch()
    return row


def _picked(combo: QComboBox) -> int:
    """The number out of a picker, whether it was chosen or typed.

    The text of a chosen entry is "F190  VIN", so the number is the first
    word of it; a typed one is only the number.
    """
    text = combo.currentText().strip().split()
    return int(text[0], 16) if text else 0


class _FittedScroll(QScrollArea):
    """A scroll area that asks for the whole height of what it holds.

    A QScrollArea asks for no more than a couple of dozen lines, whatever it
    holds, so it scrolls when there is room to show everything. This one
    asks for all of it, grows no further, and still scrolls once the pane is
    made smaller than that.
    """

    def __init__(self) -> None:
        super().__init__()
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)

    def sizeHint(self) -> QSize:
        inner = self.widget()
        if inner is None:
            return super().sizeHint()
        wanted = inner.sizeHint()
        return QSize(wanted.width(), wanted.height() + 2 * self.frameWidth())


class UdsView(QWidget):
    def __init__(
        self,
        manager: UdsManager,
        ctx: Context,
        confirm: Confirmations | None = None,
        j1939=None,
    ) -> None:
        super().__init__()
        self.manager = manager
        self.ctx = ctx
        self.confirm = confirm or Confirmations()
        self._image: images.Image | None = None
        cfg = UdsConfig.from_dict(ctx.settings.get("uds.config", {}))
        # Made here, before anything else: the boxes below save the whole
        # configuration as they are filled in, and these are part of it.
        # Placed in the session row further down.
        self.timing = QComboBox()
        self.timing.setToolTip(TIMING_TIP)
        for label, value in (
            ("ECU's", TIMING_ECU),
            ("At least", TIMING_AT_LEAST),
            ("Forced", TIMING_FORCED),
        ):
            self.timing.addItem(label, value)
        self.timing.setCurrentIndex(max(self.timing.findData(cfg.timing), 0))
        self.p2 = QSpinBox()
        self.p2.setRange(1, 60_000)
        self.p2.setSuffix(" ms")
        self.p2.setValue(round(cfg.p2_timeout_s * 1000))
        self.p2.setToolTip(P2_TIP)
        self.p2_star = QSpinBox()
        self.p2_star.setRange(1, 600_000)
        self.p2_star.setSuffix(" ms")
        self.p2_star.setValue(round(cfg.p2_star_timeout_s * 1000))
        self.p2_star.setToolTip(P2_TIP)

        # --- addressing ----------------------------------------------------
        addr = QGroupBox("ECU config")
        # Rows packed left rather than a grid. The grid spread the boxes
        # across the whole width of the pane and put each label a long way
        # from what it labels, and its columns were counted by hand: adding
        # the functional address shifted everything after it, which landed
        # "Pad" on top of "Transport". Two rows rather than one, since one
        # made the pane as wide as all twelve boxes together: which ECU, then
        # how the transport carries the conversation with it.
        g = QVBoxLayout(addr)
        self.tx_id = _hex_edit(_id_text(cfg.tx_id))
        self.tx_id.setToolTip(ADDRESS_TIP)
        self.rx_id = _hex_edit(_id_text(cfg.rx_id))
        self.rx_id.setToolTip(ADDRESS_TIP)
        # The functional address has its own box: what the services ticked
        # under Functional are sent to, and what the trace names UDS func. A
        # bus using 0x7DF for something else can say so by emptying it.
        self.functional_id = _hex_edit(_id_text(cfg.functional_id))
        self.functional_id.setToolTip(FUNCTIONAL_TIP)
        for box in (self.tx_id, self.rx_id, self.functional_id):
            box.setPlaceholderText("none")
            # Two signals, because the two things want different moments.
            # Whether Open can be pressed follows every keystroke, which
            # costs nothing. What is sent and what the trace calls UDS
            # waits until the box is finished with: halfway through typing
            # 7E0 the address is 7, and claiming that id for a moment is
            # worse than waiting.
            box.textChanged.connect(lambda _t: self._addresses_changed())
            box.editingFinished.connect(self._apply_addresses)
        self.addressing = QComboBox()
        self.addressing.setToolTip(ADDRESSING_TIP)
        self.addressing.addItem("Identifiers", False)
        self.addressing.addItem("J1939", True)
        self.addressing.setCurrentIndex(1 if cfg.fixed else 0)
        self.addressing.currentIndexChanged.connect(lambda _i: self._addressing_changed())
        self.addressing.currentIndexChanged.connect(lambda _i: self._apply_addresses())
        self.ecu_address = _hex_edit(f"{cfg.ecu_address:02X}", 2)
        self.ecu_address.setToolTip(ECU_ADDRESS_TIP)
        self.tester_address = _hex_edit(f"{cfg.tester_address:02X}", 2)
        self.tester_address.setToolTip(TESTER_ADDRESS_TIP)
        self.functional_target = _hex_edit(f"{cfg.functional_target:02X}", 2)
        self.functional_target.setToolTip(FUNCTIONAL_TARGET_TIP)
        self.address_labels = {}
        # In J1939 addressing the three identifiers are worked out, not typed:
        # shown as one line to read and copy, in place of three boxes nobody
        # can type in, which with the addresses beside them made this the
        # widest row of the pane.
        self.worked_out = QLabel()
        self.worked_out.setFont(fonts.mono())
        self.worked_out.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.worked_out.setToolTip(WORKED_OUT_TIP)
        for box in (self.ecu_address, self.tester_address, self.functional_target):
            box.textChanged.connect(lambda _t: self._addresses_changed())
            box.editingFinished.connect(self._apply_addresses)
        # A byte rather than a tick. "Pad" answered half the question and
        # left the other half to a constant nobody could see: which byte.
        # Empty is no padding, the same "empty means none" the identifier
        # boxes above use.
        self.padding = _hex_edit("" if cfg.padding is None else f"{cfg.padding:02X}", 2)
        self.padding.setPlaceholderText("none")
        self.padding.setToolTip(PADDING_TIP)
        self.padding.editingFinished.connect(self._save)
        self.transport = QComboBox()
        self.transport.setToolTip(TRANSPORT_TIP)
        for spec in COMPONENTS.specs("isotp"):
            self.transport.addItem(spec.name, spec.name)
            self.transport.setItemData(self.transport.count() - 1, spec.description, Qt.ToolTipRole)
        index = self.transport.findData(manager.component_name)
        if index >= 0:
            self.transport.setCurrentIndex(index)
        self.transport.currentTextChanged.connect(manager.set_component)
        self.can_dl = QComboBox()
        self.can_dl.setToolTip(
            "CAN_DL: how many bytes go in one ISO-TP frame. Eight is all a\n"
            "classic bus can carry; the longer lengths need a channel opened as\n"
            "CAN FD, and are what makes running UDS over FD worth the trouble --\n"
            "at 64 there are eight times fewer flow control rounds.\n"
            "Only 8, 12, 16, 20, 24, 32, 48 and 64 exist: CAN FD has no lengths\n"
            "in between, so a shorter message is padded up to the next one."
        )
        for length in CAN_DL:
            self.can_dl.addItem(str(length), length)
        self.can_dl.setCurrentText(str(cfg.tx_data_length))
        self.can_dl.currentIndexChanged.connect(lambda _i: self._save())
        self.brs = QCheckBox("BRS")
        self.brs.setToolTip(
            "Switch to the faster data rate for the data phase of each FD\n"
            "frame. Without it an FD frame runs end to end at the arbitration\n"
            "bitrate, so the data rate chosen on the toolbar never gets used."
        )
        self.brs.setChecked(cfg.bitrate_switch)
        self.brs.toggled.connect(lambda _on: self._save())
        self.open_btn = QPushButton("Open")
        self.open_btn.setCheckable(True)
        self.open_btn.toggled.connect(self._toggle_open)
        # Which services go to every ECU: a tick each, behind one button beside
        # the functional address they are sent to. A choice per service rather
        # than one switch, because the usual thing is a mixture -- tester
        # present, CommunicationControl and DTC setting to all of them, the
        # rest to the ECU being worked on.
        self.functional = QToolButton()
        self.functional.setPopupMode(QToolButton.InstantPopup)
        self.functional.setToolTip(FUNCTIONAL_SERVICES_TIP)
        menu = QMenu(self.functional)
        self.functional_actions: dict[str, QAction] = {}
        for key, name in FUNCTIONAL_SERVICES.items():
            action = menu.addAction(name)
            action.setCheckable(True)
            action.setChecked(cfg.goes_to_all(key))
            action.toggled.connect(lambda _on: self._functional_changed())
            self.functional_actions[key] = action
        self.functional.setMenu(menu)
        self._show_functional()
        for boxes in (
            (
                ("Addressing", self.addressing),
                ("ECU", self.ecu_address),
                ("Tester", self.tester_address),
                ("Func TA", self.functional_target),
                ("Tx ID", self.tx_id),
                ("Rx ID", self.rx_id),
                ("Func ID", self.functional_id),
                ("IDs", self.worked_out),
                ("", self.functional),
            ),
            (
                ("Pad", self.padding),
                ("Transport", self.transport),
                ("CAN-DL", self.can_dl),
                ("", self.brs),
                ("", self.open_btn),
            ),
        ):
            line = QHBoxLayout()
            for label, widget in boxes:
                if widget is self.open_btn:
                    # At the right-hand edge: it is what the rest of the box is
                    # filled in for, and the one control used every time.
                    line.addStretch()
                if label:
                    named = QLabel(label)
                    self.address_labels[widget] = named
                    line.addWidget(named)
                line.addWidget(widget)
            if widget is not self.open_btn:
                line.addStretch()  # everything to the left, rather than spread out
            g.addLayout(line)
        self._addressing_changed()
        # One tester on the bus, not two. If the J1939 pane has claimed an
        # address, that is this tool's address, and typing a different one
        # here would mean answering to one and sending from the other.
        if j1939 is not None:
            if (claimed := j1939.own_address) is not None:
                self._j1939_claimed(claimed)
            j1939.claimed.connect(self._j1939_claimed)

        # --- session / security ----------------------------------------------
        sess = QGroupBox("Session and security")
        sess_rows = QVBoxLayout(sess)
        h = QHBoxLayout()
        sess_rows.addLayout(h)
        self.session = QComboBox()
        self.session.setToolTip(
            "DiagnosticSessionControl (0x10). Most services are only allowed\n"
            "in some sessions, and an ECU drops back to the default one after a\n"
            "few seconds of quiet unless Tester present is ticked.\n"
            "0x40 to 0x5F belong to the manufacturer and 0x60 to 0x7E to the\n"
            "supplier: name yours in hooks/uds.py::sessions and they appear here."
        )
        for code, name in sorted(manager.sessions().items()):
            self.session.addItem(f"0x{code:02X}  {name.capitalize()}", code)
        change = QPushButton("Change")
        change.setToolTip("Move the ECU into the session on the left")
        change.clicked.connect(lambda: self.manager.change_session(self.session.currentData()))
        h.addWidget(QLabel("Session"))
        h.addWidget(self.session)
        h.addWidget(change)
        h.addSpacing(12)
        h.addWidget(QLabel("Level"))
        # A plain level number. Not the sub-function: udsoncan normalises
        # whatever it is given to the odd request and its even answer, so
        # an unpaired combination cannot be sent anyway, and speaking in
        # sub-functions only moved the arithmetic onto the user. The pair
        # is the suffix, so the one number that can be typed is the level
        # and the two hex bytes are plainly derived from it.
        self.level = QSpinBox()
        self.level.setRange(1, MAX_SECURITY_LEVEL)
        self.level.setToolTip(LEVEL_TIP)
        h.addWidget(self.level)
        #: What the level about to be sent actually is on the wire, beside
        #: the number rather than inside it: the box holds one thing, and
        #: these two bytes are what an ECU document is written in.
        self.level_pair = QLabel("")
        self.level_pair.setFont(fonts.mono())
        self.level_pair.setToolTip(LEVEL_TIP)
        self.level.valueChanged.connect(self._say_pair)
        self._say_pair(self.level.value())
        # After the pair is connected, so a restored level shows its bytes.
        remember(ctx, "uds.security.level", self.level)
        h.addWidget(self.level_pair)
        unlock = QPushButton("Unlock")
        unlock.setToolTip(
            "SecurityAccess (0x27): ask for a seed and answer it with a key.\n"
            "There is no standard algorithm. The key comes from\n"
            "hooks/uds.py::security_key, and where that returns None, from\n"
            "the seed and key DLL beside this button."
        )
        unlock.clicked.connect(lambda: self.manager.unlock(self.level.value()))
        h.addWidget(unlock)
        seed_key = QPushButton("Seed and key DLL...")
        seed_key.setToolTip(SEED_KEY_TIP)
        seed_key.clicked.connect(lambda: seedkey_view.ask(self.ctx, self))
        self.tp = QCheckBox("Tester present")
        self.tp.setToolTip(
            "Send TesterPresent (0x3E) every couple of seconds -- to every ECU,\n"
            "as 3E 80 with no answer, when Tester present is ticked under\n"
            "Functional. Without it an ECU drops back to the default session,\n"
            "and any unlock with it, after a few seconds of quiet."
        )
        self.tp.toggled.connect(self.manager.set_tester_present)
        h.addStretch()
        # The second row is keeping the session and how long to wait in it:
        # tester present, and whose timing a request waits for. Beside the
        # session, because that is where the ECU gives its own timing and the
        # log line says what it gave.
        self.timing.currentIndexChanged.connect(lambda _i: self._timing_changed())
        self.p2.valueChanged.connect(lambda _v: self._timing_changed())
        self.p2_star.valueChanged.connect(lambda _v: self._timing_changed())
        sess_rows.addLayout(
            # The DLL is chosen once, so it is down here with what is set and
            # left: beside Unlock it made this the widest row of the pane.
            _row(
                self.tp, 12, "Timing", self.timing, "P2", self.p2, "P2*", self.p2_star, 12, seed_key
            )
        )
        self.manager.set_timing(cfg.timing, cfg.p2_timeout_s, cfg.p2_star_timeout_s)

        # --- ECU control -------------------------------------------------------------
        # Its own box, above the tabs: none of these is part of getting into a
        # session, and each changes how the ECU behaves on the bus -- restart
        # it, quieten it, move it to another bitrate. Kept in view rather than
        # in a tab because they are what somebody reaches for in a hurry.
        # A frame for each service, named as ISO 14229 names it and with its
        # number: that is what is looked for in a trace and in a specification,
        # and one frame called ECU control round all three, inside a tab called
        # ECU control, said the same thing twice and named none of them.
        self.reset_type = QComboBox()
        for code, name in RESETS.items():
            self.reset_type.addItem(name.capitalize(), code)
        reset = QPushButton("Reset")
        reset.setToolTip(
            "ECUReset (0x11). The ECU restarts, so the session and any\n"
            "security unlock are lost with it."
        )
        reset.clicked.connect(self._reset)
        reset_box = QGroupBox("ECUReset (0x11)")
        QVBoxLayout(reset_box).addLayout(_row(self.reset_type, reset))

        self.comm_control = QComboBox()
        for code, name in COMM_CONTROLS.items():
            self.comm_control.addItem(name[0].upper() + name[1:], code)
        self.comm_control.setToolTip(
            "CommunicationControl (0x28): whether the ECU sends and listens.\n"
            "Disabling Tx of normal messages is how a flash is usually made\n"
            "quiet for the rest of the bus. The ECU puts it back itself when\n"
            "the session ends, or when asked to enable Rx and Tx."
        )
        self.comm_messages = QComboBox()
        for code, name in COMM_MESSAGES.items():
            self.comm_messages.addItem(name.capitalize(), code)
        self.comm_messages.setToolTip("Which messages it applies to")
        comm = QPushButton("Send")
        comm.setToolTip("Send CommunicationControl with the choices on the left")
        comm.clicked.connect(self._communication_control)
        comm_box = QGroupBox("CommunicationControl (0x28)")
        QVBoxLayout(comm_box).addLayout(_row(self.comm_control, self.comm_messages, comm))

        self.link_bitrate = QComboBox()
        for bitrate in LINK_BITRATES:
            self.link_bitrate.addItem(f"{bitrate // 1000} kbit/s", bitrate)
        self.link_bitrate.setToolTip(
            "LinkControl (0x87): move the ECUs to another bitrate -- every ECU\n"
            "when Baud rate change is ticked under Functional, which is the\n"
            "usual way, since one left behind breaks the bus. Each is asked\n"
            "whether it can first, then told to, and this channel follows: it is\n"
            "reopened at the new rate, with the session and tester present.\n"
            "There is no request to put it back. The new rate lasts for the\n"
            "session it was set in, which is what the button that appears\n"
            "beside Change uses to take everything back."
        )
        check_rate = QPushButton("Check")
        check_rate.setToolTip(
            "Ask whether the ECU can move to the bitrate on the left, and stop\n"
            "there: the first half of a change, with nothing changed. Every ECU\n"
            "is asked when Baud rate change is ticked under Functional."
        )
        check_rate.clicked.connect(
            lambda: self.manager.check_bitrate(self.link_bitrate.currentData())
        )
        change_rate = QPushButton("Change")
        change_rate.setToolTip("Ask the ECU to move to the bitrate on the left")
        change_rate.clicked.connect(self._change_bitrate)
        # Only while the channel is away from its own rate: there is no
        # request to undo LinkControl, but ending the session does it.
        self.rate_back = QPushButton()
        self.rate_back.setToolTip(
            "Return to the default session, which takes the ECUs back to their\n"
            "own bitrate, and reopen this channel at the rate it had before."
        )
        self.rate_back.clicked.connect(self._back_to_own_rate)
        self.rate_back.hide()
        self._own_rate = 0  # the channel's own rate while it is away from it
        link_box = QGroupBox("LinkControl (0x87)")
        QVBoxLayout(link_box).addLayout(
            _row(self.link_bitrate, check_rate, change_rate, self.rate_back)
        )

        # --- data ----------------------------------------------------------------
        # A box each for DIDs and routines, rather than one grid. In a grid
        # the two rows shared columns, so the identifier list was as narrow as
        # the routine buttons beside it allowed, and "F190  VIN" with its
        # description could not be read.
        # The service bytes beside the names, as on the ECU control tab: read
        # and write for a DID, the one RoutineControl for a routine.
        did_box = QGroupBox("DID (0x22 read, 0x2E write)")
        routine_box = QGroupBox("Routine (0x31)")
        self.did = _picker(manager.did_choices(), 4, manager.did_description)
        self.did.setToolTip(
            "The identifier to read or write. The list is what ISO 14229-1\n"
            "names plus your own DID_NAMES; anything else can be typed.\n"
            "Hover an entry for what it holds."
        )
        self.did.setCurrentText("F190")
        self.did_value = QLineEdit()
        self.did_value.setFont(fonts.mono())
        read_did = QPushButton("Read")
        read_did.setToolTip("ReadDataByIdentifier (0x22)")
        read_did.clicked.connect(lambda: self.manager.read_did(_picked(self.did)))
        write_did = QPushButton("Write")
        write_did.setToolTip(
            "WriteDataByIdentifier (0x2E). What you type is turned into bytes\n"
            "by hooks/uds.py::did_encode -- hex by default."
        )
        write_did.clicked.connect(
            lambda: self.manager.write_did(_picked(self.did), self.did_value.text())
        )
        # The list has the most room: it is what has to be read.
        did_row = QHBoxLayout(did_box)
        did_row.addWidget(self.did, 3)
        did_row.addWidget(read_did)
        did_row.addWidget(self.did_value, 2)
        did_row.addWidget(write_did)

        self.routine = _picker(manager.routine_choices(), 4, manager.routine_description)
        self.routine.setToolTip(
            "The routine to run. ISO 14229-1 names four; everything from 0200\n"
            "to DFFF is the manufacturer's, which is where the rest of a flash\n"
            "sequence lives, so type those or add them to ROUTINE_NAMES."
        )
        self.routine.setCurrentText("0203")
        self.routine_data = QLineEdit()
        self.routine_data.setFont(fonts.mono())
        self.routine_data.setPlaceholderText("option bytes (hex)")
        rbox = QHBoxLayout()
        for label, control in (("Start", 1), ("Stop", 2), ("Result", 3)):
            b = QPushButton(label)
            b.setToolTip(
                f"RoutineControl (0x31), sub-function {control}: {label.lower()} the routine\n"
                "whose identifier is on the left."
            )
            b.clicked.connect(
                lambda _=False, c=control: self.manager.routine(
                    c, _picked(self.routine), parse_bytes(self.routine_data.text())
                )
            )
            rbox.addWidget(b)
        routine_row = QHBoxLayout(routine_box)
        routine_row.addWidget(self.routine, 3)
        routine_row.addLayout(rbox)
        routine_row.addWidget(self.routine_data, 2)
        for picker in (self.did, self.routine):
            _narrow(picker, 22)

        # Its own box, because it is not one of these. DID and Routine are
        # services with their parameters laid out for them; this is the
        # escape hatch for everything the pane does not offer, and in the
        # same frame it reads as a third kind of routine control.
        raw_box = QGroupBox("Raw request")
        raw_row = QHBoxLayout(raw_box)
        self.raw = QLineEdit("22 F1 90")
        self.raw.setFont(fonts.mono())
        self.raw.returnPressed.connect(self._send_raw)
        raw_btn = QPushButton("Send raw")
        raw_btn.setToolTip(
            "Send these bytes as a request, with no help: the first byte is the\n"
            "service id and the rest is whatever that service expects."
        )
        raw_btn.clicked.connect(self._send_raw)
        raw_row.addWidget(QLabel("Bytes"))
        raw_row.addWidget(self.raw, 1)
        raw_row.addWidget(raw_btn)

        # --- DTCs ------------------------------------------------------------------
        # ReadDTCInformation is twenty-odd reports wearing one service number,
        # and each takes a different set of parameters. Choosing the report
        # first and letting it decide which boxes are live is the only way to
        # offer all of them without offering nonsense.
        dtc_box = QWidget()
        d = QGridLayout(dtc_box)
        d.setContentsMargins(0, 0, 0, 0)

        self.report = QComboBox()
        self.report.setToolTip(
            "Which ReadDTCInformation (0x19) report to ask for.\n"
            "The boxes below light up according to what it takes; an ECU\n"
            "answers the wrong ones with NRC 0x13 and no explanation."
        )
        for report in REPORTS:
            self.report.addItem(report.label, report.subfunction)
            if report.note:
                self.report.setItemData(self.report.count() - 1, report.note, Qt.ToolTipRole)
        self.report.currentIndexChanged.connect(self._on_report)
        _narrow(self.report, 30)  # the report names are long
        read_dtc = QPushButton("Read")
        read_dtc.setToolTip("Send the report chosen on the left")
        read_dtc.clicked.connect(self._read_dtcs)
        read_all = QPushButton("Read all")
        read_all.setToolTip(
            "Everything the ECU holds about its faults, as one report: how many\n"
            "and which DTCs match the status mask, each one's extended data and\n"
            "severity, every snapshot, the first and most recent failed and\n"
            "confirmed DTCs, the fault detection counters and the permanent\n"
            "ones. A report the ECU does not offer is one line in the log.\n"
            "Extended data records are split and named by\n"
            "hooks/uds.py::extended_data_record; a snapshot's DIDs are named and\n"
            "decoded like any other DID."
        )
        read_all.clicked.connect(self._read_all_dtcs)
        self.read_all_supported = QCheckBox("Supported DTCs too")
        self.read_all_supported.setToolTip(
            "Add every DTC the ECU supports, with its status (report 0x0A). It\n"
            "can run to hundreds of lines, so it is left out unless asked for."
        )
        top = QHBoxLayout()
        top.addWidget(QLabel("Report"))
        top.addWidget(self.report, 1)
        top.addWidget(read_dtc)
        top.addSpacing(12)
        top.addWidget(read_all)
        top.addWidget(self.read_all_supported)
        d.addLayout(top, 0, 0, 1, 7)

        self.dtc_mask = _hex_edit("FF", 2)
        self.dtc_mask.setToolTip(
            "Which faults to ask about. A bit set means "
            + "include it:\n  "
            + "\n  ".join(STATUS_BITS)
            + "\nFF is everything; 08 is only the confirmed ones."
        )
        self.severity = _hex_edit("FF", 2)
        self.severity.setToolTip(
            "Severity bits (ISO 14229-1): 0x20 maintenance only,\n"
            "0x40 check at next halt, 0x80 check immediately."
        )
        self.dtc_number = _hex_edit("FFFFFF", 6)
        self.dtc_number.setToolTip(
            "The three-byte DTC the report is about, in hex.\n"
            "FFFFFF is how most ECUs are asked for all of them -- a convention\n"
            "rather than something ISO 14229-1 defines for these reports, so an\n"
            "ECU is within its rights to want one particular fault instead."
        )
        self.record = _hex_edit("FF", 2)
        self.record.setToolTip("Which record to read. FF asks for all of them.")
        self.memory = _hex_edit("00", 2)
        self.memory.setToolTip("Which user-defined DTC memory to read from")
        self.functional_group = _hex_edit("33", 2)
        self.functional_group.setToolTip(
            "WWH-OBD functional group: 33 is emissions, FE all groups, FF the VOBD system"
        )
        # A label to the left of its box is only unambiguous while the columns
        # stay narrow; six pairs across a stretched row put every label nearer
        # its neighbour's box than its own. Above it, in the same column,
        # cannot come apart however the pane is resized.
        #
        # ``field`` is the parameter the box fills in, or None for the record
        # box, which serves two of them and takes its name from the report.
        self._dtc_fields: list[tuple[str | None, QLabel, QLineEdit]] = []
        for column, (field, text, widget) in enumerate(
            (
                (STATUS, "Status mask", self.dtc_mask),
                (SEVERITY, "Severity mask", self.severity),
                (DTC, "DTC", self.dtc_number),
                (None, "Record", self.record),
                (MEMORY, "Memory", self.memory),
                (GROUP, "Group", self.functional_group),
            )
        ):
            label = QLabel(text)
            label.setBuddy(widget)
            d.addWidget(label, 1, column, alignment=Qt.AlignBottom | Qt.AlignLeft)
            d.addWidget(widget, 2, column, alignment=Qt.AlignLeft)
            self._dtc_fields.append((field, label, widget))
        self.record_label = self._dtc_fields[3][1]
        # The spare column takes the slack, so the six stay together on the
        # left rather than drifting apart as the pane widens.
        d.setColumnStretch(6, 1)

        self.dtc_setting = QCheckBox("DTC setting on")
        self.dtc_setting.setToolTip(
            "ControlDTCSetting (0x85). Untick to stop the ECU recording new\n"
            "faults while you work on it, so that pulling a connector does not\n"
            "leave one behind. The ECU turns it back on itself when the\n"
            "session ends, which is worth remembering when it looks as though\n"
            "the setting did not take. The tick says what was last asked for,\n"
            "not what the ECU has done about it."
        )
        self.dtc_setting.setChecked(True)
        self.dtc_setting.toggled.connect(self.manager.set_dtc_setting)
        self.clear_group = _hex_edit("FFFFFF", 6)
        self.clear_group.setToolTip(
            "Which faults to erase. FFFFFF is all of them; a group such as\n"
            "FFFF33 is emissions related only."
        )
        clear_dtc = QPushButton("Clear")
        clear_dtc.setToolTip(
            "ClearDiagnosticInformation (0x14). The ECU's stored faults are\n"
            "erased, along with the freeze frames that go with them."
        )
        clear_dtc.clicked.connect(lambda: self.manager.clear_dtcs(self._int(self.clear_group)))
        self.standard = QComboBox()
        self.standard.setToolTip(
            "Which edition of ISO 14229-1 requests are built to. It applies to\n"
            "every service, but it shows up here: the 2020 edition withdrew the\n"
            "mirror memory reports, and they cannot be sent while it is chosen."
        )
        for year in STANDARDS:
            self.standard.addItem(str(year), year)
        self.standard.setCurrentText(str(DEFAULT_STANDARD))
        self.standard.currentTextChanged.connect(lambda text: self.manager.set_standard(int(text)))
        bottom = QHBoxLayout()
        bottom.addWidget(self.dtc_setting)
        bottom.addSpacing(16)
        bottom.addWidget(QLabel("Clear group"))
        bottom.addWidget(self.clear_group)
        bottom.addWidget(clear_dtc)
        bottom.addStretch()
        bottom.addWidget(QLabel("Standard"))
        bottom.addWidget(self.standard)
        d.addLayout(bottom, 3, 0, 1, 7)

        for key, widget in (
            ("uds.session", self.session),
            ("uds.did", self.did),
            ("uds.routine", self.routine),
            ("uds.dtc.report", self.report),
            ("uds.dtc.status", self.dtc_mask),
            ("uds.dtc.standard", self.standard),
            ("uds.dtc.read_all_supported", self.read_all_supported),
        ):
            remember(ctx, key, widget)
        self._on_report()

        # --- transfer -------------------------------------------------------------
        # Its own box because it is the one thing here that runs for minutes
        # rather than milliseconds, and the only one with something to cancel.
        xfer = QWidget()

        self.operation = QComboBox()
        self.operation.setToolTip(
            "What to transfer and which way.\n"
            "Download and Upload address memory directly (0x34 / 0x35).\n"
            "The rest name a file on the ECU's own filesystem (0x38), so they\n"
            "need no address at all."
        )
        self.operation.addItem("Download to ECU (0x34)", "download")
        self.operation.addItem("Upload from ECU (0x35)", "upload")
        for mode, name in FILE_MODES.items():
            self.operation.addItem(f"{name.capitalize()} (0x38)", mode)
        self.operation.currentIndexChanged.connect(self._on_operation)

        self.block = QSpinBox()
        self.block.setRange(0, 4095)
        self.block.setSpecialValueText("from ECU")
        self.block.setToolTip(
            "Data bytes per TransferData. Left at 0 the ECU's own\n"
            "maxNumberOfBlockLength is used, less the two bytes the service id\n"
            "and the block counter take out of it."
        )

        self.width_bits = QComboBox()
        self.width_bits.addItems(("auto", "8", "16", "24", "32"))
        self.width_bits.setToolTip(
            "Bits used to write the address and the size in the request.\n"
            "Auto uses the narrowest that fits; bootloaders that insist on a\n"
            "fixed width answer anything else with NRC 0x13."
        )

        self.dfi = _hex_edit("00", 2)
        self.dfi.setToolTip(
            "dataFormatIdentifier: high nibble compression, low nibble\n"
            "encryption. 00 is plain bytes, which is what most bootloaders\n"
            "want and all of them understand."
        )

        self.local = QLineEdit()
        self.local.setPlaceholderText("Intel HEX, S-record or raw binary")
        self.local.editingFinished.connect(self._reload_image)
        browse = QPushButton("Browse...")
        browse.setToolTip("Choose the file on this computer")
        browse.clicked.connect(self._browse)

        self.address = _hex_edit("", 8)
        self.address.setToolTip(
            "Where the bytes go, in hex. A hex or S-record file carries its\n"
            "own address and fills this in; a raw binary has none, so for one\n"
            "of those it has to be typed."
        )
        self.address.editingFinished.connect(self._reload_image)
        self.byte_count = _hex_edit("", 8)
        self.byte_count.setToolTip("How many bytes to read out of the ECU, in hex")

        self.ecu_path = QLineEdit()
        self.ecu_path.setPlaceholderText("path on the ECU")
        self.ecu_path.setToolTip(
            "The name the file has on the ECU. This is what 0x38 transfers\n"
            "by, in place of an address."
        )

        # A flash sequence is an erase, then the blocks, then something that
        # has the ECU check what it was given. Only the erase is standardised.
        self.erase = QCheckBox("Erase first")
        self.erase.setToolTip(
            f"RoutineControl start {ERASE_MEMORY:04X} over every segment before\n"
            "the first one is written. Flash has to be erased before it can be\n"
            "written, and this is the one routine ISO 14229-1 names for it.\n"
            "All of them first, not each before its own download: two segments\n"
            "can share a flash block, and erasing between them would take the\n"
            "first one back out again."
        )
        self.check = QCheckBox("Check after")
        self.check.setToolTip(
            "Run a routine once each segment has been sent, to have the ECU\n"
            "check what it was given. Unlike the erase this one has no\n"
            "standard behind it -- the number beside it is the one the\n"
            "HIS/AUTOSAR bootloaders settled on, and yours may differ.\n"
            "What it is sent comes from hooks/uds.py::check_options."
        )
        self.check_routine = _hex_edit(f"{CHECK_MEMORY:04X}", 4)
        self.check_routine.setToolTip("Which routine to run afterwards")
        self.erase.toggled.connect(self._on_operation)
        self.check.toggled.connect(self._on_operation)

        self.start = QPushButton("Download")
        self.start.clicked.connect(self._start)
        self.bar = QProgressBar()
        self.bar.setTextVisible(True)
        self.stop = QPushButton("Cancel")
        self.stop.setToolTip(
            "Stop after the block being sent now. The ECU is waiting for a\n"
            "TransferData it has already been promised, so stopping part way\n"
            "through one would leave the connection out of step."
        )
        self.stop.setEnabled(False)
        self.stop.clicked.connect(manager.cancel_transfer)

        _narrow(self.operation, 24)
        # A row each, packed left. The nine-column grid made the pane as wide
        # as its widest row laid across all nine, and a tab is as wide as its
        # widest page.
        x = QVBoxLayout(xfer)
        x.setContentsMargins(0, 0, 0, 0)
        file_row = _row("File", self.local, browse)
        file_row.setStretch(1, 1)  # the path, rather than the space after it
        file_row.takeAt(file_row.count() - 1)
        where_row = _row(
            "Address", self.address, "Size", self.byte_count, 8, "On ECU", self.ecu_path
        )
        where_row.setStretch(6, 1)
        where_row.takeAt(where_row.count() - 1)
        progress_row = _row(self.start, self.bar, self.stop)
        progress_row.setStretch(1, 1)
        progress_row.takeAt(progress_row.count() - 1)
        for row in (
            _row("Operation", self.operation),
            file_row,
            where_row,
            _row("Block", self.block, "Width", self.width_bits, "DFI", self.dfi),
            _row(self.erase, self.check, self.check_routine),
            progress_row,
        ):
            x.addLayout(row)

        for key, widget in (
            ("uds.transfer.block", self.block),
            ("uds.transfer.width", self.width_bits),
            ("uds.transfer.dfi", self.dfi),
            ("uds.transfer.ecu_path", self.ecu_path),
            ("uds.transfer.erase", self.erase),
            ("uds.transfer.check", self.check),
            ("uds.transfer.check_routine", self.check_routine),
        ):
            remember(ctx, key, widget)
        self._on_operation()

        # --- output ---------------------------------------------------------------
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setFont(fonts.mono())
        self.output.setMaximumBlockCount(2000)

        # Scrolled for the same reason as the LSS pane: the controls must not
        # dictate how small the dock can be made.
        # The ECU and the session stay in view: every tab depends
        # on them, and one hidden behind a tab is how a request goes to the
        # wrong ECU or fails for a session nobody could see. The rest is in
        # tabs, which is what kept the pane from being taller than a screen.
        self.tabs = QTabWidget()
        for title, parts in (
            ("DIDs, routines and raw", (did_box, routine_box, raw_box)),
            ("DTCs", (dtc_box,)),
            # Reset, communication and bitrate: done now and then, and three
            # rows of the pane while they were always in view.
            ("ECU control", (reset_box, comm_box, link_box)),
            ("Transfer", (xfer,)),
        ):
            page = QWidget()
            page_layout = QVBoxLayout(page)
            for part in parts:
                page_layout.addWidget(part)
            page_layout.addStretch()
            self.tabs.addTab(page, title)
        remember(ctx, "uds.tab", self.tabs)

        controls = QWidget()
        controls_layout = QVBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        for w in (addr, sess, self.tabs):
            controls_layout.addWidget(w)
        controls_layout.addStretch()
        scroll = _FittedScroll()
        scroll.setWidget(controls)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setMinimumHeight(0)

        # The controls take the height they need and the log takes the rest.
        # The other way round left a gap under the tabs that the log could
        # have used, since the scroll area was the one that stretched.
        self.output.setMinimumHeight(40)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.addWidget(scroll)
        layout.addWidget(self.output, 1)

        manager.result.connect(self._append)
        manager.opened.connect(self._on_opened)
        # Which lengths are allowed is the channel's business, not this pane's,
        # so follow it rather than asking the user to keep the two in step.
        manager.bus.connected.connect(lambda _d: self._on_fd_changed())
        manager.bus.disconnected.connect(self._on_fd_changed)
        self._on_fd_changed()
        manager.progress.connect(self._on_progress)
        manager.transferring.connect(self._on_transferring)
        manager.tester_present_stopped.connect(self._on_tester_present_stopped)
        manager.tester_present_resumed.connect(lambda: self.tp.setChecked(True))
        manager.rate_moved.connect(self._on_rate_moved)

    # --- helpers ----------------------------------------------------------------------
    @staticmethod
    def _int(edit: QLineEdit) -> int:
        """What is typed, as a number, or NO_ID for a box left empty."""
        text = edit.text().strip()
        if not text:
            return NO_ID
        try:
            return int(text, 16)
        except ValueError:
            return NO_ID

    def _addressing_changed(self) -> None:
        """Show the boxes this way of addressing needs, and hide the rest.

        In J1939 addressing the identifiers are worked out rather than
        typed, so they are still shown -- somebody comparing against a
        trace wants to see them -- but as a line to read, not boxes to
        type in: editing one would be disagreeing with the addresses
        beside it.
        """
        fixed = bool(self.addressing.currentData())
        for widget in (self.ecu_address, self.tester_address, self.functional_target):
            widget.setVisible(fixed)
            self.address_labels[widget].setVisible(fixed)
        for widget, typed, worked_out in (
            (self.tx_id, ADDRESS_TIP, FIXED_TX_TIP),
            (self.rx_id, ADDRESS_TIP, FIXED_RX_TIP),
            (self.functional_id, FUNCTIONAL_TIP, FIXED_FUNC_TIP),
        ):
            widget.setReadOnly(fixed)
            widget.setToolTip(worked_out if fixed else typed)
            # Typed in boxes; worked out, they are the one line beside the addresses.
            widget.setVisible(not fixed)
            self.address_labels[widget].setVisible(not fixed)
        self.worked_out.setVisible(fixed)
        self.address_labels[self.worked_out].setVisible(fixed)
        self._apply_addresses()

    @Slot(int)
    def _j1939_claimed(self, address: int) -> None:
        """Follow the address the J1939 pane claimed, while that is possible.

        Not while a session is open, and not over something somebody has
        typed and not yet applied: a claim arriving mid-edit should not
        take the box away from them. 0xFE is "lost it", which is not an
        address to send from.
        """
        if self.manager.is_open or address == 0xFE:
            return
        self.tester_address.setText(f"{address:02X}")
        self._apply_addresses()

    def _fill_in_fixed(self) -> None:
        """Work the identifiers out from the two addresses, and show them."""
        request, response, functional = fixed_addressing(
            self._int(self.ecu_address) & 0xFF,
            self._int(self.tester_address) & 0xFF,
            self._int(self.functional_target) & 0xFF,
        )
        for box, value in (
            (self.tx_id, request),
            (self.rx_id, response),
            (self.functional_id, functional),
        ):
            box.blockSignals(True)  # these are not somebody typing
            box.setText(f"{value:08X}")
            box.blockSignals(False)
        self._show_worked_out()

    def _address_boxes(self):
        """Each address box and the field it sets, in one place."""
        return (
            (self.tx_id, "tx_id"),
            (self.rx_id, "rx_id"),
            (self.functional_id, "functional_id"),
        )

    def _addresses_changed(self) -> None:
        """Open is offered once there is an ECU to open a session with.

        A box holding something not yet applied is tinted, the same amber a
        typed value wears in the CANopen and custom panes: it says "this is
        not what is in use yet" without a message, and Enter or leaving the
        box settles it.
        """
        ready = self._int(self.tx_id) != NO_ID and self._int(self.rx_id) != NO_ID
        self.open_btn.setEnabled(ready or self.manager.is_open)
        self.open_btn.setToolTip(OPEN_TIP if ready else NO_ADDRESS_TIP)
        for box, field in self._address_boxes():
            waiting = self._int(box) != getattr(self.manager.config, field)
            box.setStyleSheet(PENDING if waiting else "")

    def _show_worked_out(self) -> None:
        """Request / response / functional, as the three boxes hold them."""
        self.worked_out.setText(
            " / ".join(box.text() or "none" for box in (self.tx_id, self.rx_id, self.functional_id))
        )

    def _apply_addresses(self) -> None:
        """Hand the addresses to the manager, once a box is finished with.

        They are what a request is sent to, what the trace names UDS and
        what Help > Known CAN ids lists, so clearing one should stop all of
        that now rather than at the next start -- which is what pressing
        Open used to be needed for. Not while a session is open: moving the
        addresses under a running one would be talking somewhere else
        halfway through.
        """
        if self.manager.is_open:
            return
        cfg = self.manager.config
        cfg.fixed = bool(self.addressing.currentData())
        if cfg.fixed:
            cfg.ecu_address = self._int(self.ecu_address) & 0xFF
            cfg.tester_address = self._int(self.tester_address) & 0xFF
            cfg.functional_target = self._int(self.functional_target) & 0xFF
            self._fill_in_fixed()
        for box, field in self._address_boxes():
            setattr(cfg, field, self._int(box))
        self._addresses_changed()  # nothing is waiting now, so no tint
        self._save()

    def _timing_changed(self) -> None:
        """Saved, and used from the next request on, on a session already open too."""
        self._save()
        self.manager.set_timing(
            self.timing.currentData(), self.p2.value() / 1000, self.p2_star.value() / 1000
        )

    def _padding(self) -> int | None:
        """The pad byte, or None for a box left empty."""
        value = self._int(self.padding)
        return None if value == NO_ID else value & 0xFF

    def _save(self) -> None:
        """Write down what the pane is set to, whenever a box is finished with.

        It used to be written only as a session opened, so anything typed
        and not opened was gone at the next start -- and clearing an
        identifier was gone twice over, since the default came back in its
        place and there was no record that anybody had cleared it.
        """
        self.ctx.settings.set("uds.config", self._config().to_dict())

    def _config(self) -> UdsConfig:
        # No 29-bit tick box: whether these are 29-bit identifiers is
        # something the identifiers themselves say, and asking as well left
        # two answers to one question, one of which could be wrong.
        return UdsConfig(
            tx_id=self._int(self.tx_id),
            rx_id=self._int(self.rx_id),
            functional_id=self._int(self.functional_id),
            fixed=bool(self.addressing.currentData()),
            ecu_address=self._int(self.ecu_address) & 0xFF,
            tester_address=self._int(self.tester_address) & 0xFF,
            functional_target=self._int(self.functional_target) & 0xFF,
            padding=self._padding(),
            can_fd=self.manager.bus.fd,
            tx_data_length=self.can_dl.currentData() or 8,
            bitrate_switch=self.brs.isChecked(),
            p2_timeout_s=self.p2.value() / 1000,
            p2_star_timeout_s=self.p2_star.value() / 1000,
            timing=self.timing.currentData(),
            functional=self._functional_chosen(),
        )

    def _functional_chosen(self) -> list[str]:
        return [key for key, action in self.functional_actions.items() if action.isChecked()]

    def _show_functional(self) -> None:
        chosen = len(self._functional_chosen())
        self.functional.setText(f"Functional ({chosen})" if chosen else "Functional")

    def _functional_changed(self) -> None:
        """From the next request on, on a session already open as well."""
        self.manager.config.functional = self._functional_chosen()
        self._show_functional()
        self._save()

    @Slot(bool)
    def _toggle_open(self, on: bool) -> None:
        if on:
            try:
                self._save()
                self.manager.open(self._config())
            except ValueError as exc:
                self._append(f"UDS: bad id: {exc}")
                self.open_btn.setChecked(False)
        else:
            self.manager.close()

    @Slot()
    def _on_fd_changed(self) -> None:
        """Offer the long frame lengths only on a channel that opened as FD."""
        fd = self.manager.bus.fd
        self.can_dl.setEnabled(fd)
        self.brs.setEnabled(fd)
        if not fd:
            self.can_dl.setCurrentText("8")

    def _say_pair(self, level: int) -> None:
        """Show the two sub-functions this level will actually send."""
        self.level_pair.setText(security_pair(seed_subfunction(level)))

    @Slot(bool)
    def _on_opened(self, opened: bool) -> None:
        self.open_btn.blockSignals(True)
        self.open_btn.setChecked(opened)
        self.open_btn.setText("Close" if opened else "Open")
        self.open_btn.blockSignals(False)
        if not opened:
            self.tp.setChecked(False)

    @Slot(str)
    def _on_tester_present_stopped(self, why: str) -> None:
        """Untick the box, so it says what is happening, and say why in both places."""
        self.tp.setChecked(False)
        self._append(why)
        self.ctx.error(why)

    def _read_all_dtcs(self) -> None:
        mask = self._int(self.dtc_mask)
        self.manager.read_all_dtcs(
            0xFF if mask == NO_ID else mask & 0xFF, self.read_all_supported.isChecked()
        )

    def _to_all(self, service: str) -> bool:
        return self.manager.config.goes_to_all(service)

    def _reset(self) -> None:
        """ECUReset -- after asking, when it goes to every ECU on the bus."""
        reset_type = self.reset_type.currentData()
        text = (
            "Reset every ECU on the bus that serves the functional address. Each "
            "restarts, and loses its session and any unlock."
        )
        if not self._to_all("reset") or self.confirm.ask(
            self, "uds.reset.all", "Reset every ECU?", text
        ):
            self.manager.ecu_reset(reset_type)

    def _communication_control(self) -> None:
        """CommunicationControl, after asking: it changes what the ECU puts on the bus."""
        control, messages = self.comm_control.currentData(), self.comm_messages.currentData()
        what = f"{COMM_CONTROLS[control]}, {COMM_MESSAGES[messages]}"
        everyone = self._to_all("comm")
        who = "every ECU on the bus" if everyone else "the ECU"
        text = (
            f"Ask {who} to {what}. Other nodes may stop hearing from them, or they "
            "from the others, until it is enabled again or the session ends."
        )
        key = f"uds.comm_control.{control}" + (".all" if everyone else "")
        if self.confirm.ask(self, key, "Change what the ECUs send?", text):
            self.manager.communication_control(control, messages)

    def _change_bitrate(self) -> None:
        """LinkControl, after asking: the ECUs change rate, and the channel follows."""
        bitrate = self.link_bitrate.currentData()
        kbit = f"{bitrate // 1000} kbit/s"
        everyone = self._to_all("link")
        who = "every ECU on the bus" if everyone else "the ECU"
        text = (
            f"Ask {who} to change to {kbit}. Each is asked whether it can first, and "
            f"nothing changes if any says no. Once they have, this channel is closed "
            f"and opened again at {kbit}, and the session and tester present with it: "
            "the other panes, and a recording, see it disconnect and connect.\n\n"
            f"Anything on the bus not told to change stays at the old rate and sees "
            "only errors from the rest. The new rate lasts until the session ends; "
            "the button beside Change takes everything back."
        )
        if not everyone:
            text += (
                "\n\nOnly the one ECU is being asked: tick Baud rate change under "
                "Functional to move every ECU together."
            )
        key = "uds.link_control" + (".all" if everyone else "")
        if self.confirm.ask(self, key, "Change the bus's bitrate?", text):
            self.manager.change_bitrate(bitrate)

    def _back_to_own_rate(self) -> None:
        own = self._own_rate
        text = (
            f"Return {'every ECU' if self._to_all('link') else 'the ECU'} to the default "
            f"session, which takes them back to their own bitrate, and reopen this "
            f"channel at {own // 1000} kbit/s. Security and anything the session held "
            "go with it."
        )
        if self.confirm.ask(self, "uds.link_control.back", "Back to the bus's own rate?", text):
            self.manager.back_to_own_rate()

    @Slot(int, int)
    def _on_rate_moved(self, now: int, own: int) -> None:
        self._own_rate = own
        self.rate_back.setText(f"Back to {own // 1000} kbit/s")
        self.rate_back.setVisible(bool(own))

    def _send_raw(self) -> None:
        try:
            payload = bytes.fromhex(self.raw.text().replace(",", " ").replace("0x", ""))
        except ValueError as exc:
            self._append(f"Raw: {exc}")
            return
        if payload:
            self.manager.raw(payload)

    # --- transfers --------------------------------------------------------------------
    def _operation(self):
        return self.operation.currentData()

    def _sends_a_local_file(self) -> bool:
        op = self._operation()
        return op == "download" or op in FILE_MODES_SENDING

    def _wants_a_local_file(self) -> bool:
        """Whether this operation puts something on this computer."""
        return self._operation() in ("upload", 4)

    @Slot()
    def _on_operation(self) -> None:
        """Grey out what this operation has no use for, rather than hiding it.

        A box that disappears takes the layout with it, and the point is to
        show that an address is not the way a file transfer is addressed --
        which an empty gap would not say.
        """
        op = self._operation()
        memory = op in ("download", "upload")
        self.local.setEnabled(self._sends_a_local_file() or self._wants_a_local_file())
        self.address.setEnabled(op == "upload" or (op == "download" and self._needs_address()))
        self.byte_count.setEnabled(op == "upload")
        self.ecu_path.setEnabled(not memory)
        self.block.setEnabled(op != 2)
        self.width_bits.setEnabled(memory)
        # Only a download writes memory, so only a download has anything to
        # erase first or to have checked afterwards.
        self.erase.setEnabled(op == "download")
        self.check.setEnabled(op == "download")
        self.check_routine.setEnabled(op == "download" and self.check.isChecked())
        labels = {"download": "Download", "upload": "Upload"}
        self.start.setText(labels.get(op) or FILE_MODES[op].capitalize())
        self.start.setToolTip(
            {
                "download": "RequestDownload (0x34), then a TransferData for every block,\n"
                "then RequestTransferExit. One RequestDownload per segment:\n"
                "gaps in the file are left as gaps.",
                "upload": "RequestUpload (0x35) and read the memory back into the file\n"
                "named above, in whichever format its name asks for.",
            }.get(op)
            or f"RequestFileTransfer (0x38), mode {op}: {FILE_MODES[op]}"
        )

    def _needs_address(self) -> bool:
        """A raw binary is bytes and nothing else, so it has to be told where it goes."""
        return bool(self.local.text()) and images.looks_binary(self.local.text())

    def _browse(self) -> None:
        if self._wants_a_local_file():
            caption, filt = "Save to", images.WRITE_FILTER if self._operation() == "upload" else ""
            path = folders.save_file(
                self, self.ctx, folders.IMAGE, caption, filt or "All files (*)", self.ctx.user_dir
            )
        else:
            filt = images.READ_FILTER if self._operation() == "download" else "All files (*)"
            path = folders.open_file(
                self, self.ctx, folders.IMAGE, "Transfer file", filt, self.ctx.user_dir
            )
        if path:
            self.local.setText(path)
            self._reload_image()

    def _reload_image(self) -> None:
        """Read the chosen file now, so what it contains is known before anything is sent."""
        self._image = None
        if self._operation() != "download" or not self.local.text():
            self._on_operation()
            return
        typed = self.address.text().strip()
        try:
            start = int(typed, 16) if typed else None
        except ValueError:
            start = None
        try:
            self._image = images.read(self.local.text(), start)
        except images.ImageError as exc:
            self._append(f"Transfer: {exc}")
            self._on_operation()
            return
        self.address.setText(f"{self._image.address:X}")
        self.byte_count.setText(f"{self._image.size:X}")
        self._append(f"Transfer: {self._image.summary()}")
        self._on_operation()

    def _width(self) -> int | None:
        text = self.width_bits.currentText()
        return None if text == "auto" else int(text)

    def _start(self) -> None:
        op = self._operation()
        if op in DESTRUCTIVE and not self._agree(op):
            return
        block, dfi = self.block.value(), self._int(self.dfi)
        if op == "download":
            if self._image is None:
                self._reload_image()
            if self._image is None:
                self._append("Download: no file to send")
                return
            self.manager.download(
                self._image,
                block,
                dfi,
                self._width(),
                erase=self.erase.isChecked(),
                check=self._int(self.check_routine) if self.check.isChecked() else 0,
            )
        elif op == "upload":
            if not self.local.text():
                self._append("Upload: nowhere to put it -- choose a file first")
                return
            size = self._int(self.byte_count)
            if size <= 0:
                self._append("Upload: how many bytes? -- a size is needed")
                return
            self.manager.upload(
                self.local.text(), self._int(self.address), size, block, dfi, self._width()
            )
        else:
            if not self.ecu_path.text():
                self._append(f"{FILE_MODES[op].capitalize()}: a path on the ECU is needed")
                return
            self.manager.file_transfer(op, self.ecu_path.text(), self.local.text(), block, dfi)

    def _agree(self, op) -> bool:
        """Ask once a session before changing what is on the ECU.

        Keyed on the operation rather than on the file: agreeing to flash one
        image is agreeing to flash, which is the part that carries the risk.
        """
        if op == "download":
            what = self._image.summary() if self._image else self.local.text()
            erasing = "\nThe memory it goes in is erased first." if self.erase.isChecked() else ""
            text = (
                f"About to write to the ECU's memory:\n\n{what}\n{erasing}\n"
                "An interrupted or wrong image can leave the ECU unable to start."
            )
        elif op == 2:
            text = f"About to delete {self.ecu_path.text()!r} from the ECU."
        else:
            text = (
                f"About to {FILE_MODES[op]} {self.ecu_path.text()!r} on the ECU"
                f" from {self.local.text()!r}."
            )
        return self.confirm.ask(self, f"uds.transfer.{op}", "Write to the ECU?", text)

    @Slot(str, int, int)
    def _on_progress(self, label: str, done: int, total: int) -> None:
        self.bar.setMaximum(total or 0)
        self.bar.setValue(done)
        self.bar.setFormat(f"{label} %p%  ({done} of {total} bytes)")

    @Slot(bool)
    def _on_transferring(self, running: bool) -> None:
        self.start.setEnabled(not running)
        self.stop.setEnabled(running)
        self.operation.setEnabled(not running)
        if not running:
            self.bar.reset()
            self.bar.setFormat("")

    # --- DTCs -------------------------------------------------------------------------
    def _report(self):
        from pycangui.uds.dtc import BY_SUBFUNCTION

        return BY_SUBFUNCTION[self.report.currentData()]

    @Slot()
    def _on_report(self) -> None:
        """Only the boxes this report actually takes are live.

        Greyed out rather than hidden: which parameters a report wants is
        half of what the pane is here to teach, and an empty gap teaches
        nothing.
        """
        needs = self._report().needs
        record = next((field for field in RECORDS if field in needs), None)
        self.record_label.setText(
            "Snapshot" if record == SNAPSHOT else "Ext data" if record == EXTENDED else "Record"
        )
        for field, label, widget in self._dtc_fields:
            live = (record is not None) if field is None else (field in needs)
            # The label goes grey with its box: a live-looking name over a dead
            # box is the thing that makes a form look broken.
            label.setEnabled(live)
            widget.setEnabled(live)

    def _read_dtcs(self) -> None:
        report = self._report()
        values = {
            STATUS: lambda: self._int(self.dtc_mask),
            SEVERITY: lambda: self._int(self.severity),
            DTC: lambda: self._int(self.dtc_number),
            SNAPSHOT: lambda: self._int(self.record),
            EXTENDED: lambda: self._int(self.record),
            MEMORY: lambda: self._int(self.memory),
            GROUP: lambda: self._int(self.functional_group),
        }
        try:
            params = {field: values[field]() for field in report.needs}
        except ValueError as exc:
            self._append(f"DTC: {exc}")
            return
        self.manager.read_dtc_information(report.subfunction, **params)

    @Slot(str)
    def _append(self, text: str) -> None:
        self.output.moveCursor(QTextCursor.End)
        self.output.appendPlainText(text)
        if text.startswith("UDS"):  # open/close state also goes to the main Log
            self.ctx.log(text)
