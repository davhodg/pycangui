# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Opening a measurement file and putting its signals on the plot.

The awkward part is not the reading, it is the *choosing*.  A real export
holds thousands of channels -- the one this was written against has 1,344
with anything in them -- and a tool that pushed all of them into the signals
list would produce a list nobody could find anything in, having spent a
minute doing it.  So the file is described first, from its header, and
nothing is read until somebody has said what they want.

Imported signals sit under the file's name as their group, beside the live
ones rather than instead of them, and *Forget* takes them away again.  A
signals list that only ever grows is one people stop opening.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pycangui.core import mdf
from pycangui.ui import messages

#: Past this, reading everything at once is a wait with no warning attached.
#: The number is not a limit -- it is when the question gets asked.
MANY = 200

INSTALL_TITLE = "Reading measurement files"
NOT_FROZEN = (
    "{why}\n\nInstall it now?  It is downloaded from PyPI into this "
    "installation of pycangui, and only this one."
)
#: The installed build ships asammdf, so reaching this means the installation
#: is damaged rather than merely lean -- and a frozen build has no Python
#: environment to pip into, so there is nothing to offer doing about it.
FROZEN = (
    "The asammdf library, which reads MDF and MF4 files, is missing from this "
    "installation.  It is normally included, so this build is incomplete: "
    "reinstalling pycangui should restore it."
)


class ChannelPicker(QDialog):
    """Which of a file's channels to read, out of possibly thousands."""

    def __init__(self, parent: QWidget, path: Path, summary, channels) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Import from {path.name}")
        self.resize(700, 620)
        self._channels = channels

        told = [
            f"MDF {summary.version} -- {summary.channels:,} channels, {summary.samples:,} samples",
            f"{len(channels):,} of them hold anything.",
        ]
        if summary.has_frames:
            told.append(
                "It also holds raw frames, which are traffic rather than "
                "measurements: replay it onto a channel to see those."
            )
        heading = QLabel("  ".join(told))
        heading.setWordWrap(True)

        self.search = QLineEdit()
        self.search.setPlaceholderText("filter by name...")
        self.search.textChanged.connect(self._filter)
        self.plumbing = QCheckBox("Show frame fields")
        self.plumbing.setToolTip(
            "The id, length and flags a bus log carries alongside its signals.\n"
            "Plottable, occasionally wanted, and hundreds of them at a time."
        )
        self.plumbing.toggled.connect(self._filter)

        find = QHBoxLayout()
        find.addWidget(self.search, 1)
        find.addWidget(self.plumbing)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Signal", "Unit", "Samples"])
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.header().resizeSection(0, 420)

        all_button = QPushButton("Select all shown")
        all_button.clicked.connect(lambda: self._tick(True))
        none_button = QPushButton("Select none")
        none_button.clicked.connect(lambda: self._tick(False))
        self.count = QLabel("")

        buttons = QHBoxLayout()
        buttons.addWidget(all_button)
        buttons.addWidget(none_button)
        buttons.addStretch()
        buttons.addWidget(self.count)

        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.button(QDialogButtonBox.Ok).setText("Import")
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(heading)
        layout.addLayout(find)
        layout.addWidget(self.tree, 1)
        layout.addLayout(buttons)
        layout.addWidget(box)

        self._fill()

    def _fill(self) -> None:
        self.tree.setUpdatesEnabled(False)
        self.tree.clear()
        for channel in self._channels:
            item = QTreeWidgetItem([channel.name, channel.unit, f"{channel.samples:,}"])
            item.setData(0, Qt.UserRole, channel.name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(0, Qt.Unchecked)
            item.setTextAlignment(2, Qt.AlignRight | Qt.AlignVCenter)
            self.tree.addTopLevelItem(item)
        self.tree.setUpdatesEnabled(True)
        self._filter()

    def _rows(self):
        for i in range(self.tree.topLevelItemCount()):
            yield self.tree.topLevelItem(i)

    def _filter(self) -> None:
        wanted = self.search.text().strip().lower()
        plumbing = self.plumbing.isChecked()
        shown = 0
        for item, channel in zip(self._rows(), self._channels, strict=True):
            hide = (wanted and wanted not in channel.name.lower()) or (
                channel.bus_metadata and not plumbing
            )
            item.setHidden(bool(hide))
            shown += not hide
        self.count.setText(f"{shown:,} shown")

    def _tick(self, on: bool) -> None:
        for item in self._rows():
            if not item.isHidden():
                item.setCheckState(0, Qt.Checked if on else Qt.Unchecked)

    def chosen(self) -> list[str]:
        return [
            str(item.data(0, Qt.UserRole))
            for item in self._rows()
            if item.checkState(0) == Qt.Checked
        ]


def ensure_available(parent: QWidget, ctx, frozen: bool = False) -> bool:
    """Make sure the library is there, offering to fetch it if it is not.

    Asked at the moment a file needs it rather than carried by everyone, and
    asked rather than done: sixty megabytes off the internet is not something
    to start because somebody opened a file dialog.
    """
    if mdf.available():
        return True
    if frozen:
        messages.information(parent, INSTALL_TITLE, FROZEN)
        ctx.warn(f"{mdf.PACKAGE} is missing from this build; MDF files cannot be read")
        return False
    answer = messages.question(
        parent,
        INSTALL_TITLE,
        NOT_FROZEN.format(why=mdf.WHY),
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.Yes,
    )
    if answer != QMessageBox.Yes:
        return False
    return _install(parent, ctx)


def _install(parent: QWidget, ctx) -> bool:
    """pip, in this interpreter, with something on screen while it runs."""
    import subprocess
    import sys

    waiting = QProgressDialog(f"Installing {mdf.PACKAGE}...", "", 0, 0, parent)
    waiting.setWindowTitle(INSTALL_TITLE)
    waiting.setCancelButton(None)
    waiting.setWindowModality(Qt.ApplicationModal)
    waiting.show()
    try:
        done = subprocess.run(
            [sys.executable, "-m", "pip", "install", mdf.PACKAGE],
            capture_output=True,
            text=True,
            timeout=900,
        )
    except Exception as exc:  # no pip, no network, no patience
        waiting.close()
        ctx.error(f"Installing {mdf.PACKAGE} failed: {exc}")
        return False
    waiting.close()
    if done.returncode != 0:
        tail = (done.stderr or done.stdout or "").strip().splitlines()[-3:]
        ctx.error(f"Installing {mdf.PACKAGE} failed:\n" + "\n".join(tail))
        return False
    if not mdf.available():
        ctx.error(f"{mdf.PACKAGE} installed but will not import; restart pycangui.")
        return False
    ctx.log(f"{mdf.PACKAGE} installed: measurement files can be read now")
    return True
