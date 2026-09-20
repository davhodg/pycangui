# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""CANopen settings: what is set once, rather than done.

The CANopen pane is for doing things to nodes -- NMT, SYNC, reading and
writing -- and every setting put beside those made them harder to find. So
what is set once and left alone lives on a dialog of its own, in two halves:
what applies to every node, and what applies to one.

Kept in the workspace, because a product's nodes are configured the way that
product is, not the way the last one was.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
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
HEARTBEATS_KEY = "canopen.heartbeat_timeouts"
SYNC_KEY = "canopen.sync_period_ms"
IDENTIFY_KEY = "canopen.identify_automatically"

#: How often SYNC goes out while it is switched on. A rate, which is a
#: settled choice about a bus, rather than something to decide each time
#: the button is pressed.
DEFAULT_SYNC_MS = 100
MIN_SYNC_MS = 1
MAX_SYNC_MS = 60000

MIN_TIMEOUT_MS = 10
MAX_TIMEOUT_MS = 60000
MAX_RETRIES = 5
#: CANopen's own identifiers are 11-bit.
MAX_COB_ID = 0x7FF
#: A heartbeat timeout shorter than this would call a node lost between two
#: frames of an ordinary bus; longer than an hour is no longer watching it.
MIN_HEARTBEAT_MS = 100
MAX_HEARTBEAT_MS = 3_600_000

NODE, REQUEST, RESPONSE, HEARTBEAT = range(4)
COLUMNS = ("Node", "SDO request", "SDO response", "Heartbeat timeout")

SYNC_TIP = (
    "How often SYNC (0x080) goes out while the button on the pane is on.\n"
    "It belongs here rather than beside the button: it is a fact about the\n"
    "bus, agreed once, not a decision to take every time synchronous PDOs\n"
    "are wanted."
)
IDENTIFY_TIP = (
    "Read 0x1018 and 0x1000 from a node the first time it is heard, which\n"
    "is what matches an EDS to it and what names it in the list.\n"
    "\n"
    "It is the only thing pycangui sends a node without being asked -- five\n"
    "SDO uploads as the row appears -- so switching it off makes 'nothing\n"
    "goes out unless I ask for it' true, which is what watching somebody\n"
    "else's live machine wants. The cost is that a new node stays Node <id>\n"
    "with no EDS until Identify or Load EDS is pressed."
)
TIMEOUT_TIP = (
    "How long to wait for a node to answer each SDO request, for every SDO\n"
    "pycangui sends. 300 ms is the canopen library's own default."
)
RETRIES_TIP = "How many more times to ask when a node does not answer in time."
HEARTBEAT_TIP = (
    "In milliseconds: how long without a heartbeat before this node is called\n"
    "lost. Blank works it out, from the producer time in 0x1017 or the\n"
    "heartbeats the node actually sends, as three of them."
)
PER_NODE_NOTE = (
    "Only for a node that needs something other than what pycangui works out: "
    "an SDO server off the channel CiA 301 predefines (requests to 0x600 + node, "
    "answers on 0x580 + node), or a heartbeat timeout of its own. Leave the "
    "COB-IDs as they are and the timeout blank for whichever does not apply."
)


def predefined(node_id: int) -> tuple[int, int]:
    return SDO_REQUEST_BASE + node_id, SDO_RESPONSE_BASE + node_id


@dataclass
class CanopenSettings:
    timeout_ms: float = DEFAULT_SDO_TIMEOUT_S * 1000
    retries: int = DEFAULT_SDO_RETRIES
    #: node -> (request, response), for the nodes off the predefined channel only.
    channels: dict[int, tuple[int, int]] = field(default_factory=dict)
    #: node -> milliseconds, for the nodes whose timeout is not worked out.
    heartbeat_timeouts: dict[int, float] = field(default_factory=dict)
    sync_period_ms: float = DEFAULT_SYNC_MS
    #: Whether a node is asked who it is as soon as it is heard. On, because
    #: an EDS is matched from the answer and nothing else would match one.
    identify: bool = True


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


def why_not_heartbeat(node_id: int, milliseconds: float) -> str:
    """Why this heartbeat timeout will not do, or "" if it will."""
    if not 1 <= node_id <= 127:
        return f"node {node_id} is not a node id (1 to 127)"
    if not MIN_HEARTBEAT_MS <= milliseconds <= MAX_HEARTBEAT_MS:
        return (
            f"node {node_id}: a heartbeat timeout of {milliseconds:g} ms is outside "
            f"{MIN_HEARTBEAT_MS} ms to {MAX_HEARTBEAT_MS // 60000} minutes"
        )
    return ""


