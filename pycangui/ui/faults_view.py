# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""What the selected node says about its own faults, asked rather than heard.

The Emergencies tab beside this one is an arrival log: it holds what was
broadcast while pycangui was listening, and nothing else. Plug in after a
controller has faulted and it is empty, which reads as "no faults" and is
not. This tab asks the node instead -- the error register it is holding now,
and the codes it kept -- so the answer does not depend on having been there.

Two of the three objects are optional in CiA 301, so the pane is built around
their absence rather than assuming them: a node with no 0x1003 says so in
place of the list, and its Clear button is switched off. "This node has
nowhere to keep that" is a different answer from "no faults", and showing an
empty list for both would be the wrong one half the time.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pycangui.canopen import faults
from pycangui.canopen.manager import CanopenManager
from pycangui.core.context import Context

NOTHING_READ = "Nothing read yet -- press Read."
NO_NODE = "No node selected."
NO_FAULT = "No error: the node says it is not faulted."
FAULTED = "Faulted: {text}"
NONE_STORED = "The node is holding no stored errors."
NONE_ACTIVE = "Nothing active: the device says nothing is wrong with it now."
ACTIVE_TIP = (
    "What this device says is wrong now, from\n"
    "hooks/canopen.py::active_faults. CiA 301 has no object for it: 0x1001\n"
    "gives categories rather than faults, so a device that can list them\n"
    "does it its own way."
)
NOT_HELD = "This node has no stored error list (0x1003), so there is nothing to show or clear."

READ_TIP = (
    "Ask the node for its error register (0x1001), its manufacturer status\n"
    "register (0x1002) and the errors it kept (0x1003). The last two are\n"
    "optional in CiA 301, so a node may have neither."
)
CLEAR_TIP = (
    "Write 0 to 0x1003 sub 0, which is how CiA 301 says to empty the node's\n"
    "own list. It does not touch the Emergencies tab: what pycangui saw and\n"
    "what the node kept are two different records."
)
REGISTER_TIP = (
    "0x1001, mandatory in CiA 301, so every node has one. Non-zero means the\n"
    "node considers itself faulted now -- which a stored error does not, since\n"
    "that is history."
)
STATUS_TIP = "0x1002, optional, and its meaning is the maker's alone."
STORED_TIP = (
    "0x1003: the emergency codes the node kept, newest first. The high word\n"
    "of each entry is manufacturer-specific and often a sub-code."
)
#: Loud, and only for the line that says a node is in error now.
FAULT_STYLE = "color: #b3261e; font-weight: bold;"

#: Roughly six rows and a header, in pixels. A device with more active
#: faults than that has something to say at length, and the list scrolls.
ACTIVE_ROWS_SHOWN = 150


