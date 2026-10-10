# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""The CANopen pane's log: everything that happens once rather than over and over.

Every SDO transfer pycangui makes, whatever asked for it; every NMT command it
sends and the SYNC producer starting and stopping; each node's state as its
heartbeat changes it, boot-up included, and its heartbeat going and coming
back; each emergency; each LSS step. Not PDOs, SYNC frames or heartbeats
themselves -- they repeat, and the trace already has them.

A tab of the pane rather than lines in the Event Log, as the UDS pane keeps its
own conversation with an ECU: a custom pane polling a few objects writes
several SDO lines a second, which would bury everything else the Event Log is
for. The Event Log keeps its summary -- an emergency, a node lost -- and this
is the whole record.

Everything is kept, newest few thousand lines, and the ticks only choose what
is shown, so hiding SDO to see the rest and ticking it back loses nothing.
Lines are written a few times a second rather than one at a time, so a busy
poll does not slow the window down.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QTimer, Slot
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pycangui.canopen.sdo_log import SdoRecord
from pycangui.ui import folders, fonts

SDO, NMT, STATE, EMCY, LSS = "SDO", "NMT", "State", "EMCY", "LSS"
#: Each kind, and what its tick says it covers.
KINDS = {
    SDO: "Every SDO read and write pycangui makes, whatever asked for it.",
    NMT: "NMT commands pycangui sends, and the SYNC producer starting and stopping.",
    STATE: "A node's NMT state as its heartbeat changes it, boot-up, and its\n"
    "heartbeat stopping and coming back.",
    EMCY: "Each emergency received. The Emergencies tab keeps them as a table.",
    LSS: "Each LSS step and what came of it.",
}
#: Lines kept; the oldest go first.
KEPT = 5000
#: How often new lines are written, in milliseconds.
WRITE_MS = 200
HIDDEN_KEY = "canopen.log.hidden"
ONLY_KEY = "canopen.log.selected_only"
#: The state a heartbeat says after a node has started or restarted.
BOOT_UP = "INITIALISING"


@dataclass(frozen=True)
class Line:
    at: float  # the bus clock, as the trace's Time column
    node_id: int | None  # None: the whole network
    kind: str
    text: str

    def shown(self) -> str:
        who = "all nodes" if self.node_id in (None, 0) else f"node {self.node_id}"
        return f"{self.at:10.3f}  {who:<9}  {self.kind:<5}  {self.text}"