def _integer(value) -> int | None:
    try:
        return int(value, 0) if isinstance(value, str) else int(value)
    except (TypeError, ValueError):
        return None


def _milliseconds(value) -> float | None:
    try:
        return float(value)
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
    the timeout, or every other node's settings, with it.
    """
    out = CanopenSettings()
    if (timeout := _milliseconds(ctx.settings.get(TIMEOUT_KEY))) is not None:
        out.timeout_ms = min(max(timeout, MIN_TIMEOUT_MS), MAX_TIMEOUT_MS)
    if (retries := _integer(ctx.settings.get(RETRIES_KEY))) is not None:
        out.retries = min(max(retries, 0), MAX_RETRIES)
    if (sync := _milliseconds(ctx.settings.get(SYNC_KEY))) is not None:
        out.sync_period_ms = min(max(sync, MIN_SYNC_MS), MAX_SYNC_MS)
    out.identify = bool(ctx.settings.get(IDENTIFY_KEY, True))
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
    saved = ctx.settings.get(HEARTBEATS_KEY, {})
    if isinstance(saved, dict):
        for node, value in saved.items():
            node_id, milliseconds = _integer(node), _milliseconds(value)
            if node_id is None or milliseconds is None or why_not_heartbeat(node_id, milliseconds):
                continue
            out.heartbeat_timeouts[node_id] = milliseconds
    return out


def save(ctx: Context, settings: CanopenSettings) -> None:
    ctx.settings.set(TIMEOUT_KEY, settings.timeout_ms)
    ctx.settings.set(RETRIES_KEY, settings.retries)
    ctx.settings.set(SYNC_KEY, settings.sync_period_ms)
    ctx.settings.set(IDENTIFY_KEY, settings.identify)
    # In hex, the way a COB-ID is spoken, so the file reads as the dialog does.
    ctx.settings.set(
        CHANNELS_KEY,
        {
            str(node_id): {"request": f"0x{request:03X}", "response": f"0x{response:03X}"}
            for node_id, (request, response) in sorted(settings.channels.items())
        },
    )
    ctx.settings.set(
        HEARTBEATS_KEY,
        {
            str(node_id): int(ms) if float(ms).is_integer() else ms
            for node_id, ms in sorted(settings.heartbeat_timeouts.items())
        },
    )


def apply(manager, settings: CanopenSettings) -> None:
    manager.set_sdo_timing(settings.timeout_ms / 1000, settings.retries)
    manager.set_sdo_channels(settings.channels)
    manager.set_heartbeat_timeouts(
        {node_id: ms / 1000 for node_id, ms in settings.heartbeat_timeouts.items()}
    )


@dataclass
class _Row:
    node_id: int
    request: int
    response: int
    heartbeat_ms: float | None


class CanopenSettingsDialog(QDialog):
    """Every node's SDO timing, and what one node needs that the rest do not."""

    def __init__(
        self, parent: QWidget | None, settings: CanopenSettings, selected_node: int | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("CANopen settings")
        self.resize(600, 440)
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
        self.sync_period = QDoubleSpinBox()
        self.sync_period.setRange(MIN_SYNC_MS, MAX_SYNC_MS)
        self.sync_period.setDecimals(0)
        self.sync_period.setSingleStep(10)
        self.sync_period.setSuffix(" ms")
        self.sync_period.setValue(settings.sync_period_ms)
        self.sync_period.setToolTip(SYNC_TIP)
        self.identify = QCheckBox("Identify a node when it is first heard")
        self.identify.setChecked(settings.identify)
        self.identify.setToolTip(IDENTIFY_TIP)
        # "Every node" rather than "SDO, every node": the SYNC period is
        # not an SDO setting, and it moved in here the moment the rate
        # stopped being a box beside the button. What each row is about is
        # said by the row.
        every = QGroupBox("Every node")
        form = QFormLayout(every)
        form.addRow("SDO timeout:", self.timeout)
        form.addRow("SDO retries:", self.retries)
        form.addRow("SYNC period:", self.sync_period)
        form.addRow("", self.identify)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.horizontalHeaderItem(HEARTBEAT).setToolTip(HEARTBEAT_TIP)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        for node_id in sorted({*settings.channels, *settings.heartbeat_timeouts}):
            request, response = settings.channels.get(node_id, predefined(node_id))
            self._add_row(node_id, request, response, settings.heartbeat_timeouts.get(node_id))
        add = QPushButton("Add")
        add.setToolTip(
            "A row for the selected node, or the first node not listed, with nothing\n"
            "changed yet: its predefined SDO channel and its heartbeat timeout worked out."
        )
        add.clicked.connect(self._add)
        remove = QPushButton("Remove")
        remove.setToolTip("Put this node back on what pycangui works out for itself.")
        remove.clicked.connect(self._remove)
        row = QHBoxLayout()
        row.addWidget(add)
        row.addWidget(remove)
        row.addStretch()
        note = QLabel(PER_NODE_NOTE)
        note.setWordWrap(True)
        per_node = QGroupBox("Per node")
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
    def _add_row(
        self, node_id: int, request: int, response: int, heartbeat_ms: float | None = None
    ) -> int:
        at = self.table.rowCount()
        self.table.insertRow(at)
        heartbeat = "" if heartbeat_ms is None else f"{heartbeat_ms:g}"
        texts = (str(node_id), f"0x{request:03X}", f"0x{response:03X}", heartbeat)
        for column, text in enumerate(texts):
            item = QTableWidgetItem(text)
            if column == HEARTBEAT:
                item.setToolTip(HEARTBEAT_TIP)
            self.table.setItem(at, column, item)
        return at

    def _add(self) -> None:
        taken = {row.node_id for row in self._rows()[0]}
        if self._selected is not None and self._selected not in taken:
            node_id = self._selected
        else:
            node_id = next((n for n in range(1, 128) if n not in taken), None)
        if node_id is None:
            return
        at = self._add_row(node_id, *predefined(node_id))
        self.table.setCurrentCell(at, REQUEST)

    def _remove(self) -> None:
        if (at := self.table.currentRow()) >= 0:
            self.table.removeRow(at)

    def _text(self, at: int, column: int) -> str:
        item = self.table.item(at, column)
        return item.text().strip() if item is not None else ""

    def _rows(self) -> tuple[list[_Row], list[str]]:
        """The rows that will do, and what is wrong with the ones that will not."""
        rows: list[_Row] = []
        problems: list[str] = []
        for at in range(self.table.rowCount()):
            node_text = self._text(at, NODE)
            node_id = int(node_text) if node_text.isdigit() else None
            if node_id is None:
                problems.append(f"row {at + 1}: {node_text!r} is not a node id")
                continue
            request_text, response_text = self._text(at, REQUEST), self._text(at, RESPONSE)
            request, response = _cob_id(request_text), _cob_id(response_text)
            if request is None or response is None:
                bad = request_text if request is None else response_text
                problems.append(f"node {node_id}: {bad!r} is not a COB-ID")
                continue
            if reason := why_not(node_id, request, response):
                problems.append(reason)
                continue
            heartbeat_text = self._text(at, HEARTBEAT)
            heartbeat_ms = None
            if heartbeat_text:
                heartbeat_ms = _milliseconds(heartbeat_text.removesuffix("ms").strip())
                if heartbeat_ms is None:
                    problems.append(
                        f"node {node_id}: {heartbeat_text!r} is not a heartbeat timeout in ms"
                    )
                    continue
                if reason := why_not_heartbeat(node_id, heartbeat_ms):
                    problems.append(reason)
                    continue
            rows.append(_Row(node_id, request, response, heartbeat_ms))
        seen: set[int] = set()
        for row in rows:
            if row.node_id in seen:
                problems.append(f"node {row.node_id} is listed twice")
            seen.add(row.node_id)
        return rows, problems

    def _accept(self) -> None:
        _rows, problems = self._rows()
        if problems:
            messages.warning(self, "These settings will not do", "\n".join(problems))
            return
        self.accept()

    def settings(self) -> CanopenSettings:
        """What was chosen. Whatever a row leaves as it was is no override."""
        rows, _problems = self._rows()
        return CanopenSettings(
            timeout_ms=self.timeout.value(),
            retries=self.retries.value(),
            sync_period_ms=self.sync_period.value(),
            identify=self.identify.isChecked(),
            channels={
                row.node_id: (row.request, row.response)
                for row in rows
                if (row.request, row.response) != predefined(row.node_id)
            },
            heartbeat_timeouts={
                row.node_id: row.heartbeat_ms for row in rows if row.heartbeat_ms is not None
            },
        )
