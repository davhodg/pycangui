# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""CANopen settings: what is set once, rather than done.

The CANopen pane is for doing things to nodes -- NMT, SYNC, reading and
writing -- and every setting put beside those made them harder to find.  So
what is set once and left alone lives on a dialog of its own, in two halves:
what applies to every node, and what applies to one.  The second half is where
per-node heartbeat and identification settings belong too, when they come.

Kept in the workspace, because a product's nodes are configured the way that
product is, not the way the last one was.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pycangui.canopen.manager import (
    DEFAULT_SDO_RETRIES,
    DEFAULT_SDO_TIMEOUT_S,
    SDO_REQUEST_BASE,
    SDO_RESPONSE_BASE,
)
from pycangui.core.context import Context
from pycangui.ui import messages

TIMEOUT_KEY = "canopen.sdo_timeout_ms"
RETRIES_KEY = "canopen.sdo_retries"
CHANNELS_KEY = "canopen.sdo_channels"

MIN_TIMEOUT_MS = 10
MAX_TIMEOUT_MS = 60000
MAX_RETRIES = 5
#: CANopen's own identifiers are 11-bit.
MAX_COB_ID = 0x7FF
COLUMNS = ("Node", "Request COB-ID", "Response COB-ID")

TIMEOUT_TIP = (
    "How long to wait for a node to answer each SDO request, for every SDO\n"
    "pycangui sends.  300 ms is the canopen library's own default."
)
RETRIES_TIP = "How many more times to ask when a node does not answer in time."
CHANNEL_NOTE = (
    "Only for a node whose SDO server is not on the channel CiA 301 predefines, "
    "requests to 0x600 + node and answers on 0x580 + node.  Its heartbeat, "
    "emergencies and NMT are not affected."
)


def predefined(node_id: int) -> tuple[int, int]:
    return SDO_REQUEST_BASE + node_id, SDO_RESPONSE_BASE + node_id


@dataclass
class CanopenSettings:
    timeout_ms: float = DEFAULT_SDO_TIMEOUT_S * 1000
    retries: int = DEFAULT_SDO_RETRIES
    #: node -> (request, response), for the nodes off the predefined channel only.
    channels: dict[int, tuple[int, int]] = field(default_factory=dict)


def why_not(node_id: int, request: int, response: int) -> str:
    """Why this channel will not do, or "" if it will."""
    if not 1 <= node_id <= 127:
        return f"node {node_id} is not a node id (1 to 127)"
    for name, cob_id in (("request", request), ("response", response)):
        if not 1 <= cob_id <= MAX_COB_ID:
            return f"node {node_id}: the {name} COB-ID 0x{cob_id:X} is not an 11-bit identifier"
    if request == response:
        return f"node {node_id}: a request and its answer cannot share 0x{request:03X}"
    return ""


def _integer(value) -> int | None:
    try:
        return int(value, 0) if isinstance(value, str) else int(value)
    except (TypeError, ValueError):
        return None


def _cob_id(text: str) -> int | None:
    """Hex, with or without its 0x, because that is how a COB-ID is written."""
    text = text.strip().lower()
    try:
        return int(text[2:] if text.startswith("0x") else text, 16)
    except ValueError:
        return None


def load(ctx: Context) -> CanopenSettings:
    """What the workspace says, forgiving a hand-edited settings.json.

    A bad entry costs that entry: a typo in one node's channel should not take
    the timeout, or every other node's channel, with it.
    """
    out = CanopenSettings()
    try:
        if (timeout := ctx.settings.get(TIMEOUT_KEY)) is not None:
            out.timeout_ms = min(max(float(timeout), MIN_TIMEOUT_MS), MAX_TIMEOUT_MS)
    except (TypeError, ValueError):
        pass
    if (retries := _integer(ctx.settings.get(RETRIES_KEY))) is not None:
        out.retries = min(max(retries, 0), MAX_RETRIES)
    saved = ctx.settings.get(CHANNELS_KEY, {})
    if isinstance(saved, dict):
        for node, pair in saved.items():
            node_id = _integer(node)
            if node_id is None or not isinstance(pair, dict):
                continue
            request, response = _integer(pair.get("request")), _integer(pair.get("response"))
            if request is None or response is None or why_not(node_id, request, response):
                continue
            out.channels[node_id] = (request, response)
    return out


def save(ctx: Context, settings: CanopenSettings) -> None:
    ctx.settings.set(TIMEOUT_KEY, settings.timeout_ms)
    ctx.settings.set(RETRIES_KEY, settings.retries)
    # In hex, the way a COB-ID is spoken, so the file reads as the dialog does.
    ctx.settings.set(
        CHANNELS_KEY,
        {
            str(node_id): {"request": f"0x{request:03X}", "response": f"0x{response:03X}"}
            for node_id, (request, response) in sorted(settings.channels.items())
        },
    )


