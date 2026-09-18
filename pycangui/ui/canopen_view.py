# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""CANopen pane: node list with NMT control, object dictionary browser with
SDO read/write, live PDO values.

EDS selection flow when a node first appears:
    heartbeat -> identify (0x1018) -> hooks.canopen.eds_for_node
      -> remembered choice for this identity (settings "canopen.eds_map")
      -> an EDS in the user's eds folder (or bundled) whose DeviceInfo matches
      -> ask the user (file dialog, offer to remember)
"""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pycangui import resources
from pycangui.canopen import NodeIdentity, find_eds
from pycangui.canopen.display import (
    Display,
    as_number,
    format_number,
    limits_text,
    out_of_range,
)
from pycangui.canopen.display import text as value_text
from pycangui.canopen.manager import CanopenManager, od_entries, type_name
from pycangui.core import workspace_files
from pycangui.core.context import Context
from pycangui.core.hooks import Hooks
from pycangui.custom_panes.model import Field as PaneField
from pycangui.custom_panes.model import names as custom_names
from pycangui.ui import canopen_login, canopen_settings, folders, keep_file, messages
from pycangui.ui.lss_view import LssView
from pycangui.ui.pdo_view import PdoConfigView

ROLE_INDEX = Qt.UserRole
ROLE_SUB = Qt.UserRole + 1
#: The text a row is filtered on, built once when the tree is filled.
ROLE_SEARCH = Qt.UserRole + 2

COL_NAME = 1
COL_ACCESS = 3
COL_VALUE = 4
#: A tick against the objects worth coming back to. A real device offers
#: fifteen hundred of them and a job uses eight, so the list you build is
#: worth more than the search that built it -- and it is the shape a
#: user-composed pane will need later.
COL_WATCH = 5
ERROR_COLOUR = QColor(200, 40, 40)
LOST_COLOUR = QColor(150, 150, 150)
ALIVE_BRUSH = QBrush()  # an empty brush restores the theme's normal colour
RESET_COLOUR = QColor(40, 140, 40)
NMT_COMMANDS_UI = (
    ("Start (operational)", "OPERATIONAL"),
    ("Pre-operational", "PRE-OPERATIONAL"),
    ("Stop", "STOPPED"),
    ("Reset node", "RESET"),
    ("Reset communication", "RESET COMMUNICATION"),
)


class CanopenView(QWidget):
    #: A pane name (or "" for one not made yet) and the objects to put on it.
    add_to_custom_pane = Signal(str, object)

    def __init__(self, manager: CanopenManager, hooks: Hooks, ctx: Context) -> None:
        super().__init__()
        self.manager = manager
        self.hooks = hooks
        self.ctx = ctx
        self._identities: dict[int, NodeIdentity] = {}
        self._asked: set[str] = set()  # identity keys we already prompted for
        #: Which node the object tree currently holds. Not the node-list
        #: selection: the rows belong to whoever they were filled for, and
        #: a tick has to be recorded against that device and no other.
        self._od_node: int | None = None
        self._updating = False  # guard against itemChanged during programmatic edits
        self._pdo_items: dict[tuple[str, str], QTreeWidgetItem] = {}
        mono = QFont("Consolas", 9)

        # --- nodes ---------------------------------------------------------
        self.nodes = QTreeWidget()
        self.nodes.setHeaderLabels(["Node", "Name", "State", "EDS", "Access"])
        self.nodes.setRootIsDecorated(False)
        self.nodes.currentItemChanged.connect(self._on_node_selected)
        self.nodes.setContextMenuPolicy(Qt.CustomContextMenu)
        self.nodes.customContextMenuRequested.connect(self._node_menu)
        # Under the list, because each is about a node in it.
        node_bar = QHBoxLayout()
        add_node = QPushButton("Add node...")
        add_node.setToolTip(
            "Put a node in the list that has not been heard from: heartbeat off,\n"
            "held in pre-operational, or sitting in its bootloader. It is\n"
            "identified straight away."
        )
        add_node.clicked.connect(self._add_node)
        login = QPushButton("Login...")
        login.setToolTip(
            "Ask the selected node for an access level. CANopen has no standard\n"
            "login, so this is hooks/canopen.py::login, written for your device."
        )
        login.clicked.connect(self._login)
        read_level = QPushButton("Read level")
        read_level.setToolTip(
            "Ask the selected node which access level is held, through\n"
            "hooks/canopen.py::current_level."
        )
        read_level.clicked.connect(self._read_level)
        for b in (add_node, login, read_level):
            node_bar.addWidget(b)
        node_bar.addStretch()
        # Two rows: commands on top, file / persistence actions below, so the
        # bar stays narrow enough for a docked pane.
        nmt_bar = QHBoxLayout()
        nmt_bar.addWidget(QLabel("NMT command:"))
        self.nmt_command = QComboBox()
        for label, command in NMT_COMMANDS_UI:
            self.nmt_command.addItem(label, command)
        self.nmt_command.setToolTip("Command to send to the selected node (or to all nodes)")
        nmt_bar.addWidget(self.nmt_command)
        send_nmt = QPushButton("Send")
        send_nmt.setToolTip("Send this NMT command to the selected node")
        send_nmt.clicked.connect(self._send_nmt)
        nmt_bar.addWidget(send_nmt)
        nmt_bar.addSpacing(16)
        nmt_bar.addWidget(QLabel("SYNC producer:"))
        self.sync_period = QDoubleSpinBox()
        self.sync_period.setRange(1, 10000)
        self.sync_period.setValue(100)
        self.sync_period.setSuffix(" ms")
        self.sync_period.setToolTip("SYNC period")
        self.sync_btn = QPushButton("Start")
        self.sync_btn.setCheckable(True)
        self.sync_btn.setToolTip("Transmit SYNC (0x080) so synchronous PDOs are exchanged")
        self.sync_btn.toggled.connect(self._toggle_sync)
        nmt_bar.addWidget(self.sync_period)
        nmt_bar.addWidget(self.sync_btn)
        nmt_bar.addStretch()
        # What is set once rather than done lives on a dialog of its own: the
        # bar is for commands, and every setting beside them hid them further.
        settings_btn = QPushButton("Settings...")
        settings_btn.setToolTip(
            "SDO timeout and retries for every node; and, per node, an SDO channel\n"
            "off the predefined one or a heartbeat timeout of its own."
        )
        settings_btn.clicked.connect(self._open_settings)
        nmt_bar.addWidget(settings_btn)
        # Whatever the workspace holds, before anything is asked of a node.
        canopen_settings.apply(manager, canopen_settings.load(ctx))

        file_bar = QHBoxLayout()
        store_btn = QPushButton("Store")
        store_btn.setToolTip("Save the node's parameters to non-volatile memory (0x1010)")
        store_btn.clicked.connect(self._store)
        restore_btn = QPushButton("Restore defaults")
        restore_btn.setToolTip("Restore the node's default parameters (0x1011)")
        restore_btn.clicked.connect(self._restore)
        save_dcf = QPushButton("Save DCF...")
        save_dcf.setToolTip("Read every parameter from the node and write a .dcf file")
        save_dcf.clicked.connect(self._save_dcf)
        apply_dcf = QPushButton("Apply DCF...")
        apply_dcf.setToolTip("Write the parameter values from a .dcf file into the node")
        apply_dcf.clicked.connect(self._apply_dcf)
        load_btn = QPushButton("Load EDS...")
        load_btn.setToolTip(
            "Choose the EDS for the selected node by hand. Normally one is\n"
            "found by itself from the node's identity, or by\n"
            "hooks/canopen.py::eds_for_node."
        )
        load_btn.clicked.connect(self._load_eds_clicked)
        self.read_rpdos_btn = QPushButton("Read RPDO config")
        self.read_rpdos_btn.setToolTip(
            "Read the selected node's RPDO mapping from the node itself.\n"
            "The EDS says how a node ships; this is how it is set up now, so\n"
            "CAN Transmit offers the RPDOs a remapped node actually receives."
        )
        self.read_rpdos_btn.clicked.connect(self._read_rpdos)
        for b in (store_btn, restore_btn, save_dcf, apply_dcf, load_btn, self.read_rpdos_btn):
            file_bar.addWidget(b)
        file_bar.addStretch()

        # --- object dictionary ---------------------------------------------
        self.od = QTreeWidget()
        self.od.setHeaderLabels(["Index", "Name", "Type", "Access", "Value", "Watch"])
        self.od.setFont(mono)
        self.od.header().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.od.header().setStretchLastSection(False)
        self.od.header().setSectionResizeMode(COL_VALUE, QHeaderView.Stretch)
        self.od.itemDoubleClicked.connect(self._on_od_double_clicked)
        self.od.itemChanged.connect(self._on_od_item_changed)
        # Several at once, because building a pane means picking the six
        # related parameters, and picking them one at a time is what a pane
        # exists to stop somebody doing.
        self.od.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.od.setContextMenuPolicy(Qt.CustomContextMenu)
        self.od.customContextMenuRequested.connect(self._od_menu)
        self.od.setToolTip(
            "Double-click an entry to read it from the node.\nEdit a value to write it back."
        )
        self.od_search = QLineEdit()
        self.od_search.setPlaceholderText("filter objects...")
        self.od_search.setToolTip(
            "Match on the index or the name; several words must all match.\n"
            "A record that matches shows its sub-indices, and nothing is\n"
            "discarded -- clearing the box brings the rest back."
        )
        self.od_search.textChanged.connect(lambda _t: self._apply_od_filter())
        self.watched_only = QPushButton("Watched")
        self.watched_only.setCheckable(True)
        self.watched_only.setToolTip(
            "Show only the objects ticked in the Watch column.\n"
            "The list is kept per device, so the next controller of the same\n"
            "kind opens with the objects you were using on the last one."
        )
        self.watched_only.toggled.connect(lambda _on: self._apply_od_filter())

        od_bar = QHBoxLayout()
        od_bar.addWidget(QLabel("Object dictionary"))
        od_bar.addWidget(self.od_search, 1)
        od_bar.addWidget(self.watched_only)
        read_all = QPushButton("Read all")
        read_all.setToolTip(
            "Read every readable entry in the dictionary, one SDO at a time.\n"
            "It can take a while on a large node."
        )
        read_all.clicked.connect(self._read_all)
        od_bar.addWidget(read_all)

        # --- PDOs -----------------------------------------------------------
        self.pdos = QTreeWidget()
        self.pdos.setHeaderLabels(["PDO / variable", "Value", "Count", "Rate"])
        self.pdos.setFont(mono)
        self.pdos.header().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.pdos.header().setStretchLastSection(True)

        # --- layout -----------------------------------------------------------
        top = QWidget()
        top_l = QVBoxLayout(top)
        top_l.setContentsMargins(0, 0, 0, 0)
        top_l.addLayout(nmt_bar)
        top_l.addLayout(file_bar)
        top_l.addWidget(self.nodes)
        top_l.addLayout(node_bar)
        mid = QWidget()
        mid_l = QVBoxLayout(mid)
        mid_l.setContentsMargins(0, 0, 0, 0)
        mid_l.addLayout(od_bar)
        mid_l.addWidget(self.od)
        self.pdo_config = PdoConfigView(manager, ctx)
        bottom = QTabWidget()
        live = QWidget()
        live_l = QVBoxLayout(live)
        live_l.setContentsMargins(0, 0, 0, 0)
        live_l.addWidget(self.pdos)
        self._pdo_counts: dict[str, int] = {}
        self._pdo_last: dict[str, tuple[int, float]] = {}  # name -> (count, when)
        self._pdo_rate_timer = QTimer(self, interval=500, timeout=self._refresh_pdo_rates)
        self._pdo_rate_timer.start()
        pdo_bar = QHBoxLayout()
        pdo_bar.addStretch()
        clear_pdos = QPushButton("Clear")
        clear_pdos.setToolTip("Forget the counts and rates collected so far")
        clear_pdos.clicked.connect(self.clear_live_pdos)
        pdo_bar.addWidget(clear_pdos)
        live_l.addLayout(pdo_bar)
        bottom.addTab(live, "Live PDOs")
        bottom.addTab(self.pdo_config, "PDO configuration")

        self.emcy = QTreeWidget()
        self.emcy.setHeaderLabels(
            ["Time", "Node", "Code", "Description", "Register", "Data", "Manufacturer"]
        )
        self.emcy.setRootIsDecorated(False)
        self.emcy.setFont(mono)
        self.emcy.header().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.emcy.header().setStretchLastSection(True)
        emcy_box = QWidget()
        emcy_l = QVBoxLayout(emcy_box)
        emcy_l.setContentsMargins(0, 0, 0, 0)
        emcy_l.addWidget(self.emcy)
        self.emcy.setToolTip(
            "Emergencies as the nodes report them (CiA 301).\n"
            "The five manufacturer bytes mean whatever the maker says they do,\n"
            "so they are decoded by hooks/canopen.py::emcy_manufacturer."
        )
        emcy_bar = QHBoxLayout()
        emcy_bar.addStretch()
        clear_emcy = QPushButton("Clear")
        clear_emcy.clicked.connect(self.clear_emergencies)
        emcy_bar.addWidget(clear_emcy)
        emcy_l.addLayout(emcy_bar)
        self.emcy_tab_index = bottom.addTab(emcy_box, "Emergencies")
        self.lss = LssView(manager, ctx)
        bottom.addTab(self.lss, "LSS")
        self.bottom_tabs = bottom
        splitter = QSplitter(Qt.Vertical)
        for w, stretch in ((top, 1), (mid, 3), (bottom, 1)):
            splitter.addWidget(w)
            splitter.setStretchFactor(splitter.count() - 1, stretch)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.addWidget(splitter)

        # --- wiring ------------------------------------------------------------
        manager.node_seen.connect(self.on_node_seen)
        manager.access_level.connect(self.on_access_level)
        manager.node_lost.connect(self.on_node_lost)
        manager.node_back.connect(self.on_node_back)
        manager.identified.connect(self.on_identified)
        manager.eds_loaded.connect(self.on_eds_loaded)
        manager.sdo_result.connect(self.on_sdo_result)
        manager.pdo_update.connect(self.on_pdo_update)
        manager.emcy.connect(manager.remember_emcy)
        manager.emcy.connect(self.on_emcy)
        manager.message.connect(ctx.log)
        manager._bus.disconnected.connect(self.clear)

    # --- node list -------------------------------------------------------------
    def _node_item(self, node_id: int) -> QTreeWidgetItem | None:
        for i in range(self.nodes.topLevelItemCount()):
            item = self.nodes.topLevelItem(i)
            if item.data(0, ROLE_INDEX) == node_id:
                return item
        return None

    def selected_node(self) -> int | None:
        item = self.nodes.currentItem()
        return None if item is None else item.data(0, ROLE_INDEX)

    # --- a node by hand, and its access level --------------------------------------
    def _node_menu(self, at) -> None:
        menu = QMenu(self.nodes)
        menu.addAction("Add node...", self._add_node)
        on_a_node = self.nodes.itemAt(at) is not None
        for text, slot in (("Login...", self._login), ("Read level", self._read_level)):
            menu.addAction(text, slot).setEnabled(on_a_node)
        menu.exec(self.nodes.viewport().mapToGlobal(at))

    def _add_node(self) -> None:
        node_id, chose = QInputDialog.getInt(
            self, "Add node", "Node id, 1 to 127:", self.selected_node() or 1, 1, 127
        )
        if not chose:
            return
        if self._node_item(node_id) is None and not self.manager.add_node(node_id):
            return
        if (item := self._node_item(node_id)) is not None:
            self.nodes.setCurrentItem(item)

    def _login(self) -> None:
        node_id = self.selected_node()
        if node_id is None:
            self.ctx.warn("Login: no node selected")
            return
        dialog = canopen_login.LoginDialog(
            self, node_id, canopen_login.remembered_level(self.ctx.settings)
        )
        if dialog.exec() != QDialog.Accepted:
            return
        level, password = dialog.chosen()
        self.ctx.settings.set(canopen_login.LEVEL_KEY, level)  # the level, never the password
        self.manager.login(node_id, level, password)

    def _read_level(self) -> None:
        node_id = self.selected_node()
        if node_id is None:
            self.ctx.warn("Read level: no node selected")
            return
        self.manager.read_level(node_id)

    @Slot(int, object)
    def on_access_level(self, node_id: int, level) -> None:
        if (item := self._node_item(node_id)) is not None:
            item.setText(4, "" if level is None else str(level))

    def _open_settings(self) -> None:
        dialog = canopen_settings.CanopenSettingsDialog(
            self, canopen_settings.load(self.ctx), self.selected_node()
        )
        if dialog.exec() != QDialog.Accepted:
            return
        chosen = dialog.settings()
        canopen_settings.save(self.ctx, chosen)
        canopen_settings.apply(self.manager, chosen)
        channels = ", ".join(
            f"node {node_id} on 0x{request:03X}/0x{response:03X}"
            for node_id, (request, response) in sorted(chosen.channels.items())
        )
        heartbeats = ", ".join(
            f"node {node_id} {ms:g} ms" for node_id, ms in sorted(chosen.heartbeat_timeouts.items())
        )
        self.ctx.log(
            f"CANopen settings: SDO timeout {chosen.timeout_ms:.0f} ms, "
            f"{chosen.retries} retries"
            + (f"; SDO channel {channels}" if channels else "")
            + (f"; heartbeat timeout {heartbeats}" if heartbeats else "")
        )

    @Slot(int, str)
    def on_node_seen(self, node_id: int, state: str) -> None:
        item = self._node_item(node_id)
        if item is None:
            item = QTreeWidgetItem([str(node_id), f"Node {node_id}", state, ""])
            item.setData(0, ROLE_INDEX, node_id)
            self.nodes.addTopLevelItem(item)
            if self.nodes.currentItem() is None:
                self.nodes.setCurrentItem(item)
            self.manager.identify(node_id)
        else:
            item.setText(2, state)  # a fresh heartbeat replaces any "lost" text

    @Slot(int)
    def on_node_lost(self, node_id: int) -> None:
        item = self._node_item(node_id)
        if item is not None:
            item.setText(2, f"lost ({item.text(2)})")
            for column in range(item.columnCount()):
                item.setForeground(column, LOST_COLOUR)

    @Slot(int)
    def on_node_back(self, node_id: int) -> None:
        item = self._node_item(node_id)
        if item is not None:
            for column in range(item.columnCount()):
                item.setForeground(column, ALIVE_BRUSH)

    @Slot(object)
    def on_identified(self, identity: NodeIdentity) -> None:
        self._identities[identity.node_id] = identity
        self.ctx.log(
            f"Node {identity.node_id}: vendor {_hex(identity.vendor_id)} "
            f"product {_hex(identity.product_code)} rev {_hex(identity.revision)} "
            f"serial {_hex(identity.serial)}"
        )
        if name := self.hooks.call("canopen", "node_name", identity):
            self._node_item(identity.node_id).setText(1, name)
        path = self.hooks.call("canopen", "eds_for_node", identity)
        if path is None:
            path = self._remembered_eds(identity)
        if path is None:
            path = find_eds(identity, [self.ctx.eds_dir, resources.path("")])
        if path is None:
            path = self._ask_for_eds(identity)
        if path:
            if Path(path).exists():
                self.manager.load_eds(identity.node_id, str(path))
            else:
                self.ctx.warn(f"Node {identity.node_id}: EDS not found: {path}")

    def _remembered_eds(self, identity: NodeIdentity) -> Path | None:
        """The EDS remembered for this device, if it is still there.

        Remembered relative to the workspace when it is in it, so an imported
        workspace finds it. One that is not there -- a link made on another
        computer, a file moved since -- is said, and the usual search of the
        EDS folder carries on rather than stopping at a path that goes nowhere.
        """
        eds_map = self.ctx.settings.get("canopen.eds_map", {})
        value = eds_map.get(identity.key) if isinstance(eds_map, dict) else None
        if not isinstance(value, str) or not value:
            return None
        path = workspace_files.resolve(value, self.ctx.workspace_dir)
        if path.is_file():
            return path
        self.ctx.warn(
            f"Node {identity.node_id}: the EDS remembered for it is not there ({value}); "
            "looking in the EDS folder instead"
        )
        return None

    def _ask_for_eds(self, identity: NodeIdentity) -> str | None:
        if identity.key in self._asked:
            return None
        self._asked.add(identity.key)
        path = folders.open_file(
            self,
            self.ctx,
            folders.EDS,
            f"EDS file for node {identity.node_id} "
            f"(vendor {_hex(identity.vendor_id)}, product {_hex(identity.product_code)})",
            "EDS / DCF files (*.eds *.dcf);;All files (*)",
            self.ctx.eds_dir,
        )
        if not path:
            return None
        if identity.vendor_id is not None:
            answer = messages.question(
                self,
                "Remember EDS",
                "Use this EDS automatically for every device with this "
                "vendor / product / revision?",
            )
            if answer == QMessageBox.Yes:
                eds_map = dict(self.ctx.settings.get("canopen.eds_map", {}))
                eds_map[identity.key] = keep_file.offer(self, self.ctx, path, workspace_files.EDS)
                self.ctx.settings.set("canopen.eds_map", eds_map)
                # The copy, if one was made: it is the file that will be used from now on.
                return str(workspace_files.resolve(eds_map[identity.key], self.ctx.workspace_dir))
        return path

    def _load_eds_clicked(self) -> None:
        node_id = self.selected_node()
        if node_id is None:
            return
        path = folders.open_file(
            self,
            self.ctx,
            folders.EDS,
            f"EDS file for node {node_id}",
            "EDS / DCF files (*.eds *.dcf)",
            self.ctx.eds_dir,
        )
        if path:
            self.manager.load_eds(node_id, path)

    @Slot(int, str, str)
    def on_eds_loaded(self, node_id: int, path: str, product_name: str) -> None:
        item = self._node_item(node_id)
        if item is None:
            return
        item.setText(3, Path(path).name)
        identity = self._identities.get(node_id, NodeIdentity(node_id))
        name = self.hooks.call("canopen", "node_name", identity) or product_name
        if name:
            item.setText(1, name)
        self.ctx.log(f"Node {node_id}: loaded {Path(path).name}")
        if self.selected_node() == node_id:
            self._populate_od(node_id)
            self.pdo_config.set_node(node_id)

    def _send_nmt(self) -> None:
        self._nmt(self.nmt_command.currentData())

    def _read_rpdos(self) -> None:
        node_id = self.selected_node()
        if node_id is not None:
            self.manager.read_rpdo_config(node_id)

    def _store(self) -> None:
        node_id = self.selected_node()
        if node_id is not None:
            self.manager.store_parameters(node_id)

    def _restore(self) -> None:
        node_id = self.selected_node()
        if node_id is not None:
            self.manager.restore_parameters(node_id)

    def _save_dcf(self) -> None:
        node_id = self.selected_node()
        if node_id is None:
            return
        path = folders.save_file(
            self,
            self.ctx,
            folders.EDS,
            f"Save node {node_id} configuration",
            "Device configuration (*.dcf);;All files (*)",
            self.ctx.eds_dir,
            suggested=f"node{node_id}.dcf",
        )
        if path:
            self.ctx.log(f"Node {node_id}: reading all parameters, this can take a while...")
            self.manager.save_dcf(node_id, path)

    def _apply_dcf(self) -> None:
        node_id = self.selected_node()
        if node_id is None:
            return
        path = folders.open_file(
            self,
            self.ctx,
            folders.EDS,
            f"Apply a configuration to node {node_id}",
            "Device configuration (*.dcf *.eds);;All files (*)",
            self.ctx.eds_dir,
        )
        if path:
            self.manager.apply_dcf(node_id, path)

    @Slot(bool)
    def _toggle_sync(self, on: bool) -> None:
        self.sync_btn.setText("Stop" if on else "Start")
        self.sync_period.setEnabled(not on)
        if on:
            self.manager.start_sync(self.sync_period.value() / 1000)
        else:
            self.manager.stop_sync()

    def _nmt(self, command: str) -> None:
        self.manager.nmt(self.selected_node() or 0, command)

    @Slot()
    def clear(self) -> None:
        self.nodes.clear()
        self.od.clear()
        self.clear_live_pdos()
        self.clear_emergencies()
        self._identities.clear()
        self._asked.clear()
        self.pdo_config.set_node(None)
        self.sync_btn.setChecked(False)

    # --- emergencies ----------------------------------------------------------------
    @Slot(object)
    def on_emcy(self, emergency) -> None:
        item = QTreeWidgetItem(
            [
                f"{emergency.timestamp:.3f}" if emergency.timestamp else "",
                str(emergency.node_id),
                f"{emergency.code:04X}",
                emergency.description,
                f"{emergency.register:02X} ({emergency.register_text})",
                emergency.data_hex,
                emergency.manufacturer_text,
            ]
        )
        if emergency.is_reset:
            item.setForeground(3, RESET_COLOUR)
        else:
            item.setForeground(3, ERROR_COLOUR)
        self.emcy.addTopLevelItem(item)
        self.emcy.scrollToBottom()
        if self.emcy.topLevelItemCount() > 500:
            self.emcy.takeTopLevelItem(0)
        self.ctx.log(f"EMCY {emergency}")

    @Slot()
    def clear_emergencies(self) -> None:
        self.emcy.clear()
        self.manager.clear_emcy_history()

    # --- object dictionary -------------------------------------------------------
    def _on_node_selected(self, current: QTreeWidgetItem | None, _previous) -> None:
        self.clear_live_pdos()
        self.pdo_config.set_node(None if current is None else current.data(0, ROLE_INDEX))
        self._populate_od(None if current is None else current.data(0, ROLE_INDEX))

    def _populate_od(self, node_id: int | None) -> None:
        self._updating = True
        self._od_node = node_id
        self.od.clear()
        node = None if node_id is None else self.manager.node(node_id)
        watched = self._watched(node_id)
        if node is not None:
            parent_items: dict[int, QTreeWidgetItem] = {}
            for index, sub, var, name in od_entries(node.object_dictionary):
                if sub is None:
                    item = QTreeWidgetItem([f"{index:04X}", name, type_name(var), _access(var), ""])
                    self.od.addTopLevelItem(item)
                    parent_items[index] = item
                else:
                    item = QTreeWidgetItem([f"{sub:02X}", name, type_name(var), _access(var), ""])
                    parent_items[index].addChild(item)
                item.setData(0, ROLE_INDEX, index)
                item.setData(0, ROLE_SUB, sub)
                # What a row can be searched by, worked out once: a dictionary
                # runs to fifteen hundred rows and filtering happens on every
                # keystroke. The parent's name is folded into each child so
                # that searching for a record reveals what is inside it.
                parent_name = "" if sub is None else parent_items[index].text(1)
                sub_text = "" if sub is None else f"{sub:02X}"
                item.setData(
                    0,
                    ROLE_SEARCH,
                    f"{index:04X} {sub_text} {name} {parent_name}".lower(),
                )
                if var is not None:
                    item.setCheckState(
                        COL_WATCH,
                        Qt.Checked if (index, sub or 0) in watched else Qt.Unchecked,
                    )
                if var is not None and var.writable:
                    item.setFlags(item.flags() | Qt.ItemIsEditable)
        self._updating = False
        self._apply_od_filter()

    # --- finding one of fifteen hundred -------------------------------------------
    def _watch_key(self, node_id: int | None) -> str:
        """Which device this watch list belongs to.

        The identity where the node has told us one, so the same kind of
        controller opens with the same list next time; the node id only as a
        fallback, since two different devices can sit at the same address on
        different days.
        """
        if node_id is None:
            return ""
        identity = self._identities.get(node_id)
        return identity.key if identity is not None else f"node{node_id}"

    def _watched(self, node_id: int | None) -> set[tuple[int, int]]:
        key = self._watch_key(node_id)
        if not key:
            return set()
        stored = self.ctx.settings.get("canopen.watch", {}).get(key, [])
        return {(int(i), int(s)) for i, s in stored}

    def _save_watched(self, node_id: int, watched: set[tuple[int, int]]) -> None:
        all_lists = dict(self.ctx.settings.get("canopen.watch", {}))
        key = self._watch_key(node_id)
        if watched:
            all_lists[key] = sorted([index, sub] for index, sub in watched)
        else:
            all_lists.pop(key, None)  # an empty list is the same as no list
        self.ctx.settings.set("canopen.watch", all_lists)

    def _on_watch_toggled(self, item: QTreeWidgetItem) -> None:
        node_id = self._od_node
        if node_id is None:
            return
        where = (item.data(0, ROLE_INDEX), item.data(0, ROLE_SUB) or 0)
        watched = self._watched(node_id)
        watched.add(where) if item.checkState(COL_WATCH) == Qt.Checked else watched.discard(where)
        self._save_watched(node_id, watched)
        if self.watched_only.isChecked():
            self._apply_od_filter()

    def _apply_od_filter(self) -> None:
        """Hide what does not match. Nothing is discarded and nothing is read.

        A record is shown when any of its sub-indices is -- and every child
        carries its parent's name in what it is searched by, so a search for
        the record shows the whole of it.
        """
        needles = self.od_search.text().lower().split()
        only_watched = self.watched_only.isChecked()

        def keep(item: QTreeWidgetItem) -> bool:
            if only_watched and item.checkState(COL_WATCH) != Qt.Checked:
                return False
            haystack = item.data(0, ROLE_SEARCH) or ""
            return all(needle in haystack for needle in needles)

        for i in range(self.od.topLevelItemCount()):
            top = self.od.topLevelItem(i)
            if top.childCount() == 0:
                top.setHidden(not keep(top))
                continue
            shown = 0
            for j in range(top.childCount()):
                child = top.child(j)
                child.setHidden(not keep(child))
                shown += not child.isHidden()
            top.setHidden(shown == 0)

    # --- onto a pane ------------------------------------------------------------
    def _od_menu(self, at) -> None:
        """Add what is selected to a custom pane.

        Here rather than in a dialog with an index box in it, because this is
        where the objects can be searched for and where their names already
        are. Typing 0x2001 into a form is what these exist to avoid.
        """
        chosen = self._picked_fields()
        if not chosen:
            return
        menu = QMenu(self.od)
        how_many = "object" if len(chosen) == 1 else f"{len(chosen)} objects"
        add = menu.addMenu(f"Add {how_many} to a custom pane")
        for name in custom_names():
            add.addAction(name, lambda n=name: self.add_to_custom_pane.emit(n, chosen))
        if custom_names():
            add.addSeparator()
        add.addAction("New custom pane...", lambda: self.add_to_custom_pane.emit("", chosen))
        menu.exec(self.od.viewport().mapToGlobal(at))

    def _picked_fields(self) -> list[PaneField]:
        """The selected rows, as fields shown the way their access suggests.

        A writable object is offered as one that can be typed into and a
        read-only one as a reading, since that is what they are. Either can
        be changed afterwards in the pane's own editor.
        """
        out: list[PaneField] = []
        for item in self.od.selectedItems():
            index = item.data(0, ROLE_INDEX)
            if index is None:
                continue
            writable = "w" in (item.text(COL_ACCESS) or "").lower()
            out.append(
                PaneField(
                    index=int(index),
                    sub=int(item.data(0, ROLE_SUB) or 0),
                    kind="number" if writable else "value",
                    label=item.text(COL_NAME),
                )
            )
        return out

    def _od_item(self, index: int, sub: int) -> QTreeWidgetItem | None:
        for i in range(self.od.topLevelItemCount()):
            top = self.od.topLevelItem(i)
            if top.data(0, ROLE_INDEX) != index:
                continue
            if top.data(0, ROLE_SUB) is None and top.childCount() == 0:
                return top
            for j in range(top.childCount()):
                child = top.child(j)
                if child.data(0, ROLE_SUB) == sub:
                    return child
        return None

    def _on_od_double_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        if column == 4 and item.flags() & Qt.ItemIsEditable:
            return  # editing the value column, not a read request
        node_id = self.selected_node()
        if node_id is not None and item.childCount() == 0:
            self.manager.sdo_read(node_id, item.data(0, ROLE_INDEX), item.data(0, ROLE_SUB) or 0)

    def _on_od_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._updating:
            return
        if column == COL_WATCH:
            self._on_watch_toggled(item)
            return
        if column != COL_VALUE:
            return
        node_id = self.selected_node()
        if node_id is None:
            return
        index, sub = item.data(0, ROLE_INDEX), item.data(0, ROLE_SUB) or 0
        text = item.text(4)
        display = self.manager.display(node_id, index, sub)
        # A scaled object is shown in its own units, so it has to be *read*
        # in them too, or typing what you see back would write a number the
        # factor away from what you meant.
        if display.scaled:
            try:
                text = str(display.raw(float(text.split()[0])))
            except (ValueError, IndexError):
                self.ctx.warn(f"Node {node_id}: {item.text(4)!r} is not a number")
                return
        raw = as_number(text)
        if raw is not None and (why := out_of_range(display, raw)):
            # The EDS states limits for nearly every object on a real device,
            # and a node is free to clamp a bad value silently -- which reads
            # as a parameter that took when it did not.
            self.ctx.warn(f"Node {node_id}: {index:04X}:{sub:02X} not written, {why}")
            return
        self.manager.sdo_write(node_id, index, sub, text)

    def _object_tooltip(self, node_id: int, index: int, sub: int, raw=None) -> str:
        """Everything the EDS said about this object that the columns cannot.

        Including the value as it came off the wire, when the cell above is
        showing a converted one -- a scaled reading is a claim made in a hook,
        and the number it was made from should be somewhere.

        And the vendor's own fields, listed exactly as the file wrote them.
        That is what tells somebody which field holds their units, which is
        what they need before they can write a line of
        hooks/canopen.py::object_display; without it the mechanism is only
        usable by whoever already knew the answer.
        """
        display = self.manager.display(node_id, index, sub)
        lines = [f"{index:04X}:{sub:02X}  {display.name}".rstrip()]
        if display.description:
            lines.append(display.description)
        if raw is not None and display.scaled:
            lines.append(f"Raw: {format_number(raw, None)}")
        if limits := limits_text(display):
            lines.append(f"Range: {limits}")
        for value, meaning in sorted(display.choices.items()):
            lines.append(f"  {value} = {meaning}")
        if extras := self.manager.extras(node_id, index, sub):
            lines.append("")
            lines.append("From the EDS:")
            lines += [f"  {key} = {value}" for key, value in extras.items()]
        return "\n".join(lines)

    def _read_all(self) -> None:
        node_id = self.selected_node()
        node = None if node_id is None else self.manager.node(node_id)
        if node is None:
            return
        for index, sub, var, _name in od_entries(node.object_dictionary):
            if var is not None and var.readable and var.data_type != 0xF:  # skip DOMAIN
                self.manager.sdo_read(node_id, index, sub or 0)

    @Slot(int, int, int, object, object)
    def on_sdo_result(self, node_id: int, index: int, sub: int, value, error) -> None:
        if error:
            self.ctx.warn(f"Node {node_id}: SDO {index:04X}:{sub:02X} failed: {error}")
        if node_id != self.selected_node():
            return
        item = self._od_item(index, sub)
        if item is None:
            return
        self._updating = True
        display = self.manager.display(node_id, index, sub)
        item.setText(4, "error" if error else value_text(display, value))
        tip = self._object_tooltip(node_id, index, sub, None if error else value)
        item.setToolTip(4, tip)
        item.setToolTip(1, tip)
        self._updating = False

    # --- PDOs -----------------------------------------------------------------
    @Slot(int, str, dict)
    def on_pdo_update(self, node_id: int, pdo_name: str, values: dict) -> None:
        if node_id != self.selected_node():
            return
        parent = self._pdo_items.get((pdo_name, None))
        if parent is None:
            parent = QTreeWidgetItem([pdo_name, "", "0", ""])
            self.pdos.addTopLevelItem(parent)
            parent.setExpanded(True)
            self._pdo_items[(pdo_name, None)] = parent
            self._pdo_last[pdo_name] = (0, time.monotonic())
        count = self._pdo_counts.get(pdo_name, 0) + 1
        self._pdo_counts[pdo_name] = count
        parent.setText(2, str(count))
        for var_name, value in values.items():
            key = (pdo_name, var_name)
            item = self._pdo_items.get(key)
            if item is None:
                item = QTreeWidgetItem([var_name, "", "", ""])
                parent.addChild(item)
                self._pdo_items[key] = item
            item.setText(1, value_text(Display(), value))

    def _refresh_pdo_rates(self) -> None:
        """Rate over the refresh interval, so the figure reads steadily."""
        now = time.monotonic()
        for pdo_name, count in self._pdo_counts.items():
            last_count, last_time = self._pdo_last.get(pdo_name, (0, now))
            dt = now - last_time
            if dt < 0.45:
                continue
            item = self._pdo_items.get((pdo_name, None))
            if item is not None:
                item.setText(3, f"{(count - last_count) / dt:.1f} Hz")
            self._pdo_last[pdo_name] = (count, now)

    @Slot()
    def clear_live_pdos(self) -> None:
        self.pdos.clear()
        self._pdo_items.clear()
        self._pdo_counts.clear()
        self._pdo_last.clear()


def _hex(value: int | None) -> str:
    return "?" if value is None else f"0x{value:08X}"


def _access(var) -> str:
    return "" if var is None else var.access_type
