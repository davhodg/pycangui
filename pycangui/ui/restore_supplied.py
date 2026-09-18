# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Putting pycangui's own hook and node files back, without eating anybody's work.

Two reasons to want one back. An edited hook is the usual way to break
pycangui quietly: the file still loads, the answers are just wrong. And a
file you changed is never updated by a new pycangui, so taking the newer one
over your changes is a decision only you can make. Either way it is a reset
rather than a repair, which is why it lives with the other resets.

The rule the dialog exists to enforce is that nothing is deleted. These
files are code somebody wrote, possibly the only copy, and a tick box that
ate one would be a tick box people are right to be afraid of. The old file
is renamed ``<name>.py.bak`` and said so in advance, here and in the log.
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

from pycangui.core.supplied import Supplied

EXPLANATION = (
    "Replace files in your workspace with the ones this pycangui ships.\n\n"
    "Nothing is deleted: what is there now is renamed to <name>.py.bak in the "
    "same folder, so an answer you spent an afternoon on is still there to "
    "copy back out of."
)
NOTHING_EDITED = "Every file is already the one pycangui ships."
TITLES = {"hooks": "Hooks", "nodes": "Simulated nodes"}


class RestoreSupplied(QDialog):
    """Which supplied files to put back. Nothing is ticked to start with."""

    def __init__(self, parent: QWidget | None, groups: dict[str, Supplied]) -> None:
        super().__init__(parent)
        self.setWindowTitle("Restore supplied files")
        self._boxes: dict[tuple[str, str], QCheckBox] = {}

        layout = QVBoxLayout(self)
        note = QLabel(EXPLANATION)
        note.setWordWrap(True)
        layout.addWidget(note)

        anything = False
        for label, supplied in groups.items():
            layout.addWidget(QLabel(f"<b>{TITLES.get(label, label)}</b> in {label}/"))
            edited = set(supplied.edited())
            anything = anything or bool(edited)
            for name in supplied.names():
                box = QCheckBox(name)
                # An untouched file has nothing to restore and nothing to lose,
                # so it is shown greyed rather than left out: somebody looking
                # for it should find out it is already the one shipped, not
                # wonder where it went.
                box.setEnabled(name in edited)
                if name not in edited:
                    box.setText(f"{name}  (unchanged)")
                layout.addWidget(box)
                self._boxes[(label, name)] = box
        if not anything:
            already = QLabel(NOTHING_EDITED)
            already.setWordWrap(True)
            layout.addWidget(already)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Restore")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def chosen(self) -> list[tuple[str, str]]:
        """(folder label, file name) for every ticked box."""
        return [key for key, box in self._boxes.items() if box.checkState() == Qt.Checked]
