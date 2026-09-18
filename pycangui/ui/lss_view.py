# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""LSS pane: commission a node -- give it a node-ID and a bit rate before it
has a usable node-ID at all.

The order of operations matters, so the pane is laid out as the workflow:

1. **Select** the node to configure. *Fastscan* finds an unconfigured node and
   discovers its identity; *Select by address* addresses a known one; *All
   nodes* is the blunt instrument for a bench with exactly one device on it.
2. **Configure** its node-ID and bit rate.
3. **Store** so the settings survive a power cycle, then leave configuration
   state. A node-ID change takes effect after a reset; a bit rate change takes
   effect on *Activate* (every node switches together) or after a reset.
"""

from __future__ import annotations

from PySide6.QtCore import Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from pycangui.canopen.manager import LSS_BIT_TIMINGS, CanopenManager
from pycangui.core.context import Context

WARNING = (
    "LSS talks to nodes that have no node-ID yet. 'All nodes' is only safe "
    "with a single device connected."
)


def _hex_edit(text: str = "") -> QLineEdit:
    edit = QLineEdit(text)
    edit.setFont(QFont("Consolas", 9))
    edit.setFixedWidth(90)
    edit.setPlaceholderText("hex")
    return edit


class LssView(QWidget):
    def __init__(self, manager: CanopenManager, ctx: Context) -> None:
        super().__init__()
        self.manager = manager
        self.ctx = ctx

        # --- 1: select the node -------------------------------------------
        select = QGroupBox("1. Select the node to configure")
        grid = QGridLayout(select)
        self.vendor = _hex_edit()
        self.product = _hex_edit()
        self.revision = _hex_edit()
        self.serial = _hex_edit()
        for column, (label, edit) in enumerate(
            (
                ("Vendor ID", self.vendor),
                ("Product code", self.product),
                ("Revision", self.revision),
                ("Serial", self.serial),
            )
        ):
            grid.addWidget(QLabel(label), 0, column * 2)
            grid.addWidget(edit, 0, column * 2 + 1)
        fastscan = QPushButton("Fastscan")
        fastscan.setToolTip(
            "Discover an unconfigured node's identity by binary search and\n"
            "leave it in configuration state"
        )
        fastscan.clicked.connect(manager.lss_fast_scan)
        select_btn = QPushButton("Select by address")
        select_btn.setToolTip("Put the node with exactly this identity into configuration state")
        select_btn.clicked.connect(self._select)
        all_btn = QPushButton("All nodes")
        all_btn.setToolTip("Put every node into configuration state (single device only)")
        all_btn.clicked.connect(lambda: manager.lss_switch_global(True))
        inquire = QPushButton("Inquire")
        inquire.setToolTip("Read back the identity and node-ID of the selected node")
        inquire.clicked.connect(manager.lss_inquire)
        buttons = QHBoxLayout()
        for b in (fastscan, select_btn, all_btn, inquire):
            buttons.addWidget(b)
        buttons.addStretch()
        grid.addLayout(buttons, 1, 0, 1, 8)

        # --- 2: configure ---------------------------------------------------
        configure = QGroupBox("2. Configure")
        row = QHBoxLayout(configure)
        row.addWidget(QLabel("Node-ID"))
        self.node_id = QSpinBox()
        self.node_id.setRange(1, 127)
        self.node_id.setValue(1)
        row.addWidget(self.node_id)
        set_id = QPushButton("Set node-ID")
        set_id.setToolTip(
            "Give the selected node a new node-ID. It does not take effect\n"
            "until the node is reset, and is only kept if you store it."
        )
        set_id.clicked.connect(lambda: manager.lss_set_node_id(self.node_id.value()))
        row.addWidget(set_id)
        row.addSpacing(16)
        row.addWidget(QLabel("Bit rate"))
        self.bit_rate = QComboBox()
        for index, rate in LSS_BIT_TIMINGS:
            self.bit_rate.addItem(f"{rate // 1000} kbit/s", index)
        self.bit_rate.setCurrentIndex(2)  # 500 kbit/s
        row.addWidget(self.bit_rate)
        set_rate = QPushButton("Set bit rate")
        set_rate.setToolTip(
            "Change the node's bit rate. It keeps the old one until Activate,\n"
            "and every node on the bus has to be changed together or the ones\n"
            "left behind will no longer be able to talk to it."
        )
        set_rate.clicked.connect(lambda: manager.lss_set_bit_timing(self.bit_rate.currentData()))
        row.addWidget(set_rate)
        activate = QPushButton("Activate")
        activate.setToolTip(
            "Every node switches to the new bit rate after the delay.\n"
            "Reconnect this tool at the new rate afterwards."
        )
        activate.clicked.connect(lambda: manager.lss_activate_bit_timing(100))
        row.addWidget(activate)
        row.addStretch()

        # --- 3: finish -------------------------------------------------------
        finish = QGroupBox("3. Store and finish")
        row3 = QHBoxLayout(finish)
        store = QPushButton("Store configuration")
        store.setToolTip("Make the node-ID and bit rate survive a power cycle")
        store.clicked.connect(manager.lss_store)
        done = QPushButton("Back to waiting state")
        done.setToolTip("Leave configuration state; a node-ID change applies after a reset")
        done.clicked.connect(lambda: manager.lss_switch_global(False))
        row3.addWidget(store)
        row3.addWidget(done)
        row3.addStretch()

        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setFont(QFont("Consolas", 9))
        self.output.setMaximumBlockCount(500)

        # The three steps go in a scroll area of their own: this pane is one tab
        # among several, and without it its natural height became the minimum
        # height of the whole CANopen pane, which then could not be made smaller.
        note = QLabel(WARNING)
        note.setWordWrap(True)
        steps = QWidget()
        steps_layout = QVBoxLayout(steps)
        steps_layout.setContentsMargins(0, 0, 0, 0)
        steps_layout.addWidget(note)
        steps_layout.addWidget(select)
        side_by_side = QHBoxLayout()  # steps 2 and 3 are short: keep them on one row
        side_by_side.addWidget(configure, 1)
        side_by_side.addWidget(finish)
        steps_layout.addLayout(side_by_side)
        steps_layout.addStretch()

        scroll = QScrollArea()
        scroll.setWidget(steps)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setMinimumHeight(0)

        self.output.setMinimumHeight(40)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.addWidget(scroll, 1)
        layout.addWidget(self.output)

        manager.lss_result.connect(self._append)
        manager.lss_found.connect(self._on_found)

    # --- actions --------------------------------------------------------------
    def _select(self) -> None:
        try:
            values = [
                int(edit.text() or "0", 16)
                for edit in (self.vendor, self.product, self.revision, self.serial)
            ]
        except ValueError as exc:
            self._append(f"LSS: {exc}")
            return
        self.manager.lss_select(*values)

    @Slot(object)
    def _on_found(self, identity) -> None:
        """Fill the address boxes in from a fastscan or inquire."""
        for edit, value in (
            (self.vendor, identity.vendor_id),
            (self.product, identity.product_code),
            (self.revision, identity.revision),
            (self.serial, identity.serial),
        ):
            edit.setText("" if value is None else f"{value:08X}")
        if identity.node_id:
            self.node_id.setValue(identity.node_id)

    @Slot(str)
    def _append(self, text: str) -> None:
        self.output.appendPlainText(text)
        self.ctx.log(text)
