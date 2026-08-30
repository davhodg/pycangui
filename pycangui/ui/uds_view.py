"""UDS pane: addressing, session / security / tester present, DIDs, DTCs,
routines, ECU reset and raw requests.  Results go to the pane's own log."""

from __future__ import annotations

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from pycangui.core.backends import BACKENDS
from pycangui.core.context import Context
from pycangui.uds import UdsConfig, images
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
    ERASE_MEMORY,
    FILE_MODES,
    FILE_MODES_SENDING,
    RESETS,
    UdsManager,
    parse_bytes,
)
from pycangui.ui.confirm import Confirmations
from pycangui.ui.persist import remember

#: Operations that change what is on the ECU, and so are worth a question
#: before the first one of a session.
DESTRUCTIVE = ("download", 1, 2, 3, 6)


def _hex_edit(text: str, width: int = 70) -> QLineEdit:
    e = QLineEdit(text)
    e.setFont(QFont("Consolas", 9))
    e.setFixedWidth(width)
    return e


def _picker(known: dict[int, str], digits: int, describe=None) -> QComboBox:
    """A dropdown of the numbers somebody has a name for, that can still be typed in.

    Editable on purpose.  A shortlist that will not let you send anything else
    would be worse than no list at all: most of the identifiers and all of the
    interesting routines on a real ECU are manufacturer specific and will
    never be on it.
    """
    combo = QComboBox()
    combo.setEditable(True)
    combo.setInsertPolicy(QComboBox.NoInsert)
    combo.lineEdit().setFont(QFont("Consolas", 9))
    for number, name in sorted(known.items()):
        combo.addItem(f"{number:0{digits}X}  {name}", number)
        if describe and (text := describe(number)):
            combo.setItemData(combo.count() - 1, text, Qt.ToolTipRole)
    return combo


def _picked(combo: QComboBox) -> int:
    """The number out of a picker, whether it was chosen or typed.

    The text of a chosen entry is "F190  VIN", so the number is the first
    word of it; a typed one is only the number.
    """
    text = combo.currentText().strip().split()
    return int(text[0], 16) if text else 0


