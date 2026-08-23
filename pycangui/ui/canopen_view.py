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

from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
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
from pycangui.canopen.manager import CanopenManager, format_value, od_entries, type_name
from pycangui.core.context import Context
from pycangui.core.hooks import Hooks
from pycangui.ui.pdo_view import PdoConfigView

ROLE_INDEX = Qt.UserRole
ROLE_SUB = Qt.UserRole + 1
NMT_COMMANDS_UI = (
    ("Start (operational)", "OPERATIONAL"),
    ("Pre-operational", "PRE-OPERATIONAL"),
    ("Stop", "STOPPED"),
    ("Reset node", "RESET"),
    ("Reset communication", "RESET COMMUNICATION"),
)


class CanopenView(QWidget):
    def __init__(self, manager: CanopenManager, hooks: Hooks, ctx: Context) -> None:
        super().__init__()
        self.manager = manager
        self.hooks = hooks
        self.ctx = ctx
        self._identities: dict[int, NodeIdentity] = {}
        self._asked: set[str] = set()  # identity keys we already prompted for
        self._updating = False  # guard against itemChanged during programmatic edits
        self._pdo_items: dict[tuple[str, str], QTreeWidgetItem] = {}
        mono = QFont("Consolas", 9)

        # --- nodes ---------------------------------------------------------
        self.nodes = QTreeWidget()
        self.nodes.setHeaderLabels(["Node", "Name", "State", "EDS"])
        self.nodes.setRootIsDecorated(False)
        self.nodes.currentItemChanged.connect(self._on_node_selected)
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
        load_btn.clicked.connect(self._load_eds_clicked)
        for b in (store_btn, restore_btn, save_dcf, apply_dcf, load_btn):
            file_bar.addWidget(b)
        file_bar.addStretch()

        # --- object dictionary ---------------------------------------------
        self.od = QTreeWidget()
        self.od.setHeaderLabels(["Index", "Name", "Type", "Access", "Value"])
        self.od.setFont(mono)
        self.od.header().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.od.header().setStretchLastSection(True)
        self.od.itemDoubleClicked.connect(self._on_od_double_clicked)
        self.od.itemChanged.connect(self._on_od_item_changed)
        od_bar = QHBoxLayout()
        od_bar.addWidget(QLabel("Object dictionary (double-click to read, edit value to write)"))
        od_bar.addStretch()
        read_all = QPushButton("Read all")
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
        splitter = QSplitter(Qt.Vertical)
        for w, stretch in ((top, 1), (mid, 3), (bottom, 1)):
            splitter.addWidget(w)
            splitter.setStretchFactor(splitter.count() - 1, stretch)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.addWidget(splitter)

        # --- wiring ------------------------------------------------------------
        manager.node_seen.connect(self.on_node_seen)
        manager.identified.connect(self.on_identified)
        manager.eds_loaded.connect(self.on_eds_loaded)
        manager.sdo_result.connect(self.on_sdo_result)
        manager.pdo_update.connect(self.on_pdo_update)
        manager.emcy.connect(lambda n, text: ctx.log(f"EMCY node {n}: {text}"))
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
            item.setText(2, state)

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
            path = self.ctx.settings.get("canopen.eds_map", {}).get(identity.key)
        if path is None:
            path = find_eds(identity, [self.ctx.eds_dir, resources.path("")])
        if path is None:
            path = self._ask_for_eds(identity)
        if path:
            if Path(path).exists():
                self.manager.load_eds(identity.node_id, str(path))
            else:
                self.ctx.log(f"Node {identity.node_id}: EDS not found: {path}")

    def _ask_for_eds(self, identity: NodeIdentity) -> str | None:
        if identity.key in self._asked:
            return None
        self._asked.add(identity.key)
        path, _ = QFileDialog.getOpenFileName(
            self,
            f"EDS file for node {identity.node_id} "
            f"(vendor {_hex(identity.vendor_id)}, product {_hex(identity.product_code)})",
            str(self.ctx.eds_dir),
            "EDS / DCF files (*.eds *.dcf);;All files (*)",
        )
        if not path:
            return None
        if identity.vendor_id is not None:
            answer = QMessageBox.question(
                self,
                "Remember EDS",
                "Use this EDS automatically for every device with this "
                "vendor / product / revision?",
            )
            if answer == QMessageBox.Yes:
                eds_map = dict(self.ctx.settings.get("canopen.eds_map", {}))
                eds_map[identity.key] = path
                self.ctx.settings.set("canopen.eds_map", eds_map)
        return path

    def _load_eds_clicked(self) -> None:
        node_id = self.selected_node()
        if node_id is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            f"EDS file for node {node_id}",
            str(self.ctx.eds_dir),
            "EDS / DCF files (*.eds *.dcf)",
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
        path, _ = QFileDialog.getSaveFileName(
            self,
            f"Save node {node_id} configuration",
            str(self.ctx.eds_dir / f"node{node_id}.dcf"),
            "Device configuration (*.dcf);;All files (*)",
        )
        if path:
            self.ctx.log(f"Node {node_id}: reading all parameters, this can take a while...")
            self.manager.save_dcf(node_id, path)

    def _apply_dcf(self) -> None:
        node_id = self.selected_node()
        if node_id is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            f"Apply a configuration to node {node_id}",
            str(self.ctx.eds_dir),
            "Device configuration (*.dcf *.eds);;All files (*)",
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
        self._identities.clear()
        self._asked.clear()
        self.pdo_config.set_node(None)
        self.sync_btn.setChecked(False)

    # --- object dictionary -------------------------------------------------------
    def _on_node_selected(self, current: QTreeWidgetItem | None, _previous) -> None:
        self.clear_live_pdos()
        self.pdo_config.set_node(None if current is None else current.data(0, ROLE_INDEX))
        self._populate_od(None if current is None else current.data(0, ROLE_INDEX))

    def _populate_od(self, node_id: int | None) -> None:
        self._updating = True
        self.od.clear()
        node = None if node_id is None else self.manager.node(node_id)
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
                if var is not None and var.writable:
                    item.setFlags(item.flags() | Qt.ItemIsEditable)
        self._updating = False

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
        if self._updating or column != 4:
            return
        node_id = self.selected_node()
        if node_id is not None:
            self.manager.sdo_write(
                node_id, item.data(0, ROLE_INDEX), item.data(0, ROLE_SUB) or 0, item.text(4)
            )

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
            self.ctx.log(f"Node {node_id}: SDO {index:04X}:{sub:02X} failed: {error}")
        if node_id != self.selected_node():
            return
        item = self._od_item(index, sub)
        if item is None:
            return
        node = self.manager.node(node_id)
        var = node.object_dictionary.get_variable(index, sub) if node else None
        self._updating = True
        item.setText(4, "error" if error else format_value(value, var))
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
            item.setText(1, format_value(value, None))

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
