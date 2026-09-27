# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""J1939 pane: nodes seen (with NAME from address claims), faults per node
(DM1 active, DM2 previously active), address claim for the tester, requests
from a list of the useful ones or any typed PGN, sending, and stopping the
network's broadcasts (DM13)."""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pycangui.core.context import Context
from pycangui.j1939 import CLEARING, GLOBAL, REQUESTABLE, Dm1, Name, pgn_label
from pycangui.j1939.manager import J1939Manager
from pycangui.ui.confirm import Confirmations
from pycangui.ui.persist import remember

ROLE_SA = Qt.UserRole


def _typed_hex(combo: QComboBox) -> int:
    """The number a picker shows first, whether chosen from its list or typed."""
    words = combo.currentText().split()
    if not words:
        raise ValueError("nothing entered")
    return int(words[0], 16)


def _picker(entries, width: int) -> QComboBox:
    """A list of the useful choices that still takes anything typed."""
    combo = QComboBox()
    combo.setEditable(True)
    combo.setInsertPolicy(QComboBox.NoInsert)
    combo.lineEdit().setFont(QFont("Consolas", 9))
    for number, name in entries:
        combo.addItem(f"{number:0{width}X}  {name}", number)
    return combo


class J1939View(QWidget):
    def __init__(
        self, manager: J1939Manager, ctx: Context, confirm: Confirmations | None = None
    ) -> None:
        super().__init__()
        self.manager = manager
        self.ctx = ctx
        self.confirm = confirm or Confirmations()
        mono = QFont("Consolas", 9)

        # --- tester controls -------------------------------------------------
        ctl = QGroupBox("Tester")
        self.address = QSpinBox()
        self.address.setRange(0, 0xFD)
        self.address.setValue(0xF9)
        self.address.setDisplayIntegerBase(16)
        self.address.setPrefix("0x")
        # Which address this tester claims is a decision about the network,
        # not about this session.
        remember(ctx, "j1939.address", self.address)
        self.claim_btn = QPushButton("Claim address")
        self.claim_btn.setToolTip(
            "Take this address on the bus (J1939-81). Needed before pycangui\n"
            "can be sent messages of its own or send multi-packet ones."
        )
        self.claim_btn.setCheckable(True)
        self.claim_btn.toggled.connect(self._toggle_claim)
        claims = QPushButton("Request address claims")
        claims.setToolTip(
            "Ask every node to say who it is. They answer with their NAME,\n"
            "which fills the node list without waiting for them to speak."
        )
        claims.clicked.connect(self.manager.request_address_claims)

        # A list of the requests worth having, which still takes any PGN
        # typed in hex: most of what a real network carries is on no list.
        self.req_pgn = _picker(REQUESTABLE, 4)
        self.req_pgn.setToolTip(
            "What to ask for. The diagnostic messages and identification are\n"
            "listed; any other PGN can be typed in hex. DM3 and DM11 clear a\n"
            "node's faults, and ask first. Answers are shown in the fault\n"
            "table or the Event Log. A node answers a long one to a single\n"
            "tester only once it has claimed an address."
        )
        remember(ctx, "j1939.request_pgn", self.req_pgn)
        self.req_dest = _picker(((GLOBAL, "Global"),), 2)
        self.req_dest.setToolTip("Who to ask: every node, or one of those seen")
        req_btn = QPushButton("Request")
        req_btn.setToolTip("Ask for the PGN on the left (a Request, PGN 59904)")
        req_btn.clicked.connect(self._request)

        self.send_pgn = QLineEdit("FF10")
        self.send_pgn.setFont(mono)
        self.send_pgn.setFixedWidth(70)
        self.send_dest = QLineEdit("FF")
        self.send_dest.setFont(mono)
        self.send_dest.setFixedWidth(40)
        self.send_prio = QSpinBox()
        self.send_prio.setRange(0, 7)
        self.send_prio.setValue(6)
        self.send_data = QLineEdit("01 02 03 04 05 06 07 08")
        self.send_data.setFont(mono)
        send_btn = QPushButton("Send PGN")
        send_btn.clicked.connect(self._send)

        # DM13: held for as long as the button stays down, since nodes start
        # broadcasting again by themselves a few seconds after the last hold.
        self.broadcast_btn = QPushButton("Stop broadcasts")
        self.broadcast_btn.setCheckable(True)
        self.broadcast_btn.setToolTip(
            "DM13: tell the nodes on this network to stop broadcasting, and\n"
            "keep telling them until Start -- they begin again by themselves\n"
            "a few seconds after the last hold. Quietens a busy bus, or one\n"
            "being flashed. Sent to the node chosen in Request's 'to'."
        )
        self.broadcast_btn.toggled.connect(self._toggle_broadcasts)

        # A row each, packed left. In one grid the rows shared columns, so the
        # request's destination sat wherever the send row's priority did.
        rows = QVBoxLayout(ctl)
        for items, stretch in (
            (("Address", self.address, self.claim_btn, claims), None),
            (("Request", self.req_pgn, "to", self.req_dest, req_btn), self.req_pgn),
            (
                (
                    "Send PGN",
                    self.send_pgn,
                    "to",
                    self.send_dest,
                    "prio",
                    self.send_prio,
                    self.send_data,
                    send_btn,
                ),
                self.send_data,
            ),
            (("Broadcasts", self.broadcast_btn), None),
        ):
            row = QHBoxLayout()
            for item in items:
                if isinstance(item, str):
                    row.addWidget(QLabel(item))
                else:
                    row.addWidget(item, 1 if item is stretch else 0)
            if stretch is None:
                row.addStretch()
            rows.addLayout(row)

        # --- nodes ---------------------------------------------------------------
        self.nodes = QTreeWidget()
        self.nodes.setHeaderLabels(["SA", "NAME", "Decoded", "Last seen"])
        self.nodes.setRootIsDecorated(False)
        self.nodes.setFont(mono)
        self.nodes.header().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.nodes.header().setStretchLastSection(True)

        # --- faults ----------------------------------------------------------------
        self.faults = QTreeWidget()
        self.faults.setHeaderLabels(
            ["SA", "Kind", "Lamps", "SPN", "SPN name", "FMI", "Failure mode", "OC"]
        )
        self.faults.setRootIsDecorated(False)
        self.faults.setFont(mono)
        self.faults.header().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.faults.header().setStretchLastSection(True)

        # --- messages ----------------------------------------------------------------
        self.messages = QTreeWidget()
        self.messages.setHeaderLabels(["PGN", "SA", "Len", "Data"])
        self.messages.setRootIsDecorated(False)
        self.messages.setFont(mono)
        self.messages.header().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.messages.header().setStretchLastSection(True)
        self._message_items: dict[tuple[int, int], QTreeWidgetItem] = {}

        splitter = QSplitter(Qt.Vertical)
        for title, w in (
            ("Nodes", self.nodes),
            ("Faults (DM1 active, DM2 previously active)", self.faults),
            ("Messages (reassembled)", self.messages),
        ):
            box = QWidget()
            v = QVBoxLayout(box)
            v.setContentsMargins(0, 0, 0, 0)
            v.addWidget(QLabel(title))
            v.addWidget(w)
            splitter.addWidget(box)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.addWidget(ctl)
        layout.addWidget(splitter, 1)

        manager.node_seen.connect(self._on_node_seen)
        manager.dm1.connect(self._on_dm1)
        manager.dm2.connect(self._on_dm2)
        manager.broadcasts_stopped.connect(self._on_broadcasts_stopped)
        manager.message.connect(self._on_message)
        manager.claimed.connect(self._on_claimed)
        manager.log.connect(ctx.log)
        manager.problem.connect(ctx.warn)
        manager._bus.disconnected.connect(self.clear)
        self._age_timer = QTimer(self, interval=1000, timeout=self._refresh_ages)
        self._age_timer.start()

    # --- tester -------------------------------------------------------------------
    @Slot(bool)
    def _toggle_claim(self, on: bool) -> None:
        if on:
            self.manager.claim_address(self.address.value())
        else:
            self.manager.release_address()

    @Slot(int)
    def _on_claimed(self, address: int) -> None:
        self.claim_btn.blockSignals(True)
        self.claim_btn.setChecked(address != 0xFE)
        self.claim_btn.setText(f"Release 0x{address:02X}" if address != 0xFE else "Claim address")
        self.claim_btn.blockSignals(False)

    def _request(self) -> None:
        try:
            pgn, destination = _typed_hex(self.req_pgn), _typed_hex(self.req_dest)
        except ValueError as exc:
            self.ctx.log(f"J1939 request: {exc}")
            return
        # Asked of the PGN, not of the list entry, so a clear typed in by
        # number asks as well.
        if (which := CLEARING.get(pgn)) is not None:
            who = "every node" if destination == GLOBAL else f"node {destination:02X}"
            text = (
                f"Ask {who} to clear its {which} faults. What is cleared is gone: "
                "the faults and what was recorded with them."
            )
            if not self.confirm.ask(self, f"j1939.clear.{pgn}", "Clear faults?", text):
                return
        # From the tester's own address, claimed first if it is not yet.
        self.manager.as_tester(
            self.address.value(), lambda: self.manager.request_pgn(pgn, destination)
        )

    def _toggle_broadcasts(self, stop: bool) -> None:
        if not stop:
            if self.manager.holding_broadcasts:
                self.manager.start_broadcasts()
            return
        try:
            destination = _typed_hex(self.req_dest)
        except ValueError:
            destination = GLOBAL
        text = (
            "Tell the nodes on this network to stop broadcasting, and keep them "
            "stopped until Start. Anything relying on their messages -- other "
            "controllers, a dashboard -- goes without them until then."
        )
        # Up until it has happened: the manager says when it has.
        self._on_broadcasts_stopped(False)
        if self.confirm.ask(self, "j1939.dm13", "Stop broadcasts?", text):
            self.manager.as_tester(
                self.address.value(), lambda: self.manager.stop_broadcasts(destination)
            )

    @Slot(bool)
    def _on_broadcasts_stopped(self, stopped: bool) -> None:
        self.broadcast_btn.blockSignals(True)
        self.broadcast_btn.setChecked(stopped)
        self.broadcast_btn.setText("Start broadcasts (holding)" if stopped else "Stop broadcasts")
        self.broadcast_btn.blockSignals(False)

    def _send(self) -> None:
        try:
            data = bytes.fromhex(self.send_data.text().replace(",", " "))
            pgn, destination = int(self.send_pgn.text(), 16), int(self.send_dest.text(), 16)
            priority = self.send_prio.value()
            self.manager.as_tester(
                self.address.value(),
                lambda: self.manager.send_pgn(pgn, data, destination, priority),
            )
        except ValueError as exc:
            self.ctx.log(f"J1939 send: {exc}")

    # --- tables ---------------------------------------------------------------------
    def _node_item(self, sa: int) -> QTreeWidgetItem:
        for i in range(self.nodes.topLevelItemCount()):
            item = self.nodes.topLevelItem(i)
            if item.data(0, ROLE_SA) == sa:
                return item
        item = QTreeWidgetItem([f"{sa:02X}", "", "", ""])
        item.setData(0, ROLE_SA, sa)
        self.nodes.addTopLevelItem(item)
        return item

    @Slot(int, object)
    def _on_node_seen(self, sa: int, name: Name | None) -> None:
        if self.req_dest.findData(sa) < 0:
            self.req_dest.addItem(f"{sa:02X}  node", sa)
        item = self._node_item(sa)
        if name is not None:
            item.setText(1, f"{name.value:016X}")
            item.setText(2, name.summary())
        item.setText(3, "now")

    def _refresh_ages(self) -> None:
        now = time.monotonic()
        for i in range(self.nodes.topLevelItemCount()):
            item = self.nodes.topLevelItem(i)
            seen = self.manager.last_seen.get(item.data(0, ROLE_SA))
            if seen is not None:
                age = now - seen
                item.setText(3, "now" if age < 1.5 else f"{age:.0f} s ago")

    @Slot(int, object)
    def _on_dm1(self, sa: int, dm1: Dm1) -> None:
        self._show_faults(sa, dm1, "Active")

    @Slot(int, object)
    def _on_dm2(self, sa: int, dm2: Dm1) -> None:
        self._show_faults(sa, dm2, "Previously active")

    def _show_faults(self, sa: int, dm: Dm1, kind: str) -> None:
        """A node's faults of one kind, replacing the ones it sent before."""
        for i in reversed(range(self.faults.topLevelItemCount())):
            item = self.faults.topLevelItem(i)
            if item.data(0, ROLE_SA) == sa and item.text(1) == kind:
                self.faults.takeTopLevelItem(i)
        lamps = dm.lamps()
        if not dm.dtcs:
            none = f"no {kind.lower()} faults"
            item = QTreeWidgetItem([f"{sa:02X}", kind, lamps, "-", none, "", "", ""])
            item.setData(0, ROLE_SA, sa)
            self.faults.addTopLevelItem(item)
        for d in dm.dtcs:
            item = QTreeWidgetItem(
                [
                    f"{sa:02X}",
                    kind,
                    lamps,
                    str(d.spn),
                    self.manager.spn_description(d.spn),
                    str(d.fmi),
                    self.manager.fmi_description(d.fmi),
                    str(d.occurrence),
                ]
            )
            item.setData(0, ROLE_SA, sa)
            self.faults.addTopLevelItem(item)

    @Slot(int, int, int, float, bytes)
    def _on_message(self, priority: int, pgn: int, sa: int, timestamp: float, data: bytes) -> None:
        key = (pgn, sa)
        item = self._message_items.get(key)
        label = self.manager.pgn_name(pgn)
        text = f"{label} ({pgn})" if label else pgn_label(pgn)
        if item is None:
            item = QTreeWidgetItem([text, f"{sa:02X}", "", ""])
            self.messages.addTopLevelItem(item)
            self._message_items[key] = item
        item.setText(2, str(len(data)))
        shown = data[:32].hex(" ").upper() + (" ..." if len(data) > 32 else "")
        if all(32 <= b < 127 for b in data) and len(data) > 8:
            shown += f'  "{data.decode("ascii")}"'
        item.setText(3, shown)

    @Slot()
    def clear(self) -> None:
        self.nodes.clear()
        self.faults.clear()
        self.messages.clear()
        self._message_items.clear()
        self._on_claimed(0xFE)
        while self.req_dest.count() > 1:  # the nodes go with the bus; Global stays
            self.req_dest.removeItem(1)


__all__ = ["GLOBAL", "J1939View"]
