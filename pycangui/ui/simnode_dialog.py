# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Starting and stopping simulated nodes.

Deliberately small.  A node is a Python file and everything interesting
about it is in there; the only questions this dialog asks are the ones the
file cannot answer for itself -- which channel, how fast, and a second
channel if it is a gateway.  Anything more here would be the node editor
that simulated nodes exist in order not to be.

Not a dock pane: this is used for a moment at the start of a session and
then not again, and a pane that spends the day empty is a pane in the way.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pycangui.core.simnodes import NodeError, SimulatedNodes
from pycangui.ui import messages

TITLE = "Simulated nodes"

WHAT_THEY_ARE = (
    "A simulated node is a device pycangui pretends to be, so a real one has "
    "something to talk to. Each is a Python file in your workspace's "
    "<b>nodes</b> folder -- edit one, or copy it, and it is yours."
)

#: Offered when a node wants a bus of its own.  Typing a name pycangui does
#: not know adds it as a virtual channel and connects it, so this is a
#: suggestion rather than anything special.
SUGGESTED_CHANNEL = "Simulation"

NONE_YET = "No node files in this workspace. Open the folder to write one."


class SimulatedNodeDialog(QDialog):
    """Which nodes there are, which are running, and the way between."""

    def __init__(self, parent: QWidget, nodes: SimulatedNodes, channels=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(TITLE)
        self.resize(620, 480)
        self.nodes = nodes
        self._channels = channels

        blurb = QLabel(WHAT_THEY_ARE)
        blurb.setWordWrap(True)
        blurb.setTextFormat(Qt.RichText)

        self.kinds = QTreeWidget()
        self.kinds.setHeaderLabels(["Node", "What it does"])
        self.kinds.setRootIsDecorated(False)
        self.kinds.setSelectionMode(QAbstractItemView.SingleSelection)
        self.kinds.header().resizeSection(0, 190)
        self.kinds.currentItemChanged.connect(self._chosen_changed)

        self.channel = QComboBox()
        self.channel.setEditable(True)
        self.channel.setToolTip(
            "Which channel to stand this node on. A node uses pycangui's\n"
            "channels like everything else, so its traffic is in the trace\n"
            "and its channel is in the connect bar.\n\n"
            "An open channel is joined -- a real adapter asks first, because\n"
            "a node transmits. A name pycangui does not know is added as a\n"
            "virtual channel and connected, which is how to give the nodes a\n"
            "bus of their own."
        )
        self.second = QComboBox()
        self.second.setEditable(True)
        self.second.setToolTip(
            "A gateway stands on two channels and passes frames between them.\n"
            "Leave this empty for an ordinary node."
        )
        self.rate = QDoubleSpinBox()
        self.rate.setRange(0.01, 1000.0)
        self.rate.setDecimals(2)
        self.rate.setSuffix(" Hz")
        self.rate.setToolTip("How often the node's poll() runs. Its own rate, to start with.")

        self.start = QPushButton("Start")
        self.start.clicked.connect(self._start)

        where = QHBoxLayout()
        where.addWidget(QLabel("Channel:"))
        where.addWidget(self.channel, 1)
        where.addWidget(QLabel("and:"))
        where.addWidget(self.second, 1)
        where.addWidget(QLabel("Rate:"))
        where.addWidget(self.rate)
        where.addWidget(self.start)

        available = QGroupBox("Available")
        inside = QVBoxLayout(available)
        inside.addWidget(self.kinds, 1)
        inside.addLayout(where)

        self.active = QTreeWidget()
        self.active.setHeaderLabels(["Running", "Channel", "Rate"])
        self.active.setRootIsDecorated(False)
        self.active.header().resizeSection(0, 190)
        self.stop = QPushButton("Stop")
        self.stop.clicked.connect(self._stop)
        self.active.currentItemChanged.connect(
            lambda current, _prev: self.stop.setEnabled(current is not None)
        )

        running = QGroupBox("Running now")
        beside = QVBoxLayout(running)
        beside.addWidget(self.active, 1)
        stop_row = QHBoxLayout()
        stop_row.addStretch()
        stop_row.addWidget(self.stop)
        beside.addLayout(stop_row)

        folder = QPushButton("Open nodes folder")
        folder.clicked.connect(self._open_folder)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.addButton(folder, QDialogButtonBox.ActionRole)

        layout = QVBoxLayout(self)
        layout.addWidget(blurb)
        layout.addWidget(available, 3)
        layout.addWidget(running, 2)
        layout.addWidget(buttons)

        self.refresh()

    # --- filling it in --------------------------------------------------------
    def refresh(self) -> None:
        """Re-read the folder and the running list.

        The folder is read every time rather than cached: somebody who has
        just written a node file expects to find it here, and being told to
        restart first would be an odd thing for a tool with a Reload hooks
        menu entry to say.
        """
        self._fill_kinds()
        self._fill_channels()
        self._fill_running()

    def _fill_kinds(self) -> None:
        chosen = self._chosen_id()
        self.kinds.clear()
        found = self.nodes.kinds()
        for kind in found:
            item = QTreeWidgetItem([kind.label, kind.error or kind.description])
            item.setData(0, Qt.UserRole, kind.id)
            if kind.error:
                # Listed rather than hidden: a file that vanished because of a
                # typo would send its author looking for something they can see
                # on disk.
                item.setDisabled(True)
                item.setToolTip(1, kind.error)
            self.kinds.addTopLevelItem(item)
            if kind.id == chosen:
                self.kinds.setCurrentItem(item)
        if not found:
            empty = QTreeWidgetItem([NONE_YET, ""])
            empty.setDisabled(True)
            self.kinds.addTopLevelItem(empty)
        if self.kinds.currentItem() is None and found:
            self.kinds.setCurrentItem(self.kinds.topLevelItem(0))

    def _fill_channels(self) -> None:
        names = list(self._channels.names()) if self._channels is not None else []
        for box in (self.channel, self.second):
            current = box.currentText()
            box.clear()
            box.addItems(names)
            if SUGGESTED_CHANNEL not in names:
                box.addItem(SUGGESTED_CHANNEL)
            if box is self.second:
                box.insertItem(0, "")
            if current:
                box.setCurrentText(current)
        self.second.setCurrentText("")

    def _fill_running(self) -> None:
        self.active.clear()
        for node in self.nodes.running():
            item = QTreeWidgetItem([node.name, ", ".join(node.channels), f"{node.rate_hz:g} Hz"])
            item.setData(0, Qt.UserRole, node)
            self.active.addTopLevelItem(item)
        self.stop.setEnabled(self.active.currentItem() is not None)

    # --- doing something ------------------------------------------------------
    def _chosen_id(self) -> str | None:
        item = self.kinds.currentItem()
        return None if item is None else item.data(0, Qt.UserRole)

    def _chosen_changed(self, current, _previous) -> None:
        """Offer the node's own rate the moment one is picked.

        A file that says what rate it wants should not have to say it again
        in a dialog, and a rate left over from the last node is a wrong
        answer that looks like a considered one.
        """
        self.start.setEnabled(current is not None and not current.isDisabled())
        if (kind_id := self._chosen_id()) and (kind := self.nodes.kind(kind_id)):
            self.rate.setValue(kind.rate_hz)

    def _start(self) -> None:
        kind_id = self._chosen_id()
        if kind_id is None:
            return
        second = self.second.currentText().strip()
        try:
            self.nodes.start(
                kind_id,
                self.channel.currentText().strip(),
                self.rate.value(),
                extra=[second] if second else [],
            )
        except NodeError as exc:
            messages.warning(self, TITLE, str(exc))
        self._fill_running()

    def _stop(self) -> None:
        item = self.active.currentItem()
        if item is None:
            return
        self.nodes.stop(item.data(0, Qt.UserRole))
        self._fill_running()

    def _open_folder(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.nodes.ctx.nodes_dir)))