class FaultsView(QWidget):
    def __init__(self, manager: CanopenManager, ctx: Context) -> None:
        super().__init__()
        self.manager = manager
        self.ctx = ctx
        self._node: int | None = None

        self.read_btn = QPushButton("Read")
        self.read_btn.setToolTip(READ_TIP)
        self.read_btn.clicked.connect(self._read)
        self.clear_btn = QPushButton("Clear stored errors")
        self.clear_btn.setToolTip(CLEAR_TIP)
        self.clear_btn.clicked.connect(self._clear)

        bar = QHBoxLayout()
        self.heading = QLabel(NO_NODE)
        bar.addWidget(self.heading)
        bar.addStretch()
        bar.addWidget(self.read_btn)
        bar.addWidget(self.clear_btn)

        self.state = QLabel(NOTHING_READ)
        self.state.setWordWrap(True)
        self.state.setToolTip(REGISTER_TIP)
        self.status = QLabel("")
        self.status.setToolTip(STATUS_TIP)
        #: Only shown for a device whose hook lists them: with no hook there
        #: is nothing to put here, and an empty box would read as "nothing
        #: wrong" on a device that simply cannot say.
        self.active = QTreeWidget()
        self.active.setHeaderLabels(["Code", "Description"])
        self.active.setRootIsDecorated(False)
        self.active.setFont(QFont("Consolas", 9))
        self.active.header().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.active.header().setStretchLastSection(True)
        self.active.setToolTip(ACTIVE_TIP)
        # Capped, and it scrolls past that. A tree asks for a great deal of
        # height by default, and this one holds two or three rows: left to
        # ask, it filled the box and pushed the kept list off the bottom.
        self.active.setMaximumHeight(ACTIVE_ROWS_SHOWN)
        self.active_note = QLabel("")
        self.active_note.setWordWrap(True)
        self.active_note.setEnabled(False)

        now_box = QGroupBox("What the node says now")
        now_inside = QVBoxLayout(now_box)
        now_inside.addWidget(self.state)
        now_inside.addWidget(self.status)
        now_inside.addWidget(self.active_note)
        now_inside.addWidget(self.active)

        self.stored = QTreeWidget()
        self.stored.setHeaderLabels(["#", "Code", "Description", "Manufacturer"])
        self.stored.setRootIsDecorated(False)
        self.stored.setFont(QFont("Consolas", 9))
        self.stored.header().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.stored.header().setStretchLastSection(True)
        self.stored.setToolTip(STORED_TIP)
        self.note = QLabel(NOTHING_READ)
        self.note.setWordWrap(True)
        self.note.setEnabled(False)
        kept_box = QGroupBox("What the node kept")
        kept_inside = QVBoxLayout(kept_box)
        kept_inside.addWidget(self.note)
        kept_inside.addWidget(self.stored, 1)

        layout = QVBoxLayout(self)
        layout.addLayout(bar)
        layout.addWidget(now_box)
        layout.addWidget(kept_box, 1)

        manager.fault_state.connect(self.on_fault_state)
        self.set_node(None)

    # --- which node ------------------------------------------------------------------
    def set_node(self, node_id: int | None) -> None:
        """Follow the node list's selection, and show what is already known.

        A fault state is state rather than an event, so coming back to a node
        read a minute ago shows that reading again instead of an empty pane.
        It is not read automatically: an SDO per stored entry is a real cost
        on somebody's bus, and clicking through a node list is not a reason
        to pay it.
        """
        self._node = node_id
        self.heading.setText(NO_NODE if node_id is None else f"Node {node_id}")
        for button in (self.read_btn, self.clear_btn):
            button.setEnabled(node_id is not None)
        known = self.manager.fault_states.get(node_id) if node_id is not None else None
        if known is None:
            self._blank()
        else:
            self.show_state(known)

    def _blank(self) -> None:
        self.state.setText(NO_NODE if self._node is None else NOTHING_READ)
        self.state.setStyleSheet("")
        self.status.setText("")
        self.active.clear()
        self._show_active(None)
        self.note.setText(NO_NODE if self._node is None else NOTHING_READ)
        self.stored.clear()

    # --- reading it ------------------------------------------------------------------
    def _read(self) -> None:
        if self._node is not None:
            self.manager.read_faults(self._node)

    def _clear(self) -> None:
        if self._node is not None:
            self.manager.clear_stored_errors(self._node)

    def _show_active(self, active: list[faults.StoredError] | None) -> None:
        """The list of what is wrong now, for a device that can say.

        Hidden altogether where nothing answered. An empty box under a
        heading reads as "nothing is wrong", and on a device that cannot
        list its faults that would be a claim nobody made.
        """
        self.active.clear()
        self.active.setVisible(bool(active))
        self.active_note.setVisible(active is not None and not active)
        if active is None:
            return
        if not active:
            self.active_note.setText(NONE_ACTIVE)
            return
        for error in active:
            self.active.addTopLevelItem(QTreeWidgetItem([f"{error.code:04X}", error.description]))

    @Slot(object)
    def on_fault_state(self, state: faults.FaultState) -> None:
        if state.node_id == self._node:
            self.show_state(state)

    def show_state(self, state: faults.FaultState) -> None:
        if not state.says(faults.ERROR_REGISTER):
            self.state.setText(faults.missing_text(faults.ERROR_REGISTER).capitalize())
            self.state.setStyleSheet("")
        elif state.faulted:
            self.state.setText(FAULTED.format(text=state.register_text))
            self.state.setStyleSheet(FAULT_STYLE)
        else:
            self.state.setText(NO_FAULT)
            self.state.setStyleSheet("")

        if not state.says(faults.MANUFACTURER_STATUS):
            self.status.setText(faults.missing_text(faults.MANUFACTURER_STATUS).capitalize())
        else:
            value = state.manufacturer_status or 0
            self.status.setText(f"Manufacturer status: 0x{value:08X}")

        self._show_active(state.active)

        self.stored.clear()
        held = state.says(faults.PREDEFINED_ERROR_FIELD)
        self.clear_btn.setEnabled(held and self._node is not None)
        self.clear_btn.setToolTip(CLEAR_TIP if held else NOT_HELD)
        if not held:
            self.note.setText(NOT_HELD)
            return
        self.note.setText(NONE_STORED if not state.stored else "")
        for position, error in enumerate(state.stored, start=1):
            item = QTreeWidgetItem(
                [
                    str(position),
                    f"{error.code:04X}",
                    error.description,
                    f"{error.info:04X}" if error.info else "",
                ]
            )
            item.setTextAlignment(0, Qt.AlignRight | Qt.AlignVCenter)
            self.stored.addTopLevelItem(item)
