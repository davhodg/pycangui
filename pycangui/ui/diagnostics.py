# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""What Help > Diagnostics says beyond versions and channels, and its window.

The report is for answering "what is pycangui doing?" on the spot, and for a
bug report afterwards. Above all it says what is being sent: every cyclic
message, whose it is, and whether pycangui or the adapter keeps its time --
since a frame on the bus that nothing on screen accounts for is the hardest
sort of fault to find, and asking the window is the only way to tell one of
pycangui's own from a device's.

Each section is built on its own and a failure costs only that section: a
report that cannot be made because one part of the window is in an odd state
is no use at exactly the moment it is wanted. Every section says "nothing"
rather than being left out, so a missing line is never mistaken for an empty
one. Names, files and identifiers are listed; hook contents, passwords and
signal values are not, beyond the bytes of a cyclic message.
"""

from __future__ import annotations

import threading
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QPlainTextEdit, QVBoxLayout, QWidget

from pycangui.canopen import node_ids
from pycangui.core.filters import describe as filters_described
from pycangui.core.pause import described
from pycangui.ui import folders, fonts

#: How far back "sent and received lately" looks in the trace.
RECENT_S = 5.0
#: How many of the identifiers pycangui sent most are named.
BUSIEST = 5
#: CANopen's SYNC, which the canopen library starts on the bus itself.
SYNC_ID = 0x080
#: How many warnings and errors the window keeps for the report.
RECENT_PROBLEMS = 20


def _id(can_id: int, extended: bool = False) -> str:
    return f"0x{can_id:08X}" if extended else f"0x{can_id:03X}"


def _bytes(data: bytes) -> str:
    return bytes(data).hex(" ").upper() or "(no data)"


# --- what is being sent -------------------------------------------------------------------
def sending(window) -> list[str]:
    """Each channel's cyclic messages, whose they are, and what went out lately."""
    lines: list[str] = []
    lately = _lately(window)
    for name in window.channels.names():
        bus = window.channels.get(name)
        if bus is None or not bus.is_connected:
            lines.append(f"{name}: not connected")
            continue
        started = bus.cyclic()
        behind = bus.untracked_tasks()
        lines.append(f"{name}: {len(started) + len(behind or [])} cyclic message(s)")
        for entry in started:
            lines.append(
                f"  {_id(entry.can_id, entry.extended)}  {_bytes(entry.data)}  every "
                f"{entry.period_s * 1000:g} ms, timed by {entry.timed_by} -- "
                f"{entry.owner or 'owner not given'}"
            )
        if behind is None:
            lines.append("  any started on the bus directly: not known to this python-can")
        for task in behind or []:
            messages = getattr(task, "messages", None) or []
            first = messages[0] if messages else None
            period = getattr(task, "period", None)
            shown = (
                f"{_id(first.arbitration_id, first.is_extended_id)}  {_bytes(first.data)}"
                if first is not None
                else "a cyclic message"
            )
            every = f"  every {period * 1000:g} ms" if period else ""
            if (
                first is not None
                and first.arbitration_id == SYNC_ID
                and window.canopen.sync_running
            ):
                whose = "CANopen SYNC"
            else:
                whose = "started on the bus directly: a library, the console or a script"
            lines.append(f"  {shown}{every} -- {whose}")
        sent, received, busiest = lately.get(name, (0, 0, Counter()))
        most = ", ".join(f"{_id(i)} x{n}" for i, n in busiest.most_common(BUSIEST))
        lines.append(
            f"  last {RECENT_S:g} s in the trace: {sent} sent, {received} received"
            + (f"; sent most: {most}" if most else "")
        )
        if bus.filters:
            lines.append(f"  message filter: {filters_described(bus.filters)}")
    return lines


def _lately(window) -> dict[str, tuple[int, int, Counter]]:
    """Per channel, frames sent and received in the last few seconds, and the
    identifiers sent most, from what the trace has captured."""
    buses = [window.channels.get(n) for n in window.channels.names()]
    now = next((bus.now() for bus in buses if bus is not None and bus.is_connected), None)
    if now is None:
        return {}
    out: dict[str, tuple[int, int, Counter]] = {}
    for frame in window.trace.model.frames_since(now - RECENT_S):
        sent, received, busiest = out.get(frame.channel, (0, 0, Counter()))
        if frame.rx:
            received += 1
        else:
            sent += 1
            busiest[frame.can_id] += 1
        out[frame.channel] = (sent, received, busiest)
    return out


