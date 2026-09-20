# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Setting a channel's acceptance filter, and being honest about what it does.

Every other way of narrowing what pycangui shows keeps the frames. This one
throws them away before they arrive -- see `core/filters.py` -- so the dialog
is built around saying so rather than around the table. The warning is above
the rules, not below them; the *would this get through* line exists because
the mistake this makes easy is filtering out the replies from the very node
you are talking to, and finding out ten minutes later.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pycangui.core.channels import Channels
from pycangui.core.context import Context
from pycangui.core.filters import Rule, accepts, from_saved, full_mask, to_saved

COLUMNS = ("ID", "Mask", "29-bit")


def key(channel: str) -> str:
    return f"channels.{channel}.filters"


def saved_rules(ctx: Context, channel: str) -> list[Rule]:
    return from_saved(ctx.settings.get(key(channel), []))


def remember(ctx: Context, channel: str, rules: list[Rule]) -> None:
    ctx.settings.set(key(channel), to_saved(rules))


def restore(ctx: Context, channels: Channels) -> None:
    """Give every channel the rules the workspace kept for it.

    Called when the window is built and whenever a channel is added, so a
    workspace made for a busy bus comes back filtered the way it was left.
    That a saved filter is still in force is exactly the thing that could
    waste somebody's afternoon, which is why the status bar says so in
    words and will not stop saying it.
    """
    for name in channels.names():
        bus = channels.get(name)
        if bus is not None:
            bus.set_filters(saved_rules(ctx, name))


WARNING = (
    "Frames that do not match are dropped before pycangui sees them. They are "
    "not traced, decoded, counted, recorded or exported, and no protocol pane "
    "can answer them -- a filter that leaves out a node's SDO replies stops "
    "CANopen talking to it."
)
EMPTY = "No rules: every frame on this channel is accepted. This is the normal state."
MASK_TIP = (
    "A frame is accepted when its id, masked, equals this id masked. All\n"
    "bits set is one id exactly, which is the default; clearing the low\n"
    "bits widens it to a block, so 0x180/0x780 is every id from 0x180 to\n"
    "0x1FF -- which on CANopen is TPDO1 from any node."
)
ID_TIP = "The identifier to accept, in hex."
EXTENDED_TIP = (
    "A 29-bit identifier. Standard and extended ids are separate: a rule\n"
    "for one never accepts the other, so a bus carrying both needs a rule\n"
    "for each."
)
TRY_TIP = (
    "Type an id and see whether these rules would let it through, before\n"
    "finding out on a bus. Add x for a 29-bit id: 18FF50E5 x."
)
GETS_THROUGH = "0x{id:X} gets through"
BLOCKED = "0x{id:X} would be dropped"


def parse_try(text: str) -> tuple[int, bool] | None:
    """An id and whether it is extended, from what somebody typed.

    A trailing x, e or ext means 29-bit. Returns None for anything that is
    not a number, which the caller shows as nothing rather than as an error:
    a half-typed id is not a mistake.
    """
    words = text.strip().lower().replace(",", " ").split()
    if not words:
        return None
    extended = False
    if len(words) > 1 and words[-1] in ("x", "e", "ext", "extended"):
        extended, words = True, words[:-1]
    body = words[0]
    if body.endswith("x") and len(body) > 1 and not body.startswith("0x"):
        extended, body = True, body[:-1]
    try:
        can_id = int(body, 16)
    except ValueError:
        return None
    if not 0 <= can_id <= full_mask(extended):
        return None
    return can_id, extended