class UdsView(QWidget):
    def __init__(
        self, manager: UdsManager, ctx: Context, confirm: Confirmations | None = None
    ) -> None:
        super().__init__()
        self.manager = manager
        self.ctx = ctx
        self.confirm = confirm or Confirmations()
        self._image: images.Image | None = None
        cfg = UdsConfig.from_dict(ctx.settings.get("uds.config", {}))

        # --- addressing ----------------------------------------------------
        addr = QGroupBox("ECU")
        g = QGridLayout(addr)
        self.tx_id = _hex_edit(f"{cfg.tx_id:X}")
        self.rx_id = _hex_edit(f"{cfg.rx_id:X}")
        self.ext = QCheckBox("29-bit")
        self.ext.setToolTip("Address the ECU with 29-bit identifiers rather than 11-bit")
        self.ext.setChecked(cfg.extended_id)
        self.padding = QCheckBox("Pad")
        self.padding.setToolTip(
            "Pad every frame out to 8 bytes.  Some ECUs require it and ignore\n"
            "anything shorter; others do not mind either way."
        )
        self.padding.setChecked(cfg.padding is not None)
        self.transport = QComboBox()
        self.transport.setToolTip("ISO-TP implementation (add your own in the backends folder)")
        for spec in BACKENDS.specs("isotp"):
            self.transport.addItem(spec.name, spec.name)
            self.transport.setItemData(self.transport.count() - 1, spec.description, Qt.ToolTipRole)
        index = self.transport.findData(manager.backend_name)
        if index >= 0:
            self.transport.setCurrentIndex(index)
        self.transport.currentTextChanged.connect(manager.set_backend)
        self.open_btn = QPushButton("Open")
        self.open_btn.setToolTip(
            "Open an ISO-TP connection on the addresses above.\n"
            "Nothing else on this pane works until it is open."
        )
        self.open_btn.setCheckable(True)
        self.open_btn.toggled.connect(self._toggle_open)
        for col, (label, w) in enumerate(
            (("Tx ID", self.tx_id), ("Rx ID", self.rx_id), ("", self.ext), ("", self.padding))
        ):
            if label:
                g.addWidget(QLabel(label), 0, col * 2)
            g.addWidget(w, 0, col * 2 + 1)
        g.addWidget(QLabel(" Transport"), 0, 9)
        g.addWidget(self.transport, 0, 10)
        g.addWidget(self.open_btn, 0, 11)

        # --- session / security ----------------------------------------------
        sess = QGroupBox("Session and security")
        h = QHBoxLayout(sess)
        self.session = QComboBox()
        self.session.setToolTip(
            "DiagnosticSessionControl (0x10).  Most services are only allowed\n"
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
        self.level = QSpinBox()
        self.level.setRange(1, 0x7D)
        self.level.setSingleStep(2)
        h.addWidget(self.level)
        unlock = QPushButton("Unlock")
        unlock.setToolTip(
            "SecurityAccess (0x27): ask for a seed and answer it with a key.\n"
            "There is no standard algorithm -- the key comes from\n"
            "hooks/uds.py::security_key, which you write."
        )
        unlock.clicked.connect(lambda: self.manager.unlock(self.level.value()))
        h.addWidget(unlock)
        self.tp = QCheckBox("Tester present")
        self.tp.setToolTip(
            "Send TesterPresent (0x3E) every couple of seconds.\n"
            "Without it the ECU drops back to the default session, and any\n"
            "unlock with it, after a few seconds of quiet."
        )
        self.tp.toggled.connect(self.manager.set_tester_present)
        h.addWidget(self.tp)
        h.addStretch()

        # --- reset ---------------------------------------------------------------
        # Its own box: a reset is not part of getting into a session, it is the
        # one control here that interrupts whatever the ECU was doing.
        reset_box = QGroupBox("ECU reset")
        r = QHBoxLayout(reset_box)
        self.reset_type = QComboBox()
        for code, name in RESETS.items():
            self.reset_type.addItem(name, code)
        r.addWidget(QLabel("Type"))
        r.addWidget(self.reset_type)
        reset = QPushButton("Reset")
        reset.setToolTip(
            "ECUReset (0x11).  The ECU restarts, so the session and any\n"
            "security unlock are lost with it."
        )
        reset.clicked.connect(lambda: self.manager.ecu_reset(self.reset_type.currentData()))
        r.addWidget(reset)
        r.addStretch()

        # --- data ----------------------------------------------------------------
        data = QGroupBox("Data and routines")
        g = QGridLayout(data)
        self.did = _picker(manager.did_choices(), 4, manager.did_description)
        self.did.setToolTip(
            "The identifier to read or write.  The list is what ISO 14229-1\n"
            "names plus your own DID_NAMES; anything else can be typed.\n"
            "Hover an entry for what it holds."
        )
        self.did.setCurrentText("F190")
        self.did_value = QLineEdit()
        self.did_value.setFont(QFont("Consolas", 9))
        read_did = QPushButton("Read DID")
        read_did.setToolTip("ReadDataByIdentifier (0x22)")
        read_did.clicked.connect(lambda: self.manager.read_did(_picked(self.did)))
        write_did = QPushButton("Write DID")
        write_did.setToolTip(
            "WriteDataByIdentifier (0x2E).  What you type is turned into bytes\n"
            "by hooks/uds.py::did_encode -- hex by default."
        )
        write_did.clicked.connect(
            lambda: self.manager.write_did(_picked(self.did), self.did_value.text())
        )
        g.addWidget(QLabel("DID"), 0, 0)
        g.addWidget(self.did, 0, 1)
        g.addWidget(read_did, 0, 2)
        g.addWidget(self.did_value, 0, 3)
        g.addWidget(write_did, 0, 4)

        self.routine = _picker(manager.routine_choices(), 4, manager.routine_description)
        self.routine.setToolTip(
            "The routine to run.  ISO 14229-1 names four; everything from 0200\n"
            "to DFFF is the manufacturer's, which is where the rest of a flash\n"
            "sequence lives, so type those or add them to ROUTINE_NAMES."
        )
        self.routine.setCurrentText("0203")
        self.routine_data = QLineEdit()
        self.routine_data.setFont(QFont("Consolas", 9))
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
        g.addWidget(QLabel("Routine"), 1, 0)
        g.addWidget(self.routine, 1, 1)
        g.addLayout(rbox, 1, 2)
        g.addWidget(self.routine_data, 1, 3, 1, 2)

        self.raw = QLineEdit("22 F1 90")
        self.raw.setFont(QFont("Consolas", 9))
        self.raw.returnPressed.connect(self._send_raw)
        raw_btn = QPushButton("Send raw")
        raw_btn.setToolTip(
            "Send these bytes as a request, with no help: the first byte is the\n"
            "service id and the rest is whatever that service expects."
        )
        raw_btn.clicked.connect(self._send_raw)
        g.addWidget(QLabel("Raw"), 2, 0)
        g.addWidget(self.raw, 2, 1, 1, 3)
        g.addWidget(raw_btn, 2, 4)

        # --- DTCs ------------------------------------------------------------------
        # ReadDTCInformation is twenty-odd reports wearing one service number,
        # and each takes a different set of parameters.  Choosing the report
        # first and letting it decide which boxes are live is the only way to
        # offer all of them without offering nonsense.
        dtc_box = QGroupBox("DTCs")
        d = QGridLayout(dtc_box)

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
        read_dtc = QPushButton("Read")
        read_dtc.setToolTip("Send the report chosen on the left")
        read_dtc.clicked.connect(self._read_dtcs)
        d.addWidget(QLabel("Report"), 0, 0)
        d.addWidget(self.report, 0, 1, 1, 10)
        d.addWidget(read_dtc, 0, 11)

        self.dtc_mask = _hex_edit("FF", 50)
        self.dtc_mask.setToolTip(
            "Which faults to ask about.  A bit set means "
            + "include it:\n  "
            + "\n  ".join(STATUS_BITS)
            + "\nFF is everything; 08 is only the confirmed ones."
        )
        self.severity = _hex_edit("FF", 50)
        self.severity.setToolTip(
            "Severity bits (ISO 14229-1): 0x20 maintenance only,\n"
            "0x40 check at next halt, 0x80 check immediately."
        )
        self.dtc_number = _hex_edit("000000", 70)
        self.dtc_number.setToolTip("The three-byte DTC the report is about, in hex")
        self.record = _hex_edit("FF", 50)
        self.record.setToolTip("Which record to read.  FF asks for all of them.")
        self.memory = _hex_edit("00", 50)
        self.memory.setToolTip("Which user-defined DTC memory to read from")
        self.functional_group = _hex_edit("33", 50)
        self.functional_group.setToolTip(
            "WWH-OBD functional group: 33 is emissions, FE all groups, FF the VOBD system"
        )
        self.record_label = QLabel("Record")
        for column, (label, widget) in enumerate(
            (
                (QLabel("Status"), self.dtc_mask),
                (QLabel("Severity"), self.severity),
                (QLabel("DTC"), self.dtc_number),
                (self.record_label, self.record),
                (QLabel("Memory"), self.memory),
                (QLabel("Group"), self.functional_group),
            )
        ):
            d.addWidget(label, 1, column * 2)
            d.addWidget(widget, 1, column * 2 + 1)

        self.dtc_setting = QCheckBox("DTC setting on")
        self.dtc_setting.setToolTip(
            "ControlDTCSetting (0x85).  Untick to stop the ECU recording new\n"
            "faults while you work on it, so that pulling a connector does not\n"
            "leave one behind.  The ECU turns it back on itself when the\n"
            "session ends, which is worth remembering when it looks as though\n"
            "the setting did not take.  The tick says what was last asked for,\n"
            "not what the ECU has done about it."
        )
        self.dtc_setting.setChecked(True)
        self.dtc_setting.toggled.connect(self.manager.set_dtc_setting)
        self.clear_group = _hex_edit("FFFFFF", 70)
        self.clear_group.setToolTip(
            "Which faults to erase.  FFFFFF is all of them; a group such as\n"
            "FFFF33 is emissions related only."
        )
        clear_dtc = QPushButton("Clear")
        clear_dtc.setToolTip(
            "ClearDiagnosticInformation (0x14).  The ECU's stored faults are\n"
            "erased, along with the freeze frames that go with them."
        )
        clear_dtc.clicked.connect(lambda: self.manager.clear_dtcs(self._int(self.clear_group)))
        self.standard = QComboBox()
        self.standard.setToolTip(
            "Which edition of ISO 14229-1 requests are built to.  It applies to\n"
            "every service, but it shows up here: the 2020 edition withdrew the\n"
            "mirror memory reports, and they cannot be sent while it is chosen."
        )
        for year in STANDARDS:
            self.standard.addItem(str(year), year)
        self.standard.setCurrentText(str(DEFAULT_STANDARD))
        self.standard.currentTextChanged.connect(lambda text: self.manager.set_standard(int(text)))
        d.addWidget(self.dtc_setting, 2, 0, 1, 3)
        d.addWidget(QLabel("Clear group"), 2, 3)
        d.addWidget(self.clear_group, 2, 4)
        d.addWidget(clear_dtc, 2, 5)
        d.addWidget(QLabel("Standard"), 2, 10)
        d.addWidget(self.standard, 2, 11)

        for key, widget in (
            ("uds.session", self.session),
            ("uds.did", self.did),
            ("uds.routine", self.routine),
            ("uds.dtc.report", self.report),
            ("uds.dtc.status", self.dtc_mask),
            ("uds.dtc.standard", self.standard),
        ):
            remember(ctx, key, widget)
        self._on_report()

        # --- transfer -------------------------------------------------------------
        # Its own box because it is the one thing here that runs for minutes
        # rather than milliseconds, and the only one with something to cancel.
        xfer = QGroupBox("Transfer")
        x = QGridLayout(xfer)

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
        x.addWidget(QLabel("Operation"), 0, 0)
        x.addWidget(self.operation, 0, 1, 1, 2)

        self.block = QSpinBox()
        self.block.setRange(0, 4095)
        self.block.setSpecialValueText("from ECU")
        self.block.setToolTip(
            "Data bytes per TransferData.  Left at 0 the ECU's own\n"
            "maxNumberOfBlockLength is used, less the two bytes the service id\n"
            "and the block counter take out of it."
        )
        x.addWidget(QLabel("Block"), 0, 3)
        x.addWidget(self.block, 0, 4)

        self.width_bits = QComboBox()
        self.width_bits.addItems(("auto", "8", "16", "24", "32"))
        self.width_bits.setToolTip(
            "Bits used to write the address and the size in the request.\n"
            "Auto uses the narrowest that fits; bootloaders that insist on a\n"
            "fixed width answer anything else with NRC 0x13."
        )
        x.addWidget(QLabel("Width"), 0, 5)
        x.addWidget(self.width_bits, 0, 6)

        self.dfi = _hex_edit("00", 40)
        self.dfi.setToolTip(
            "dataFormatIdentifier: high nibble compression, low nibble\n"
            "encryption.  00 is plain bytes, which is what most bootloaders\n"
            "want and all of them understand."
        )
        x.addWidget(QLabel("DFI"), 0, 7)
        x.addWidget(self.dfi, 0, 8)

        self.local = QLineEdit()
        self.local.setPlaceholderText("Intel HEX, S-record or raw binary")
        self.local.editingFinished.connect(self._reload_image)
        browse = QPushButton("Browse...")
        browse.setToolTip("Choose the file on this computer")
        browse.clicked.connect(self._browse)
        x.addWidget(QLabel("File"), 1, 0)
        x.addWidget(self.local, 1, 1, 1, 7)
        x.addWidget(browse, 1, 8)

        self.address = _hex_edit("", 90)
        self.address.setToolTip(
            "Where the bytes go, in hex.  A hex or S-record file carries its\n"
            "own address and fills this in; a raw binary has none, so for one\n"
            "of those it has to be typed."
        )
        self.address.editingFinished.connect(self._reload_image)
        self.byte_count = _hex_edit("", 90)
        self.byte_count.setToolTip("How many bytes to read out of the ECU, in hex")
        x.addWidget(QLabel("Address"), 2, 0)
        x.addWidget(self.address, 2, 1)
        x.addWidget(QLabel("Size"), 2, 2)
        x.addWidget(self.byte_count, 2, 3, 1, 2)

        self.ecu_path = QLineEdit()
        self.ecu_path.setPlaceholderText("path on the ECU")
        self.ecu_path.setToolTip(
            "The name the file has on the ECU.  This is what 0x38 transfers\n"
            "by, in place of an address."
        )
        x.addWidget(QLabel("On ECU"), 2, 5)
        x.addWidget(self.ecu_path, 2, 6, 1, 3)

        # A flash sequence is an erase, then the blocks, then something that
        # has the ECU check what it was given.  Only the erase is standardised.
        self.erase = QCheckBox("Erase first")
        self.erase.setToolTip(
            f"RoutineControl start {ERASE_MEMORY:04X} over every segment before\n"
            "the first one is written.  Flash has to be erased before it can be\n"
            "written, and this is the one routine ISO 14229-1 names for it.\n"
            "All of them first, not each before its own download: two segments\n"
            "can share a flash block, and erasing between them would take the\n"
            "first one back out again."
        )
        self.check = QCheckBox("Check after")
        self.check.setToolTip(
            "Run a routine once each segment has been sent, to have the ECU\n"
            "check what it was given.  Unlike the erase this one has no\n"
            "standard behind it -- the number beside it is the one the\n"
            "HIS/AUTOSAR bootloaders settled on, and yours may differ.\n"
            "What it is sent comes from hooks/uds.py::check_options."
        )
        self.check_routine = _hex_edit(f"{CHECK_MEMORY:04X}", 50)
        self.check_routine.setToolTip("Which routine to run afterwards")
        self.erase.toggled.connect(self._on_operation)
        self.check.toggled.connect(self._on_operation)
        x.addWidget(self.erase, 3, 0, 1, 2)
        x.addWidget(self.check, 3, 2)
        x.addWidget(self.check_routine, 3, 3)

        self.start = QPushButton("Download")
        self.start.clicked.connect(self._start)
        self.bar = QProgressBar()
        self.bar.setTextVisible(True)
        self.stop = QPushButton("Cancel")
        self.stop.setToolTip(
            "Stop after the block being sent now.  The ECU is waiting for a\n"
            "TransferData it has already been promised, so stopping part way\n"
            "through one would leave the connection out of step."
        )
        self.stop.setEnabled(False)
        self.stop.clicked.connect(manager.cancel_transfer)
        x.addWidget(self.start, 4, 0, 1, 2)
        x.addWidget(self.bar, 4, 2, 1, 6)
        x.addWidget(self.stop, 4, 8)

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
        self.output.setFont(QFont("Consolas", 9))
        self.output.setMaximumBlockCount(2000)

        # Scrolled for the same reason as the LSS pane: the controls must not
        # dictate how small the dock can be made.
        controls = QWidget()
        controls_layout = QVBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        for w in (addr, sess, reset_box, data, dtc_box, xfer):
            controls_layout.addWidget(w)
        controls_layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidget(controls)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setMinimumHeight(0)

        self.output.setMinimumHeight(40)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.addWidget(scroll, 1)
        layout.addWidget(self.output)

        manager.result.connect(self._append)
        manager.opened.connect(self._on_opened)
        manager.progress.connect(self._on_progress)
        manager.transferring.connect(self._on_transferring)

    # --- helpers ----------------------------------------------------------------------
    @staticmethod
    def _int(edit: QLineEdit) -> int:
        return int(edit.text().strip() or "0", 16)

    def _config(self) -> UdsConfig:
        cfg = UdsConfig(
            tx_id=self._int(self.tx_id),
            rx_id=self._int(self.rx_id),
            extended_id=self.ext.isChecked(),
            padding=0xCC if self.padding.isChecked() else None,
        )
        self.ctx.settings.set("uds.config", cfg.to_dict())
        return cfg

    @Slot(bool)
    def _toggle_open(self, on: bool) -> None:
        if on:
            try:
                self.manager.open(self._config())
            except ValueError as exc:
                self._append(f"UDS: bad id: {exc}")
                self.open_btn.setChecked(False)
        else:
            self.manager.close()

    @Slot(bool)
    def _on_opened(self, opened: bool) -> None:
        self.open_btn.blockSignals(True)
        self.open_btn.setChecked(opened)
        self.open_btn.setText("Close" if opened else "Open")
        self.open_btn.blockSignals(False)
        if not opened:
            self.tp.setChecked(False)

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
                "then RequestTransferExit.  One RequestDownload per segment:\n"
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
            path, _ = QFileDialog.getSaveFileName(self, caption, "", filt or "All files (*)")
        else:
            filt = images.READ_FILTER if self._operation() == "download" else "All files (*)"
            path, _ = QFileDialog.getOpenFileName(self, "Transfer file", "", filt)
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
        for field, widget in (
            (STATUS, self.dtc_mask),
            (SEVERITY, self.severity),
            (DTC, self.dtc_number),
            (MEMORY, self.memory),
            (GROUP, self.functional_group),
        ):
            widget.setEnabled(field in needs)
        record = next((field for field in RECORDS if field in needs), None)
        self.record.setEnabled(record is not None)
        self.record_label.setText(
            "Snapshot" if record == SNAPSHOT else "Ext data" if record == EXTENDED else "Record"
        )

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