def pause(window) -> list[str]:
    holders = window.ctx.pause.holders
    return [f"paused, held by {described(holders)}" if holders else "running, not paused"]


def polling(window) -> list[str]:
    """Every pane reading something over and over: custom panes, plugin panes,
    and objects polled from the CANopen object dictionary."""
    lines = []
    for name in window.panes.names():
        poller = getattr(window.panes.view(name), "poller", None)
        if poller is None or not getattr(poller, "running", False):
            continue
        dock = window.panes.docks.get(name)
        title = dock.windowTitle() if dock is not None else name
        held = " (paused)" if getattr(poller, "paused", False) else ""
        lines.append(f"{title}: {poller.hz:g} Hz asked, {poller.achieved:.1f} Hz achieved{held}")
    for name in window.panes.names():
        # The CANopen pane's object dictionary, polling the objects ticked there.
        poll = getattr(window.panes.view(name), "sdo_poll", None)
        if poll is None or not poll.running:
            continue
        dock = window.panes.docks.get(name)
        title = dock.windowTitle() if dock is not None else name
        nodes = ", ".join(str(n) for n in poll.nodes()) or "none that can answer"
        held = " (paused)" if poll.paused else ""
        lines.append(
            f"{title} object dictionary: nodes {nodes}; {poll.hz:g} Hz asked, "
            f"{poll.achieved:.1f} Hz achieved{held}"
        )
    return lines


def simulated(window) -> list[str]:
    return [
        f"{node.name} on {', '.join(node.channels)} at {node.rate_hz:g} Hz"
        + (" (told of a pause)" if getattr(node, "paused", False) else "")
        for node in window.nodes.running()
    ]


# --- the protocols ------------------------------------------------------------------------
def canopen(window) -> list[str]:
    manager = window.canopen
    sync = getattr(manager, "_sync_settings", None)
    lines = [
        f"node ids heard: {node_ids.text(manager.node_ids)}; SDO timeout "
        f"{manager.sdo_timeout_s * 1000:g} ms, {manager.sdo_retries} retries",
        f"SYNC: {f'every {sync[0] * 1000:g} ms' if manager.sync_running and sync else 'off'}"
        f"; TIME: {'on' if manager.time_running else 'off'}"
        f"; unasked reads {'held by a pause' if manager.paused else 'not held'}",
    ]
    held = getattr(manager, "_held", {})
    for node_id in manager.nodes():
        state = manager.nmt_states.get(node_id, "not heard")
        if node_id in manager.lost_nodes:
            state = f"lost ({state})"
        eds = manager.eds_path(node_id)
        tpdo, rpdo = manager.read_from_node(node_id)
        pdos = "PDOs read from it" if tpdo and rpdo else "PDOs as the EDS has them"
        waiting = f"; waiting: {', '.join(sorted(held[node_id]))}" if held.get(node_id) else ""
        lines.append(
            f"node {node_id}: {state}; {Path(eds).name if eds else 'no EDS'}; {pdos}{waiting}"
        )
    if not manager.nodes():
        lines.append("no nodes")
    return lines


def uds(window) -> list[str]:
    manager = window.uds
    config = manager.config
    present = getattr(manager, "_tp_timer", None)
    ticking = present is not None and present.isActive()
    return [
        f"{'open' if manager.is_open else 'not open'}; request {_id(config.tx_id)}, response "
        f"{_id(config.rx_id)}, functional {_id(config.functional_id)}",
        f"Tester Present: {f'every {config.tester_present_s:g} s' if ticking else 'off'}; "
        f"transfer {'running' if manager.is_transferring else 'not running'}",
    ]


def j1939(window) -> list[str]:
    manager = window.j1939
    ca = getattr(manager, "ca", None)
    hold = getattr(manager, "_hold_timer", None)
    address = getattr(ca, "device_address", None)
    return [
        f"address: {f'0x{address:02X}' if isinstance(address, int) else 'none claimed'}; "
        f"DM13 hold: {'on' if hold is not None and hold.isActive() else 'off'}"
    ]


