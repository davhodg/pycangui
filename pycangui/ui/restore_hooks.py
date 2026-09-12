# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Putting pycangui's own hook files back, without eating anybody's work.

An edited hook is the usual way to break pycangui quietly: the file still
loads, the answers are just wrong.  Restoring one is the way back, and it is
a reset rather than a repair -- which is why it lives with the other resets
rather than beside the hook entries it undoes.

The rule the dialog exists to enforce is that nothing is deleted.  A hook
file is code somebody wrote, possibly the only copy, and a tick box that ate
it would be a tick box people are right to be afraid of.  The old file is
renamed ``<name>.py.bak`` and said so in advance, here and in the log.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from pycangui.core.hooks import Hooks, registry

EXPLANATION = (
    "Replace a hook file with the one pycangui ships.\n\n"
    "Nothing is deleted: what is there now is renamed to <name>.py.bak in the "
    "same folder, so an answer you spent an afternoon on is still there to "
    "copy back out of."
)
NOTHING_EDITED = "Every hook file is already the one pycangui ships."


class RestoreHooks(QDialog):
    """Which hook files to put back.  Nothing is ticked to start with."""

    def __init__(self, parent: QWidget | None, hooks: Hooks) -> None:
        super().__init__(parent)
        self.setWindowTitle("Restore hook files")
        self.hooks = hooks
        self._boxes: dict[str, QCheckBox] = {}

        layout = QVBoxLayout(self)
        note = QLabel(EXPLANATION)
        note.setWordWrap(True)
        layout.addWidget(note)

        edited = set(hooks.edited())
        for module in sorted(registry()):
            box = QCheckBox(f"{module}.py")
            # An untouched file has nothing to restore and nothing to lose,
            # so it is shown greyed rather than left out: somebody looking
            # for it should find out it is already the default, not wonder
            # where it went.
            box.setEnabled(module in edited)
            if module not in edited:
                box.setText(f"{module}.py  (unchanged)")
            layout.addWidget(box)
            self._boxes[module] = box
        if not edited:
            already = QLabel(NOTHING_EDITED)
            already.setWordWrap(True)
            layout.addWidget(already)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Restore")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def chosen(self) -> list[str]:
        return [name for name, box in self._boxes.items() if box.checkState() == Qt.Checked]
