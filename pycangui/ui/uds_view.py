"""UDS pane: addressing, session / security / tester present, DIDs, DTCs,
routines, ECU reset and raw requests.  Results go to the pane's own log."""

from __future__ import annotations

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from pycangui.core.backends import BACKENDS
from pycangui.core.context import Context
from pycangui.uds import UdsConfig
from pycangui.uds.manager import RESETS, SESSIONS, UdsManager, parse_bytes


def _hex_edit(text: str, width: int = 70) -> QLineEdit:
    e = QLineEdit(text)
    e.setFont(QFont("Consolas", 9))
    e.setFixedWidth(width)
    return e


class UdsView(QWidget):
    def __init__(self, manager: UdsManager, ctx: Context) -> None:
        super().__init__()
        self.manager = manager
        self.ctx = ctx
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
        for code, name in SESSIONS.items():
            if code == 4:
                continue
            b = QPushButton(name.capitalize())
            b.setToolTip(
                f"DiagnosticSessionControl (0x10): move the ECU into its {name} session.\n"
                "Most services are only allowed in some of them."
            )
            b.clicked.connect(lambda _=False, c=code: self.manager.change_session(c))
            h.addWidget(b)
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
        data = QGroupBox("Data, DTCs, routines")
        g = QGridLayout(data)
        self.did = _hex_edit("F190")
        self.did_value = QLineEdit()
        self.did_value.setFont(QFont("Consolas", 9))
        read_did = QPushButton("Read DID")
        read_did.setToolTip("ReadDataByIdentifier (0x22)")
        read_did.clicked.connect(lambda: self.manager.read_did(self._int(self.did)))
        write_did = QPushButton("Write DID")
        write_did.setToolTip(
            "WriteDataByIdentifier (0x2E).  What you type is turned into bytes\n"
            "by hooks/uds.py::did_encode -- hex by default."
        )
        write_did.clicked.connect(
            lambda: self.manager.write_did(self._int(self.did), self.did_value.text())
        )
        g.addWidget(QLabel("DID"), 0, 0)
        g.addWidget(self.did, 0, 1)
        g.addWidget(read_did, 0, 2)
        g.addWidget(self.did_value, 0, 3)
        g.addWidget(write_did, 0, 4)

        self.dtc_mask = _hex_edit("FF", 50)
        read_dtc = QPushButton("Read DTCs")
        read_dtc.setToolTip("ReadDTCInformation (0x19), for the faults matching the status mask")
        read_dtc.clicked.connect(lambda: self.manager.read_dtcs(self._int(self.dtc_mask)))
        clear_dtc = QPushButton("Clear DTCs")
        clear_dtc.setToolTip(
            "ClearDiagnosticInformation (0x14).  The ECU's stored faults are\n"
            "erased, along with the freeze frames that go with them."
        )
        clear_dtc.clicked.connect(lambda: self.manager.clear_dtcs())
        g.addWidget(QLabel("Status mask"), 1, 0)
        g.addWidget(self.dtc_mask, 1, 1)
        g.addWidget(read_dtc, 1, 2)
        g.addWidget(clear_dtc, 1, 4)

        self.routine = _hex_edit("0203")
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
                    c, self._int(self.routine), parse_bytes(self.routine_data.text())
                )
            )
            rbox.addWidget(b)
        g.addWidget(QLabel("Routine"), 2, 0)
        g.addWidget(self.routine, 2, 1)
        g.addLayout(rbox, 2, 2)
        g.addWidget(self.routine_data, 2, 3, 1, 2)

        self.raw = QLineEdit("22 F1 90")
        self.raw.setFont(QFont("Consolas", 9))
        self.raw.returnPressed.connect(self._send_raw)
        raw_btn = QPushButton("Send raw")
        raw_btn.setToolTip(
            "Send these bytes as a request, with no help: the first byte is the\n"
            "service id and the rest is whatever that service expects."
        )
        raw_btn.clicked.connect(self._send_raw)
        g.addWidget(QLabel("Raw"), 3, 0)
        g.addWidget(self.raw, 3, 1, 1, 3)
        g.addWidget(raw_btn, 3, 4)

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
        for w in (addr, sess, reset_box, data):
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

    @Slot(str)
    def _append(self, text: str) -> None:
        self.output.moveCursor(QTextCursor.End)
        self.output.appendPlainText(text)
        if text.startswith("UDS"):  # open/close state also goes to the main Log
            self.ctx.log(text)