class CanopenLogView(QWidget):
    def __init__(self, manager, ctx, selected: Callable[[], int | None]) -> None:
        super().__init__()
        self.manager = manager
        self.ctx = ctx
        self._selected = selected
        self._now = manager._bus.now
        self._lines: deque[Line] = deque(maxlen=KEPT)
        self._pending: list[Line] = []
        #: node -> the NMT state its heartbeat last said, to log changes only.
        self._states: dict[int, str] = {}

        hidden = set(ctx.settings.get(HIDDEN_KEY, []) or [])
        self.kinds: dict[str, QCheckBox] = {}
        bar = QHBoxLayout()
        bar.addWidget(QLabel("Show:"))
        for kind, tip in KINDS.items():
            tick = QCheckBox(kind)
            tick.setToolTip(tip)
            tick.setChecked(kind not in hidden)
            tick.toggled.connect(self._filters_changed)
            self.kinds[kind] = tick
            bar.addWidget(tick)
        bar.addSpacing(12)
        self.only_selected = QCheckBox("Selected node only")
        self.only_selected.setToolTip(
            "Leave out the other nodes. What goes to every node -- an NMT\n"
            "command to all, SYNC -- is kept, since it reached this one too."
        )
        self.only_selected.setChecked(bool(ctx.settings.get(ONLY_KEY, False)))
        self.only_selected.toggled.connect(self._filters_changed)
        bar.addWidget(self.only_selected)
        bar.addStretch()
        clear = QPushButton("Clear")
        clear.clicked.connect(self.clear)
        save = QPushButton("Save...")
        save.setToolTip("Write the lines shown to a text file.")
        save.clicked.connect(self._save)
        bar.addWidget(clear)
        bar.addWidget(save)

        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setFont(fonts.mono())
        self.text.setMaximumBlockCount(KEPT)
        self.text.setLineWrapMode(QPlainTextEdit.NoWrap)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.addLayout(bar)
        layout.addWidget(self.text)

        manager.sdo_logged.connect(self._on_sdo)
        manager.nmt_sent.connect(self._on_nmt)
        manager.sync_changed.connect(self._on_sync)
        manager.node_seen.connect(self._on_state)
        manager.node_lost.connect(lambda n: self.add(Line(self._now(), n, STATE, "heartbeat lost")))
        manager.node_back.connect(lambda n: self.add(Line(self._now(), n, STATE, "heartbeat back")))
        manager.emcy.connect(self._on_emcy)
        manager.lss_result.connect(lambda text: self.add(Line(self._now(), None, LSS, text)))
        manager._bus.disconnected.connect(self._on_disconnected)
        self._timer = QTimer(self, interval=WRITE_MS, timeout=self._write)
        self._timer.start()

    # --- what arrives --------------------------------------------------------------
    def add(self, line: Line) -> None:
        self._lines.append(line)
        self._pending.append(line)

    @Slot(object)
    def _on_sdo(self, record: SdoRecord) -> None:
        self.add(Line(record.at, record.node_id, SDO, record.text()))

    @Slot(int, str)
    def _on_nmt(self, node_id: int, command: str) -> None:
        self.add(Line(self._now(), node_id, NMT, f"sent {command}"))

    @Slot(bool, float)
    def _on_sync(self, on: bool, period_s: float) -> None:
        text = (
            f"SYNC producer started, every {period_s * 1000:.0f} ms"
            if on
            else ("SYNC producer stopped")
        )
        self.add(Line(self._now(), None, NMT, text))

    @Slot(int, str)
    def _on_state(self, node_id: int, state: str) -> None:
        """A node's state, when it changes -- not every heartbeat that repeats it."""
        before = self._states.get(node_id)
        if state == before and state != BOOT_UP:
            return
        self._states[node_id] = state
        if state == BOOT_UP:
            text = "boot-up"
        elif before is None:
            text = f"first heard: {state.lower()}" if state.isupper() else state
        elif before == BOOT_UP:
            text = f"after boot-up: {state.lower()}"
        else:
            text = f"{before.lower()} -> {state.lower()}"
        self.add(Line(self._now(), node_id, STATE, text))

    @Slot(object)
    def _on_emcy(self, emcy) -> None:
        said = str(emcy)
        self.add(Line(self._now(), emcy.node_id, EMCY, said.split(": ", 1)[-1]))

    @Slot()
    def _on_disconnected(self) -> None:
        # Whatever a node says next is news, not a repeat of before.
        self._states.clear()
        self.add(Line(self._now(), None, STATE, "channel disconnected"))

    # --- what is shown -------------------------------------------------------------
    def _wanted(self, line: Line) -> bool:
        if not self.kinds[line.kind].isChecked():
            return False
        if self.only_selected.isChecked() and line.node_id not in (None, 0):
            return line.node_id == self._selected()
        return True

    def _write(self) -> None:
        if not self._pending:
            return
        lines, self._pending = self._pending, []
        wanted = [line.shown() for line in lines if self._wanted(line)]
        if not wanted:
            return
        bar = self.text.verticalScrollBar()
        following = bar.value() == bar.maximum()
        self.text.appendPlainText("\n".join(wanted))
        if following:  # keep up with the newest, unless somebody has scrolled up to read
            self.text.moveCursor(QTextCursor.End)

    def flush(self) -> None:
        """Write what has arrived now, rather than on the next tick."""
        self._write()

    def _filters_changed(self) -> None:
        """Show everything kept again, through the new filters."""
        hidden = [kind for kind, tick in self.kinds.items() if not tick.isChecked()]
        self.ctx.settings.set(HIDDEN_KEY, hidden)
        self.ctx.settings.set(ONLY_KEY, self.only_selected.isChecked())
        self._pending.clear()
        self.text.setPlainText(
            "\n".join(line.shown() for line in self._lines if self._wanted(line))
        )
        self.text.moveCursor(QTextCursor.End)

    def node_changed(self) -> None:
        """The selection moved: with *Selected node only* on, that changes what is shown."""
        if self.only_selected.isChecked():
            self._filters_changed()

    @Slot()
    def clear(self) -> None:
        self._lines.clear()
        self._pending.clear()
        self.text.clear()

    def _save(self) -> None:
        path = folders.save_file(
            self,
            self.ctx,
            folders.EXPORT,
            "Save the CANopen log",
            "Text (*.txt);;All files (*)",
            self.ctx.user_dir,
            suggested=folders.stamped("canopen_log", ".txt"),
        )
        if not path:
            return
        self.flush()
        try:
            Path(path).write_text(self.text.toPlainText() + "\n", encoding="utf-8")
        except OSError as exc:
            self.ctx.error(f"CANopen log: could not write {path}: {exc}")
            return
        self.ctx.log(f"CANopen log saved to {path}")