def xcp(window) -> list[str]:
    manager = window.xcp
    return [
        f"{manager.protocol}: {'connected' if manager.is_connected else 'not connected'}; "
        f"engine {manager.component_name}; {len(getattr(manager, '_polled', {}))} value(s) "
        f"polled{' (paused)' if manager.paused else ''}"
    ]


# --- what is loaded, and what went wrong ------------------------------------------------
def loaded(window) -> list[str]:
    lines = [
        f"DBC {Path(path).name}: {len(db.messages)} messages"
        for path, db in window.dbc.databases.items()
    ] or ["no DBC loaded"]
    if (a2l := getattr(window.xcp, "a2l", None)) is not None:
        lines.append(f"A2L: {getattr(a2l, 'name', '') or 'loaded'}")
    for record in window.plugins.loaded.values():
        state = "error" if record.error else ("on" if record.active else "off")
        lines.append(f"plugin {record.label} {record.version}: {state}")
    lines.append(f"workspace: {window.ctx.workspace} ({window.ctx.workspace_dir})")
    return lines


def problems(window) -> list[str]:
    return [
        f"{when} {level}: {text.splitlines()[0] if text else ''}"
        for when, level, text in getattr(window, "recent_problems", ())
    ]


def threads(_window) -> list[str]:
    return sorted(
        f"{thread.name}{' (daemon)' if thread.daemon else ''}" for thread in threading.enumerate()
    )


#: In the order the report gives them: what is sent first.
SECTIONS: tuple[tuple[str, Callable], ...] = (
    ("Sending", sending),
    ("Automatic traffic", pause),
    ("Polling", polling),
    ("Simulated nodes", simulated),
    ("CANopen", canopen),
    ("UDS", uds),
    ("J1939", j1939),
    ("XCP / CCP", xcp),
    ("Loaded", loaded),
    (f"Last {RECENT_PROBLEMS} warnings and errors", problems),
    ("Threads", threads),
)


def section(title: str, build: Callable, window) -> list[str]:
    """One section, whatever happens in building it."""
    try:
        body = build(window) or ["nothing"]
    except Exception as exc:  # one odd corner must not cost the whole report
        body = [f"could not be read: {type(exc).__name__}: {exc}"]
    return ["", title, *(f"  {line}" for line in body)]


def sections(window) -> list[str]:
    lines: list[str] = []
    for title, build in SECTIONS:
        lines += section(title, build, window)
    return lines


# --- the window -----------------------------------------------------------------------------
class DiagnosticsDialog(QDialog):
    """The report, to read, refresh, copy or save. One, brought forward when asked again."""

    def __init__(self, parent: QWidget, make: Callable[[], str], ctx) -> None:
        super().__init__(parent)
        self.setWindowTitle("Diagnostics")
        self.resize(860, 640)
        self._make = make
        self._ctx = ctx
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setFont(fonts.mono())
        self.text.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.text.setAccessibleName("Diagnostics report")
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        refresh = buttons.addButton("Refresh", QDialogButtonBox.ActionRole)
        refresh.setToolTip("Make the report again, as things are now.")
        refresh.clicked.connect(self.refresh)
        copy = buttons.addButton("Copy", QDialogButtonBox.ActionRole)
        copy.setToolTip("Put the whole report on the clipboard, for a bug report.")
        copy.clicked.connect(self.copy_to_clipboard)
        save = buttons.addButton("Save...", QDialogButtonBox.ActionRole)
        save.setToolTip("Write the report to a text file.")
        save.clicked.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.text)
        layout.addWidget(buttons)
        self.refresh()

    def report(self) -> str:
        return self.text.toPlainText()

    def refresh(self) -> None:
        self.text.setPlainText(self._make())

    def copy_to_clipboard(self) -> None:
        QGuiApplication.clipboard().setText(self.report())

    def save(self) -> None:
        path = folders.save_file(
            self,
            self._ctx,
            folders.EXPORT,
            "Save diagnostics",
            "Text (*.txt);;All files (*)",
            self._ctx.user_dir,
            suggested=folders.stamped("pycangui-diagnostics", ".txt"),
        )
        if not path:
            return
        try:
            Path(path).write_text(self.report() + "\n", encoding="utf-8")
        except OSError as exc:
            self._ctx.error(f"Could not write {path}: {exc}")
            return
        self._ctx.log(f"Diagnostics written to {path}")
