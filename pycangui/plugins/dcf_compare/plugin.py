# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Two CANopen configurations side by side: file against file, or file against device.

The pane is two source pickers and a table, and almost all of it is the two
pickers.  Which is right: comparing is the easy half, and *deciding what to
compare against what* is the half people get wrong.

Each side is a DCF or EDS file, or a node on the bus.  A file is read
immediately; a node is read in the background, and only for the objects the
other side names -- which is what makes comparing against a device take
seconds rather than minutes, and what lets it work on a device nobody has an
EDS for.

Two nodes can be compared as well, and then something still has to say which
objects: the EDS loaded against one of them.  With neither a file nor an EDS
in sight the pane refuses and says why, rather than reading a guessed-at range
of indices and calling the result a comparison.

Nothing here writes.  Comparing tells you what is different; putting it right
is the [Apply DCF] button in the CANopen pane, deliberately, because "write
these seventeen selected differences into the device in front of me" is a
bigger thing than this pane and deserves its own question.

A plugin rather than part of the tool, on the same line the other two are
drawn along: pycangui's own job is speaking CANopen -- reading an object,
writing one, capturing a dictionary into a DCF and putting one back.  What
somebody then *does* with two captured configurations is a workflow built on
top of that, and workflows are what plugins are for.  Uninstall it and the
CANopen pane is exactly as it was.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pycangui.ui import folders

# Relative, so that the copy of compare.py beside *this* file is the one
# that runs: an installed plugin naming it absolutely would reach back into
# the one pycangui ships, and editing your own would do nothing.
from . import compare as comparison

#: What a side can be.  Kept as text because it is what the combo box holds
#: and what gets written into the settings.
FILE, NODE = "File", "Node"

EDS_FILTER = "Device files (*.dcf *.eds);;All files (*)"

HEADINGS = ("Index", "Sub", "Object", "Left", "Right", "")

NOTHING_TO_READ = (
    "Neither side names any objects, so there is nothing to compare. A DCF "
    "says which objects matter; comparing two nodes needs an EDS loaded "
    "against one of them to say the same thing."
)


class Side(QWidget):
    """One half of the comparison: a file, or a node."""

    def __init__(self, label: str, canopen_manager, ctx) -> None:
        super().__init__()
        self.canopen = canopen_manager
        self.ctx = ctx

        self.kind = QComboBox()
        self.kind.addItems([FILE, NODE])
        self.kind.currentTextChanged.connect(self._show_kind)

        self.path = QLineEdit()
        self.path.setPlaceholderText("a .dcf or .eds file...")
        self.browse = QPushButton("Browse...")
        self.browse.clicked.connect(self._choose)

        self.node = QComboBox()
        self.node.setToolTip("A node on the bus. It is read when you press Compare.")

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(QLabel(label))
        row.addWidget(self.kind)
        row.addWidget(self.path, 1)
        row.addWidget(self.browse)
        row.addWidget(self.node, 1)
        self._show_kind(self.kind.currentText())

    def _show_kind(self, kind: str) -> None:
        for widget in (self.path, self.browse):
            widget.setVisible(kind == FILE)
        self.node.setVisible(kind == NODE)

    def _choose(self) -> None:
        path = folders.open_file(
            self, self.ctx, folders.EDS, "Open a device file", EDS_FILTER, self.ctx.eds_dir
        )
        if path:
            self.path.setText(path)

    def fill_nodes(self) -> None:
        chosen = self.node.currentData()
        self.node.clear()
        for node_id in self.canopen.nodes():
            self.node.addItem(f"Node {node_id}", node_id)
        at = self.node.findData(chosen)
        self.node.setCurrentIndex(max(at, 0))

    # --- what this side is ------------------------------------------------------------
    @property
    def is_node(self) -> bool:
        return self.kind.currentText() == NODE

    @property
    def node_id(self) -> int | None:
        return self.node.currentData() if self.is_node else None

    def file_reading(self) -> comparison.Reading | None:
        """The file this side names, read, or None with the reason reported."""
        path = self.path.text().strip()
        if not path:
            self.ctx.warn("Pick a file for both sides, or a node.")
            return None
        try:
            return comparison.read_file(path)
        except Exception as exc:  # a file that is not one, or that will not parse
            self.ctx.warn(f"{path} could not be read: {exc}")
            return None

    def state(self) -> dict:
        return {
            "kind": self.kind.currentText(),
            "path": self.path.text(),
            "node": self.node.currentData(),
        }

    def restore(self, state: dict) -> None:
        self.kind.setCurrentText(str(state.get("kind", FILE)))
        self.path.setText(str(state.get("path", "")))
        if (node_id := state.get("node")) is not None:
            at = self.node.findData(node_id)
            if at >= 0:
                self.node.setCurrentIndex(at)


