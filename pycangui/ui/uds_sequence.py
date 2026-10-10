# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""The numbers a flash sequence needs, on a form of their own.

Which steps are done is ticked in the Transfer tab's Before and After menus.
A few of them need a number the pane has no box for -- three unlocks can be
at three levels, and the pane has one level box -- so they are asked for
here, once, and kept with the workspace.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from pycangui.uds.manager import COMM_CONTROLS, COMM_MESSAGES, LINK_BITRATES, RESETS
from pycangui.uds.sequence import Values
from pycangui.uds.standard import MAX_SECURITY_LEVEL
from pycangui.ui import fonts

STEPS_KEY = "uds.sequence.steps"
VALUES_KEY = "uds.sequence.values"

LEVEL_TIP = "The security level, counting from 1: level 2 is sub-functions 03 and 04."
FINGERPRINT_TIP = (
    "The identifier written, and the bytes written to it, in hex. What a\n"
    "bootloader wants here is its maker's own: often a date and a tester's\n"
    "serial number."
)
CHECK_TIP = (
    "The routine run once each segment has been sent, where Check memory is\n"
    "ticked under After. Unlike the erase it has no standard behind it:\n"
    "0202 is the one the HIS/AUTOSAR bootloaders settled on. What it is sent\n"
    "comes from hooks/uds.py::check_options."
)
WAIT_TIP = "How long the ECU is given to restart before anything more is asked of it."


#: Where the check routine was kept while it had a box on the Transfer tab.
OLD_CHECK_ROUTINE_KEY = "uds.transfer.check_routine"


def load(settings) -> Values:
    saved = settings.get(VALUES_KEY, None)
    values = Values.from_saved(saved)
    if not (isinstance(saved, dict) and "check_routine" in saved):
        # A workspace from before it moved here: what was typed in the old box.
        try:
            values.check_routine = int(str(settings.get(OLD_CHECK_ROUTINE_KEY, "")), 16)
        except ValueError:
            pass
    return values


def save(settings, values: Values) -> None:
    settings.set(VALUES_KEY, values.to_save())


def chosen(settings) -> set[str]:
    """The steps ticked, as remembered. None to begin with: a download is a download."""
    saved = settings.get(STEPS_KEY, [])
    return {str(key) for key in saved} if isinstance(saved, list) else set()


def _level(value: int) -> QSpinBox:
    box = QSpinBox()
    box.setRange(1, MAX_SECURITY_LEVEL)
    box.setValue(value)
    box.setToolTip(LEVEL_TIP)
    return box


def _choice(entries: dict, value) -> QComboBox:
    box = QComboBox()
    for code, name in entries.items():
        box.addItem(str(name)[0].upper() + str(name)[1:], code)
    box.setCurrentIndex(max(box.findData(value), 0))
    return box


class ValuesDialog(QDialog):
    """Asks for the sequence's values. ``values()`` is what was chosen."""

    def __init__(self, parent: QWidget | None, values: Values) -> None:
        super().__init__(parent)
        self.setWindowTitle("Flash sequence values")
        self.unlock_extended = _level(values.unlock_extended)
        self.unlock_programming = _level(values.unlock_programming)
        self.unlock_after = _level(values.unlock_after)
        # Off is any of them but "enable Rx and Tx", which is what on means.
        self.comm_control = _choice(
            {code: name for code, name in COMM_CONTROLS.items() if code}, values.comm_control
        )
        self.comm_messages = _choice(COMM_MESSAGES, values.comm_messages)
        self.bitrate = _choice({b: f"{b // 1000} kbit/s" for b in LINK_BITRATES}, values.bitrate)
        self.fingerprint_did = QLineEdit(f"{values.fingerprint_did:04X}")
        self.fingerprint_did.setFont(fonts.mono())
        self.fingerprint_did.setToolTip(FINGERPRINT_TIP)
        self.fingerprint_data = QLineEdit(values.fingerprint_data)
        self.fingerprint_data.setFont(fonts.mono())
        self.fingerprint_data.setPlaceholderText("bytes (hex)")
        self.fingerprint_data.setToolTip(FINGERPRINT_TIP)
        self.check_routine = QLineEdit(f"{values.check_routine:04X}")
        self.check_routine.setFont(fonts.mono())
        self.check_routine.setToolTip(CHECK_TIP)
        self.reset_type = _choice(RESETS, values.reset_type)
        self.reset_wait = QDoubleSpinBox()
        self.reset_wait.setRange(0, 60)
        self.reset_wait.setDecimals(1)
        self.reset_wait.setSuffix(" s")
        self.reset_wait.setValue(values.reset_wait_s)
        self.reset_wait.setToolTip(WAIT_TIP)

        form = QFormLayout()
        form.addRow("Unlock level, extended session:", self.unlock_extended)
        form.addRow("Unlock level, programming session:", self.unlock_programming)
        form.addRow("Unlock level, after the reset:", self.unlock_after)
        form.addRow("Communication off as:", self.comm_control)
        form.addRow("Communication, which messages:", self.comm_messages)
        form.addRow("Bitrate to change to:", self.bitrate)
        form.addRow("Fingerprint identifier:", self.fingerprint_did)
        form.addRow("Fingerprint bytes:", self.fingerprint_data)
        form.addRow("Check routine:", self.check_routine)
        form.addRow("Reset type:", self.reset_type)
        form.addRow("Wait after the reset:", self.reset_wait)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def problem(self) -> str:
        """What is wrong with what is typed, or nothing."""
        try:
            did = int(self.fingerprint_did.text().strip() or "0", 16)
        except ValueError:
            return "the fingerprint identifier is not hex"
        if not 0 <= did <= 0xFFFF:
            return "the fingerprint identifier is two bytes"
        try:
            bytes.fromhex(self.fingerprint_data.text())
        except ValueError:
            return "the fingerprint bytes are not hex, two digits each"
        try:
            routine = int(self.check_routine.text().strip() or "0", 16)
        except ValueError:
            return "the check routine is not hex"
        if not 0 < routine <= 0xFFFF:
            return "the check routine is two bytes, and not zero"
        return ""

    def accept(self) -> None:
        if said := self.problem():
            self.fingerprint_data.setToolTip(f"{FINGERPRINT_TIP}\n\nNot taken: {said}.")
            self.fingerprint_did.setFocus()
            return
        super().accept()

    def values(self) -> Values:
        return Values(
            unlock_extended=self.unlock_extended.value(),
            unlock_programming=self.unlock_programming.value(),
            unlock_after=self.unlock_after.value(),
            comm_control=self.comm_control.currentData(),
            comm_messages=self.comm_messages.currentData(),
            bitrate=self.bitrate.currentData(),
            fingerprint_did=int(self.fingerprint_did.text().strip() or "0", 16),
            fingerprint_data=self.fingerprint_data.text().strip(),
            check_routine=int(self.check_routine.text().strip() or "0", 16),
            reset_type=self.reset_type.currentData(),
            reset_wait_s=self.reset_wait.value(),
        )
