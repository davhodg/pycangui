"""J1939 pane: nodes seen (with NAME from address claims), active faults
(DM1) per node, address claim for the tester, PGN request and send."""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QGridLayout,
    QGroupBox,
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
from pycangui.j1939 import GLOBAL, Dm1, Name, pgn_label
from pycangui.j1939.manager import J1939Manager
from pycangui.ui.persist import remember

ROLE_SA = Qt.UserRole


class J1939View(QWidget):
    def __init__(self, manager: J1939Manager, ctx: Context) -> None:
        super().__init__()
        self.manager = manager
        self.ctx = ctx
        mono = QFont("Consolas", 9)

        # --- tester controls -------------------------------------------------
        ctl = QGroupBox("Tester")
        g = QGridLayout(ctl)
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
            "Take this address on the bus (J1939-81).  Needed before pycangui\n"
            "can be sent messages of its own or send multi-packet ones."
        )
        self.claim_btn.setCheckable(True)
        self.claim_btn.toggled.connect(self._toggle_claim)
        g.addWidget(QLabel("Address"), 0, 0)
        g.addWidget(self.address, 0, 1)
        g.addWidget(self.claim_btn, 0, 2)

        self.req_pgn = QLineEdit("FEEB")
        self.req_pgn.setFont(mono)
        self.req_pgn.setFixedWidth(70)
        self.req_dest = QLineEdit("FF")
        self.req_dest.setFont(mono)
        self.req_dest.setFixedWidth(40)
        req_btn = QPushButton("Request PGN")
        req_btn.setToolTip("Ask a node to send a parameter group (request PGN 59904)")
        req_btn.clicked.connect(self._request)
        g.addWidget(QLabel("PGN (hex)"), 1, 0)
        g.addWidget(self.req_pgn, 1, 1)
        g.addWidget(QLabel("to"), 1, 2, Qt.AlignRight)
        g.addWidget(self.req_dest, 1, 3)
        g.addWidget(req_btn, 1, 4)

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
        g.addWidget(QLabel("Send PGN"), 2, 0)
        g.addWidget(self.send_pgn, 2, 1)
        g.addWidget(QLabel("to"), 2, 2, Qt.AlignRight)
        g.addWidget(self.send_dest, 2, 3)
        g.addWidget(QLabel("prio"), 2, 4, Qt.AlignRight)
        g.addWidget(self.send_prio, 2, 5)
        g.addWidget(self.send_data, 2, 6)
        g.addWidget(send_btn, 2, 7)
        g.setColumnStretch(6, 1)

        # --- nodes ---------------------------------------------------------------
        self.nodes = QTreeWidget()
        self.nodes.setHeaderLabels(["SA", "NAME", "Decoded", "Last seen"])
        self.nodes.setRootIsDecorated(False)
        self.nodes.setFont(mono)
        self.nodes.header().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.nodes.header().setStretchLastSection(True)

        # --- faults ----------------------------------------------------------------
        self.faults = QTreeWidget()
        self.faults.setHeaderLabels(["SA", "Lamps", "SPN", "SPN name", "FMI", "Failure mode", "OC"])
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
            ("Active faults (DM1)", self.faults),
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
        manager.message.connect(self._on_message)
        manager.claimed.connect(self._on_claimed)
        manager.log.connect(ctx.log)
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
            self.manager.request_pgn(int(self.req_pgn.text(), 16), int(self.req_dest.text(), 16))
        except ValueError as exc:
            self.ctx.log(f"J1939 request: {exc}")

    def _send(self) -> None:
        try:
            data = bytes.fromhex(self.send_data.text().replace(",", " "))
            self.manager.send_pgn(
                int(self.send_pgn.text(), 16),
                data,
                int(self.send_dest.text(), 16),
                self.send_prio.value(),
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
        for i in reversed(range(self.faults.topLevelItemCount())):
            if self.faults.topLevelItem(i).data(0, ROLE_SA) == sa:
                self.faults.takeTopLevelItem(i)
        lamps = dm1.lamps()
        if not dm1.dtcs:
            item = QTreeWidgetItem([f"{sa:02X}", lamps, "-", "no active faults", "", "", ""])
            item.setData(0, ROLE_SA, sa)
            self.faults.addTopLevelItem(item)
        for d in dm1.dtcs:
            item = QTreeWidgetItem(
                [
                    f"{sa:02X}",
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


__all__ = ["GLOBAL", "J1939View"]
