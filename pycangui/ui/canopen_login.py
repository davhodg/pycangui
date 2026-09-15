# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Asking a node for an access level.

CANopen has no login, so this dialog only collects what a maker's login is
likely to want -- a level and perhaps a password -- and hands both to
``hooks/canopen.py::login``.  The level is remembered, because most people sit
at one level; the password is not, and is never written anywhere.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

LEVEL_KEY = "canopen.login_level"
MAX_LEVEL = 255

LEVEL_TIP = (
    "Which access level to ask for.  What the numbers mean is the device's own;\n"
    "the last one asked for is remembered."
)
NOTE = (
    "Done by hooks/canopen.py::login, written for your device.  The password is "
    "passed to it and is neither logged nor kept."
)


def remembered_level(settings) -> int:
    """The level last asked for, forgiving a hand-edited settings.json."""
    try:
        return min(max(int(settings.get(LEVEL_KEY, 1)), 0), MAX_LEVEL)
    except (TypeError, ValueError):
        return 1


class LoginDialog(QDialog):
    def __init__(self, parent: QWidget | None, node_id: int, level: int) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Log in to node {node_id}")

        self.level = QSpinBox()
        self.level.setRange(0, MAX_LEVEL)
        self.level.setValue(level)
        self.level.setToolTip(LEVEL_TIP)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.password.setPlaceholderText("if the device wants one")

        form = QFormLayout()
        form.addRow("Level:", self.level)
        form.addRow("Password:", self.password)
        note = QLabel(NOTE)
        note.setWordWrap(True)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(note)
        layout.addWidget(buttons)

    def chosen(self) -> tuple[int, str]:
        return self.level.value(), self.password.text()