class MessageFilterDialog(QDialog):
    """The acceptance rules for one channel."""

    def __init__(self, channel: str, rules: list[Rule], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Message filter: {channel}")
        self.setMinimumWidth(460)

        warning = QLabel(WARNING)
        warning.setWordWrap(True)
        warning.setStyleSheet("color: #a05000; font-weight: bold;")

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(list(COLUMNS))
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        for rule in rules:
            self._add_row(rule)
        # Connected after the rows are in: filling the table fires it for
        # every cell, and the answer would be recomputed three times a row.
        self.table.itemChanged.connect(self._on_item_changed)

        add = QPushButton("Add")
        add.clicked.connect(self._add_clicked)
        remove = QPushButton("Remove")
        remove.clicked.connect(self._remove)
        clear = QPushButton("Accept everything")
        clear.setToolTip("Take the filter off: every frame on this channel arrives again.")
        clear.clicked.connect(self._clear)
        buttons = QHBoxLayout()
        buttons.addWidget(add)
        buttons.addWidget(remove)
        buttons.addStretch()
        buttons.addWidget(clear)

        self.trial = QLineEdit()
        self.trial.setPlaceholderText("would this get through? e.g. 581, or 18FF50E5 x")
        self.trial.setToolTip(TRY_TIP)
        self.trial.textChanged.connect(self._on_trial_changed)
        self.verdict = QLabel("")
        trial_row = QHBoxLayout()
        trial_row.addWidget(self.trial, 1)
        trial_row.addWidget(self.verdict, 1)

        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        self.summary.setEnabled(False)

        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(warning)
        layout.addWidget(self.table, 1)
        layout.addLayout(buttons)
        layout.addLayout(trial_row)
        layout.addWidget(self.summary)
        layout.addWidget(box)
        self._recheck()

    # --- the table -------------------------------------------------------------------
    def _add_row(self, rule: Rule) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        width = 8 if rule.extended else 3
        identifier = QTableWidgetItem(f"{rule.can_id:0{width}X}")
        identifier.setToolTip(ID_TIP)
        mask = QTableWidgetItem(f"{rule.mask:0{width}X}")
        mask.setToolTip(MASK_TIP)
        extended = QTableWidgetItem()
        extended.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        extended.setCheckState(Qt.Checked if rule.extended else Qt.Unchecked)
        extended.setToolTip(EXTENDED_TIP)
        # Silently, then one look afterwards. Each setItem fires
        # itemChanged, so a row put in three cells at a time is read back
        # twice while it is still half there.
        blocked = self.table.blockSignals(True)
        for column, item in enumerate((identifier, mask, extended)):
            self.table.setItem(row, column, item)
        self.table.blockSignals(blocked)

    def _add_clicked(self) -> None:
        self._add_row(Rule(0, full_mask(False), False))
        self._recheck()
        last = self.table.rowCount() - 1
        self.table.setCurrentCell(last, 0)
        self.table.editItem(self.table.item(last, 0))

    def _remove(self) -> None:
        for row in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(row)
        self._recheck()

    def _clear(self) -> None:
        self.table.setRowCount(0)
        self._recheck()

    def _on_item_changed(self, _item: QTableWidgetItem) -> None:
        self._recheck()

    def _on_trial_changed(self, _text: str) -> None:
        self._recheck()

    def rules(self) -> list[Rule]:
        """What is in the table, skipping any row that is not a number yet.

        A row being typed into is not an error, and neither is an empty
        mask: an id on its own means that id exactly, which is what
        somebody who typed one id and stopped meant.

        A row with a cell not filled in yet is skipped for the same
        reason. That is not hypothetical -- a row is built one cell at a
        time -- and reading the table is something this does on every
        keystroke, so it answers for whatever is there rather than
        insisting the table be whole.
        """
        out: list[Rule] = []
        for row in range(self.table.rowCount()):
            cells = [self.table.item(row, column) for column in range(len(COLUMNS))]
            if any(cell is None for cell in cells):
                continue
            extended = cells[2].checkState() == Qt.Checked
            limit = full_mask(extended)
            try:
                can_id = int(cells[0].text().strip() or "-", 16)
                mask_text = cells[1].text().strip()
                mask = int(mask_text, 16) if mask_text else limit
            except ValueError:
                continue
            if 0 <= can_id <= limit:
                out.append(Rule(can_id, min(max(mask, 0), limit), extended))
        return out

    # --- saying what it will do ---------------------------------------------------------
    def _recheck(self) -> None:
        rules = self.rules()
        if not rules:
            self.summary.setText(EMPTY)
        else:
            joined = ", ".join(r.text() for r in rules)
            self.summary.setText(f"Accepting {len(rules)} rule(s): {joined}")
        asked = parse_try(self.trial.text())
        if asked is None:
            self.verdict.setText("")
            return
        can_id, extended = asked
        through = accepts(rules, can_id, extended)
        self.verdict.setText((GETS_THROUGH if through else BLOCKED).format(id=can_id))
        self.verdict.setStyleSheet("color: #1b6b2f;" if through else "color: #b3261e;")


def ask(channel: str, rules: list[Rule], parent: QWidget | None = None) -> list[Rule] | None:
    """Run the dialog. Returns the new rules, or None where it was cancelled."""
    dialog = MessageFilterDialog(channel, rules, parent)
    if dialog.exec() != QDialog.Accepted:
        return None
    return dialog.rules()