class CompareView(QWidget):
    def __init__(self, canopen_manager, ctx, key: str = "compare") -> None:
        super().__init__()
        self.canopen = canopen_manager
        self.ctx = ctx
        self.key = key
        self.rows: list[comparison.Row] = []
        self._readings: dict[str, comparison.Reading] = {}
        self._busy = False

        self.left = Side("Left", canopen_manager, ctx)
        self.right = Side("Right", canopen_manager, ctx)

        self.compare_button = QPushButton("Compare")
        self.compare_button.clicked.connect(self.run)
        self.differences_only = QCheckBox("Differences only")
        self.differences_only.setChecked(True)
        self.differences_only.setToolTip(
            "An object held on one side and not the other is not a difference:\n"
            "a file carries only what it was given, and a device answers only\n"
            "what it implements. Those are listed separately."
        )
        self.differences_only.toggled.connect(self._show_rows)
        copy = QPushButton("Copy")
        copy.setToolTip("The comparison as text, for a note or a build record.")
        copy.clicked.connect(self._copy)

        buttons = QHBoxLayout()
        buttons.addWidget(self.compare_button)
        buttons.addWidget(self.differences_only)
        buttons.addStretch()
        buttons.addWidget(copy)

        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        self.note = QLabel("")
        self.note.setWordWrap(True)
        self.note.setEnabled(False)
        self.progress = QProgressBar()
        self.progress.setVisible(False)

        self.table = QTreeWidget()
        self.table.setColumnCount(len(HEADINGS))
        self.table.setHeaderLabels(list(HEADINGS))
        self.table.setRootIsDecorated(False)
        self.table.setAlternatingRowColors(True)
        self.table.setUniformRowHeights(True)
        self.table.setFont(QFont("Consolas", 9))
        self.table.header().setSectionResizeMode(2, QHeaderView.Stretch)

        sides = QGridLayout()
        sides.addWidget(self.left, 0, 0)
        sides.addWidget(self.right, 1, 0)

        layout = QVBoxLayout(self)
        layout.addLayout(sides)
        layout.addLayout(buttons)
        layout.addWidget(self.summary)
        layout.addWidget(self.note)
        layout.addWidget(self.progress)
        layout.addWidget(self.table, 1)

        self.fill_nodes()
        canopen_manager.node_seen.connect(lambda *_a: self.fill_nodes())
        canopen_manager.dcf_progress.connect(self._on_progress)
        self.restore()

    def fill_nodes(self) -> None:
        self.left.fill_nodes()
        self.right.fill_nodes()

    # --- doing it ---------------------------------------------------------------------
    def run(self) -> None:
        """Read whichever sides need reading, then compare.

        A file first, always, so that a node has a list of objects to be asked
        for.  Two nodes fall back to the EDS loaded against the left one, and
        with neither the pane says so rather than inventing a range to read.
        """
        if self._busy:
            return
        self._readings = {}
        for name, side in (("left", self.left), ("right", self.right)):
            if not side.is_node:
                reading = side.file_reading()
                if reading is None:
                    return
                self._readings[name] = reading
        wanted = comparison.objects_to_read(*self._readings.values())
        if not wanted:
            wanted = self._objects_from_eds()
        if not wanted:
            self.ctx.warn(NOTHING_TO_READ)
            self.summary.setText(NOTHING_TO_READ)
            return
        self._read_nodes(wanted)

    def _objects_from_eds(self) -> list[tuple[int, int]]:
        """Two nodes and no file: the EDS loaded against one of them says what."""
        from pycangui.canopen.manager import _all_variables

        for side in (self.left, self.right):
            node = self.canopen.node(side.node_id) if side.is_node else None
            od = getattr(node, "object_dictionary", None)
            if od is not None and len(od):
                return [
                    (var.index, var.subindex)
                    for var in _all_variables(od)
                    if var.index >= comparison.FIRST_INDEX and var.readable
                ]
        return []

    def _read_nodes(self, wanted: list[tuple[int, int]]) -> None:
        """Read the node sides, one after the other, then show the result."""
        outstanding = [
            (name, side)
            for name, side in (("left", self.left), ("right", self.right))
            if side.is_node
        ]
        if not outstanding:
            self._finish()
            return
        if any(side.node_id is None for _name, side in outstanding):
            self.ctx.warn("No node selected, or it is not on the bus.")
            return

        self._busy = True
        self.compare_button.setEnabled(False)
        self.progress.setVisible(True)

        def next_one() -> None:
            if not outstanding:
                self._busy = False
                self.compare_button.setEnabled(True)
                self.progress.setVisible(False)
                self._finish()
                return
            name, side = outstanding.pop(0)
            node_id = side.node_id
            self.summary.setText(f"Reading node {node_id}...")

            def done(values, error) -> None:
                if error:
                    self._busy = False
                    self.compare_button.setEnabled(True)
                    self.progress.setVisible(False)
                    self.ctx.warn(f"Node {node_id} could not be read: {error}")
                    self.summary.setText(f"Node {node_id} could not be read.")
                    return
                self._readings[name] = comparison.Reading(
                    label=f"Node {node_id}", values=values, node_id=node_id
                )
                next_one()

            self.canopen.read_objects(node_id, wanted, done)

        next_one()

    def _finish(self) -> None:
        left = self._readings.get("left")
        right = self._readings.get("right")
        if left is None or right is None:
            return
        self.rows = comparison.compare(left, right)
        how = comparison.summarise(self.rows)
        self.summary.setText(f"{left.label} against {right.label}: {how}")
        self.note.setText(comparison.node_id_warning(left, right))
        self.table.setHeaderLabels(["Index", "Sub", "Object", left.label, right.label, ""])
        self._show_rows()
        self.save()

    def _on_progress(self, done: int, total: int) -> None:
        self.progress.setRange(0, max(total, 1))
        self.progress.setValue(done)

    # --- showing it -------------------------------------------------------------------
    def _show_rows(self) -> None:
        only = self.differences_only.isChecked()
        self.table.clear()
        for row in self.rows:
            if only and row.state == comparison.SAME:
                continue
            item = QTreeWidgetItem(
                [
                    f"0x{row.index:04X}",
                    str(row.sub),
                    row.name,
                    comparison.shown(row.left),
                    comparison.shown(row.right),
                    "" if row.state == comparison.SAME else row.state,
                ]
            )
            item.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
            self.table.addTopLevelItem(item)
        for column in (0, 1, 3, 4, 5):
            self.table.resizeColumnToContents(column)

    def _copy(self) -> None:
        if not self.rows:
            self.ctx.warn("Nothing has been compared yet.")
            return
        left = self._readings["left"].label
        right = self._readings["right"].label
        QGuiApplication.clipboard().setText(
            comparison.as_text(self.rows, left, right, self.differences_only.isChecked())
        )
        self.ctx.log("Comparison copied to the clipboard")

    # --- what it was set to last time ----------------------------------------------------
    def save(self) -> None:
        self.ctx.settings.set(
            f"compare.{self.key}",
            {
                "left": self.left.state(),
                "right": self.right.state(),
                "differences_only": self.differences_only.isChecked(),
            },
        )

    def restore(self) -> None:
        stored = self.ctx.settings.get(f"compare.{self.key}", {})
        if not isinstance(stored, dict):
            return
        self.left.restore(stored.get("left", {}))
        self.right.restore(stored.get("right", {}))
        self.differences_only.setChecked(bool(stored.get("differences_only", True)))


API_VERSION = 1
NAME = "CANopen DCF compare"
VERSION = "1.0"
DESCRIPTION = "Two CANopen configurations side by side: file, device or EDS."


def register(app) -> None:
    """Several, because comparing is one question at a time.

    The unit that fails against the one beside it, and the file it was built
    from against the file it shipped with, are two comparisons somebody wants
    open together rather than one they keep re-entering.
    """
    app.add_pane(
        "main",
        "CANopen DCF compare",
        lambda name: CompareView(app.canopen, app.ctx, key=name),
        area="right",
        several=True,
    )