def apply(manager, settings: CanopenSettings) -> None:
    manager.set_sdo_timing(settings.timeout_ms / 1000, settings.retries)
    manager.set_sdo_channels(settings.channels)


class CanopenSettingsDialog(QDialog):
    """Every node's SDO timing, and the SDO channel of any node off the usual one."""

    def __init__(
        self, parent: QWidget | None, settings: CanopenSettings, selected_node: int | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("CANopen settings")
        self.resize(520, 420)
        self._selected = selected_node

        self.timeout = QDoubleSpinBox()
        self.timeout.setRange(MIN_TIMEOUT_MS, MAX_TIMEOUT_MS)
        self.timeout.setDecimals(0)
        self.timeout.setSingleStep(100)
        self.timeout.setSuffix(" ms")
        self.timeout.setValue(settings.timeout_ms)
        self.timeout.setToolTip(TIMEOUT_TIP)
        self.retries = QSpinBox()
        self.retries.setRange(0, MAX_RETRIES)
        self.retries.setValue(settings.retries)
        self.retries.setToolTip(RETRIES_TIP)
        every = QGroupBox("SDO, every node")
        form = QFormLayout(every)
        form.addRow("Timeout:", self.timeout)
        form.addRow("Retries:", self.retries)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        for node_id, (request, response) in sorted(settings.channels.items()):
            self._add_row(node_id, request, response)
        add = QPushButton("Add")
        add.setToolTip(
            "A row for the selected node, or the first node not listed, on its\n"
            "predefined channel -- change the COB-IDs to where its server is."
        )
        add.clicked.connect(self._add)
        remove = QPushButton("Remove")
        remove.setToolTip("Put this node back on the predefined channel.")
        remove.clicked.connect(self._remove)
        row = QHBoxLayout()
        row.addWidget(add)
        row.addWidget(remove)
        row.addStretch()
        note = QLabel(CHANNEL_NOTE)
        note.setWordWrap(True)
        per_node = QGroupBox("SDO channel, per node")
        inside = QVBoxLayout(per_node)
        inside.addWidget(note)
        inside.addWidget(self.table)
        inside.addLayout(row)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(every)
        layout.addWidget(per_node, 1)
        layout.addWidget(buttons)

    # --- the table ---------------------------------------------------------------------
    def _add_row(self, node_id: int, request: int, response: int) -> int:
        at = self.table.rowCount()
        self.table.insertRow(at)
        for column, text in enumerate((str(node_id), f"0x{request:03X}", f"0x{response:03X}")):
            self.table.setItem(at, column, QTableWidgetItem(text))
        return at

    def _add(self) -> None:
        taken = {node_id for node_id, _q, _r in self._rows()[0]}
        if self._selected is not None and self._selected not in taken:
            node_id = self._selected
        else:
            node_id = next((n for n in range(1, 128) if n not in taken), None)
        if node_id is None:
            return
        at = self._add_row(node_id, *predefined(node_id))
        self.table.setCurrentCell(at, 1)

    def _remove(self) -> None:
        if (at := self.table.currentRow()) >= 0:
            self.table.removeRow(at)

    def _rows(self) -> tuple[list[tuple[int, int, int]], list[str]]:
        """The rows that will do, and what is wrong with the ones that will not."""
        rows: list[tuple[int, int, int]] = []
        problems: list[str] = []
        for at in range(self.table.rowCount()):
            node_text, request_text, response_text = (
                (self.table.item(at, column).text() if self.table.item(at, column) else "").strip()
                for column in range(len(COLUMNS))
            )
            node_id = int(node_text) if node_text.isdigit() else None
            if node_id is None:
                problems.append(f"row {at + 1}: {node_text!r} is not a node id")
                continue
            request, response = _cob_id(request_text), _cob_id(response_text)
            if request is None or response is None:
                bad = request_text if request is None else response_text
                problems.append(f"node {node_id}: {bad!r} is not a COB-ID")
                continue
            if reason := why_not(node_id, request, response):
                problems.append(reason)
                continue
            rows.append((node_id, request, response))
        seen: set[int] = set()
        for node_id, _q, _r in rows:
            if node_id in seen:
                problems.append(f"node {node_id} is listed twice")
            seen.add(node_id)
        return rows, problems

    def _accept(self) -> None:
        _rows, problems = self._rows()
        if problems:
            messages.warning(self, "These settings will not do", "\n".join(problems))
            return
        self.accept()

    def settings(self) -> CanopenSettings:
        """What was chosen.  A row left on the predefined channel is no override."""
        rows, _problems = self._rows()
        return CanopenSettings(
            timeout_ms=self.timeout.value(),
            retries=self.retries.value(),
            channels={
                node_id: (request, response)
                for node_id, request, response in rows
                if (request, response) != predefined(node_id)
            },
        )
