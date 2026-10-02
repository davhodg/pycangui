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

import csv
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pycangui import resources
from pycangui.canopen import NodeIdentity, find_eds, load_od
from pycangui.canopen import emcy as emcy_mod
from pycangui.canopen.display import (
    Display,
    as_number,
    format_number,
    limits_text,
    out_of_range,
)
from pycangui.canopen.display import text as value_text
from pycangui.canopen.manager import (
    NOT_CONNECTED,
    STOPPED,
    CanopenManager,
    od_entries,
    type_name,
)
from pycangui.core import workspace_files
from pycangui.core.context import Context
from pycangui.core.events import ERROR, GOOD
from pycangui.core.hooks import Hooks
from pycangui.custom_panes.model import Field as PaneField
from pycangui.custom_panes.model import names as custom_names
from pycangui.custom_panes.source import FileSource
from pycangui.ui import canopen_login, canopen_settings, folders, keep_file, messages
from pycangui.ui.canopen_log_view import CanopenLogView
from pycangui.ui.faults_view import FaultsView
from pycangui.ui.lss_view import LssView
from pycangui.ui.pdo_view import PdoConfigView

ROLE_INDEX = Qt.UserRole
ROLE_SUB = Qt.UserRole + 1
#: The text a row is filtered on, built once when the tree is filled.
ROLE_SEARCH = Qt.UserRole + 2
#: On a node-list row that is a DCF or EDS rather than a node: its path.
ROLE_FILE = Qt.UserRole + 3
#: The files open in the list, reopened with the workspace.
FILES_KEY = "canopen.files"
#: A value changed in a file and not saved yet. Translucent, as elsewhere.
EDITED_COLOUR = QColor(230, 150, 0, 70)
READ_EDS_TIP = (
    "Read the EDS the selected node keeps in itself (object 0x1021), save\n"
    "it in the workspace's EDS folder and use it for the node. For a node\n"
    "with no EDS to hand. Few devices keep one; one that does not says so."
)
OPEN_FILE_TIP = (
    "Open a DCF or EDS as a row of the list, with no node and no bus needed:\n"
    "its object dictionary in the tree below, with the file's values -- or the\n"
    "EDS defaults where it has none -- to read and change. Changes stay in\n"
    "memory, tinted, until Save writes a DCF through the file's own text."
)

# --- columns of the object dictionary tree ---
COL_NAME = 1
COL_ACCESS = 3
COL_VALUE = 4
#: A tick against the objects worth coming back to. A real device offers
#: fifteen hundred of them and a job uses eight, so the list you build is
#: worth more than the search that built it -- and it is the shape a
#: user-composed pane will need later.
COL_WATCH = 5

# --- columns of the node list, which is a different tree with its own 5 ---
COL_LEVEL = 4  #: the access level held
COL_ERROR = 5  #: what the node's own error register says, when asked

# --- columns of the emergencies tree, which is a third one ---
COL_EMCY_DESCRIPTION = 2
COL_EMCY_STATE = 3
#: Arrivals kept across every node. Old enough to have scrolled past a
#: hundred times, and a tree of thousands is a tree nobody reads.
MOST_EMERGENCIES = 500
ERROR_COLOUR = QColor(200, 40, 40)
LOST_COLOUR = QColor(150, 150, 150)
ALIVE_BRUSH = QBrush()  # an empty brush restores the theme's normal colour
RESET_COLOUR = QColor(40, 140, 40)
NMT_TARGET_TIP = (
    "Send the NMT command to the selected node, or to every node (id 0)\n"
    "when none is selected. Click an empty part of the node list, or press\n"
    "Esc in it, to select none."
)
NMT_COMMANDS_UI = (
    ("Start (operational)", "OPERATIONAL"),
    ("Pre-operational", "PRE-OPERATIONAL"),
    ("Stop", "STOPPED"),
    ("Reset node", "RESET"),
    ("Reset communication", "RESET COMMUNICATION"),
)


IDENTIFY_TIP = (
    "Read 0x1018 and 0x1000 from the selected node: who it is, which is\n"
    "what an EDS is matched from. A node is identified once, when its row\n"
    "appears, so a reflashed one keeps what it said then until this is\n"
    "pressed -- and with automatic identification off in Settings, nothing\n"
    "has asked it at all."
)
SYNC_TIP = (
    "Transmit SYNC (0x080), so synchronous PDOs are exchanged. How often\n"
    "is in Settings: a rate is a fact about the bus, agreed once, rather\n"
    "than a decision to take every time this is pressed."
)


class _NodeList(QTreeWidget):
    """The node list, which can also have no node selected.

    A tree keeps its current row however it is clicked, and the first node
    heard is selected for you -- so once any node was listed, NMT could only
    ever go to one of them, never to all. A click on empty space, or Esc,
    selects none.
    """

    def mousePressEvent(self, event) -> None:
        if self.itemAt(event.position().toPoint()) is None:
            self.select_none()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape and self.currentItem() is not None:
            self.select_none()
            return
        super().keyPressEvent(event)

    def select_none(self) -> None:
        self.clearSelection()
        self.setCurrentItem(None)


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
        #: The files open in the node list, by path, and the one the tree holds.
        self._files: dict[str, FileSource] = {}
        self._od_file: FileSource | None = None
        self._updating = False  # guard against itemChanged during programmatic edits
        self._pdo_items: dict[tuple[str, str], QTreeWidgetItem] = {}
        #: Nodes whose heartbeat has stopped arriving. Still in the list,
        #: because they were there a moment ago and which one went is the
        #: news, but nothing can be asked of them until they are back.
        self._lost: set[int] = set()
        #: The Emergencies tree, which is grouped: a row per node, and the
        #: arrivals under it. The flat list beside it is arrival order, which
        #: is what says which to drop when there are too many.
        self._emcy_nodes: dict[int, QTreeWidgetItem] = {}
        self._emcy_rows: list[QTreeWidgetItem] = []
        mono = QFont("Consolas", 9)

        # --- nodes ---------------------------------------------------------
        self.nodes = _NodeList()
        self.nodes.setHeaderLabels(["Node", "Name", "State", "EDS", "Access", "Error"])
        self.nodes.setRootIsDecorated(False)
        self.nodes.currentItemChanged.connect(self._on_node_selected)
        self.nodes.setContextMenuPolicy(Qt.CustomContextMenu)
        self.nodes.customContextMenuRequested.connect(self._node_menu)
        # Under the list, because each is about the node highlighted in it.
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
        read_level = QPushButton("Read access level")
        read_level.setToolTip(
            "Ask the selected node which access level is held, through\n"
            "hooks/canopen.py::current_level."
        )
        read_level.clicked.connect(self._read_level)
        identify = QPushButton("Identify")
        identify.setToolTip(IDENTIFY_TIP)
        identify.clicked.connect(self._identify)
        load_btn = QPushButton("Load EDS...")
        load_btn.setToolTip(
            "Choose the EDS for the selected node by hand. Normally one is\n"
            "found by itself from the node's identity, or by\n"
            "hooks/canopen.py::eds_for_node."
        )
        load_btn.clicked.connect(self._load_eds_clicked)
        read_eds_btn = QPushButton("Read EDS from node")
        read_eds_btn.setToolTip(READ_EDS_TIP)
        read_eds_btn.clicked.connect(self._read_stored_eds)
        # Being allowed to talk to the node, and what says what its objects
        # are. Login and Read access level are two halves of one question,
        # so they sit together. Add node is not here: it acts on the list
        # rather than on a row of it, which puts it above with the network.
        for b in (identify, login, read_level, load_btn, read_eds_btn):
            node_bar.addWidget(b)
        node_bar.addStretch()
        #: Everything under the list acts on the node highlighted in it, so
        #: with nothing highlighted there is nothing for them to act on. A
        #: button that looks pressable and then says "no node selected" is a
        #: worse way to find that out than a button that is plainly not.
        self._node_buttons = [identify, login, read_level, load_btn, read_eds_btn]
        # Above the list: the network, and what changes who is on it. NMT and
        # SYNC are not about whichever row happens to be highlighted -- they
        # are services the whole bus hears, and NMT with no node selected
        # goes to every node on it. Add node is up here for the same reason:
        # it acts on the list rather than on a row of it.
        nmt_bar = QHBoxLayout()
        nmt_bar.addWidget(add_node)
        open_file = QPushButton("Open DCF/EDS...")
        open_file.setToolTip(OPEN_FILE_TIP)
        open_file.clicked.connect(self._open_file_dialog)
        nmt_bar.addWidget(open_file)
        nmt_bar.addSpacing(16)
        nmt_bar.addWidget(QLabel("NMT command:"))
        self.nmt_command = QComboBox()
        for label, command in NMT_COMMANDS_UI:
            self.nmt_command.addItem(label, command)
        self.nmt_command.setToolTip("Command to send to the selected node (or to all nodes)")
        nmt_bar.addWidget(self.nmt_command)
        # "Send NMT" rather than "Send": the bar has a second control beside
        # it and several more below, and a bare Send does not say which of
        # them it belongs to. It says who it goes to as well, since that is
        # the selection's doing and a reset sent to every node is not the
        # same thing as one sent to one of them.
        self.send_nmt = QPushButton()
        self.send_nmt.setToolTip(NMT_TARGET_TIP)
        self.send_nmt.clicked.connect(self._send_nmt)
        nmt_bar.addWidget(self.send_nmt)
        self._show_nmt_target()
        nmt_bar.addSpacing(16)
        # A tick rather than a button that stays down. It is a state this
        # tool is in -- producing SYNC or not -- and the label no longer has
        # to change to say which, since a tick already says it.
        self.sync_btn = QCheckBox("SYNC producer")
        self.sync_btn.setToolTip(SYNC_TIP)
        self.sync_btn.toggled.connect(self._toggle_sync)
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

        # The node's own parameters: what it is set to now, and the files
        # that carry those values. A second row rather than one long one,
        # because eight buttons on a line is wider than a docked pane.
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
        self.read_pdos_btn = QPushButton("Read PDO config")
        self.read_pdos_btn.setToolTip(
            "Read the selected node's PDO mapping, both directions, from the\n"
            "node itself. The EDS says how a node ships; this is how it is set\n"
            "up now, so Signals and Plot decode the TPDOs a remapped node\n"
            "actually sends, and CAN Transmit offers the RPDOs it receives."
        )
        self.read_pdos_btn.clicked.connect(self._read_pdos)
        for b in (self.read_pdos_btn, store_btn, restore_btn, save_dcf, apply_dcf):
            file_bar.addWidget(b)
            self._node_buttons.append(b)
        # In place of the node's buttons when the row is a file: what can be
        # done to a file is save it or put it away.
        self.save_file_btn = QPushButton("Save")
        self.save_file_btn.setToolTip(
            "Write the changed values into the file, as a DCF, through its own\n"
            "text so its comments are kept. An EDS is saved under a new name."
        )
        self.save_file_btn.clicked.connect(lambda: self.save_file(self.selected_file()))
        self.save_file_as_btn = QPushButton("Save as...")
        self.save_file_as_btn.setToolTip("Write the values to a new DCF, and go on with that one.")
        self.save_file_as_btn.clicked.connect(lambda: self.save_file_as(self.selected_file()))
        self.close_file_btn = QPushButton("Close file")
        self.close_file_btn.setToolTip("Take the file out of the list; asks first if edited.")
        self.close_file_btn.clicked.connect(lambda: self.close_file(self.selected_file()))
        self._file_buttons = [self.save_file_btn, self.save_file_as_btn, self.close_file_btn]
        for b in self._file_buttons:
            file_bar.addWidget(b)
            b.hide()
        file_bar.addStretch()
        self._offer_node_buttons()

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
        # The list is the dividing line. Above it, what is done to the
        # network: NMT and SYNC are services the whole bus hears, and
        # Settings belongs with them. Below it, everything that acts on the
        # node highlighted in it -- two rows of those, because nine buttons
        # on one line is wider than a docked pane.
        top_l.addLayout(nmt_bar)
        top_l.addWidget(self.nodes)
        top_l.addLayout(node_bar)
        top_l.addLayout(file_bar)
        objects = QWidget()
        objects_l = QVBoxLayout(objects)
        objects_l.setContentsMargins(0, 0, 0, 0)
        objects_l.addLayout(od_bar)
        objects_l.addWidget(self.od)
        self.pdo_config = PdoConfigView(manager, ctx)
        # One tab each, rather than the dictionary above a strip of tabs.
        # The dictionary, the live PDOs and LSS all want the height, and
        # sharing it between them left every one of them too short to read.
        bottom = QTabWidget()
        bottom.addTab(objects, "Object dictionary")
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
            ["Time / node", "Code", "Description", "State", "Register", "Data", "Manufacturer"]
        )
        # A row per node with its emergencies under it, rather than one flat
        # arrival log. An arrival log answers "what happened"; somebody with
        # a machine that will not run is asking "what is still wrong", and
        # that question is per node.
        self.emcy.setRootIsDecorated(True)
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
        save_emcy = QPushButton("Save...")
        save_emcy.setToolTip(
            "Write what has arrived to a CSV file, each emergency with what it\n"
            "is now: active, or cleared by a later reset from the same node."
        )
        save_emcy.clicked.connect(self._save_emergencies)
        emcy_bar.addWidget(save_emcy)
        clear_emcy = QPushButton("Clear")
        clear_emcy.setToolTip(
            "Empty this list. It is pycangui's record of what it heard, so this\n"
            "does not touch what the nodes themselves kept -- see the Faults tab."
        )
        clear_emcy.clicked.connect(self.clear_emergencies)
        emcy_bar.addWidget(clear_emcy)
        emcy_l.addLayout(emcy_bar)
        self.emcy_tab_index = bottom.addTab(emcy_box, "Emergencies")
        # Beside the arrival log rather than in it: one holds what was
        # broadcast while pycangui was listening, the other what the node
        # says when asked. Plugging in after a fault is the case that needs
        # both.
        self.faults = FaultsView(manager, ctx)
        bottom.addTab(self.faults, "Faults")
        self.lss = LssView(manager, ctx)
        bottom.addTab(self.lss, "LSS")
        self.log = CanopenLogView(manager, ctx, self.selected_node)
        bottom.addTab(self.log, "CANopen log")
        self.bottom_tabs = bottom
        splitter = QSplitter(Qt.Vertical)
        for w, stretch in ((top, 1), (bottom, 4)):
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
        manager.stored_eds.connect(self._on_stored_eds)
        manager.stored_eds_progress.connect(self._on_stored_eds_progress)
        manager.stored_eds_failed.connect(self._on_stored_eds_failed)
        manager.sdo_result.connect(self.on_sdo_result)
        manager.pdo_update.connect(self.on_pdo_update)
        manager.fault_state.connect(self.on_fault_state)
        manager.emcy.connect(manager.remember_emcy)
        manager.emcy.connect(self.on_emcy)
        manager.message.connect(ctx.log)
        manager._bus.disconnected.connect(self.clear)
        self.reopen_files()

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

    def selected_file(self) -> FileSource | None:
        """The DCF or EDS on the selected row, if the row is a file rather than a node."""
        item = self.nodes.currentItem()
        path = None if item is None else item.data(0, ROLE_FILE)
        return self._files.get(path) if path else None

    # --- a DCF or EDS as a row of the list ----------------------------------------------
    def _open_file_dialog(self) -> None:
        path = folders.open_file(
            self,
            self.ctx,
            folders.EDS,
            "Open a device configuration",
            "Device configuration (*.dcf *.eds);;All files (*)",
            self.ctx.eds_dir,
        )
        if path:
            self.open_file(path)

    def open_file(self, path: str | Path, select: bool = True) -> FileSource | None:
        """Put a DCF or EDS in the list and, by default, show it in the tree."""
        key = str(Path(path).resolve())
        if key not in self._files:
            if not Path(key).is_file():
                self.ctx.warn(f"Configuration file not found: {path}")
                return None
            source = FileSource(key, hooks=self.hooks)
            if source.object_dictionary is None:
                self.ctx.warn(f"{Path(key).name}: not a DCF or EDS pycangui can read")
                return None
            self._files[key] = source
            item = QTreeWidgetItem(["File", source.label, "", Path(key).name])
            item.setData(0, ROLE_FILE, key)
            item.setToolTip(3, key)
            self.nodes.addTopLevelItem(item)
            source.modified.connect(lambda _on, s=source: self._show_file_state(s))
            source.value.connect(
                lambda index, sub, raw, error, s=source: self._on_file_value(
                    s, index, sub, raw, error
                )
            )
            self._show_file_state(source)
            self._remember_files()
        if select:
            self.nodes.setCurrentItem(self._file_item(self._files[key]))
        return self._files[key]

    def _file_item(self, source: FileSource) -> QTreeWidgetItem | None:
        for i in range(self.nodes.topLevelItemCount()):
            item = self.nodes.topLevelItem(i)
            if item.data(0, ROLE_FILE) and self._files.get(item.data(0, ROLE_FILE)) is source:
                return item
        return None

    def _show_file_state(self, source: FileSource) -> None:
        if (item := self._file_item(source)) is not None:
            item.setText(1, source.label)
            item.setText(2, "edited" if source.unsaved else "offline")
        if source is self.selected_file():
            self.save_file_btn.setEnabled(source.unsaved)

    def _remember_files(self) -> None:
        self.ctx.settings.set(FILES_KEY, [str(s.path) for s in self._files.values()])

    def reopen_files(self) -> None:
        """The files open when the workspace was last closed, those still there."""
        for path in self.ctx.settings.get(FILES_KEY, []) or []:
            if Path(path).is_file():
                self.open_file(path, select=False)

    def save_file(self, source: FileSource | None) -> bool:
        """Write the file's changes. False if they were not written."""
        if source is None:
            return True
        if source.needs_new_name:
            return self.save_file_as(source)
        return self._save_file_to(source, None)

    def save_file_as(self, source: FileSource | None) -> bool:
        if source is None:
            return True
        path = folders.save_file(
            self,
            self.ctx,
            folders.EDS,
            "Save configuration as",
            "Device configuration (*.dcf);;All files (*)",
            self.ctx.eds_dir,
            suggested=source.path.stem + ".dcf",
        )
        return bool(path) and self._save_file_to(source, path)

    def _save_file_to(self, source: FileSource, path: str | None) -> bool:
        old = next((k for k, s in self._files.items() if s is source), None)
        item = self._file_item(source)  # found by its old path, so before it changes
        try:
            written = source.save(path)
        except OSError as exc:
            messages.warning(self, "The configuration was not saved", str(exc))
            return False
        new = str(Path(written).resolve())
        if old is not None and new != old:
            # Saved as another file, the row is that file now.
            self._files[new] = self._files.pop(old)
            if item is not None:
                item.setData(0, ROLE_FILE, new)
                item.setText(3, Path(new).name)
                item.setToolTip(3, new)
            self._remember_files()
        self._show_file_state(source)
        if source is self._od_file:
            self._populate_od(None, source)
        self.ctx.log(f"Saved {written}")
        return True

    def _may_drop(self, source: FileSource) -> bool:
        """Whether a file's changes can go: saved, thrown away, or none. False is Cancel."""
        if not source.unsaved:
            return True
        answer = messages.question(
            self,
            f"Save the changes to {source.label}?",
            f"Values changed in {source.label} have not been saved. Discard throws them away.",
            messages.Button.Save | messages.Button.Discard | messages.Button.Cancel,
            messages.Button.Save,
        )
        if answer == messages.Button.Save:
            return self.save_file(source)
        return answer == messages.Button.Discard

    def close_file(self, source: FileSource | None) -> bool:
        if source is None or not self._may_drop(source):
            return False
        item = self._file_item(source)
        self._files = {k: s for k, s in self._files.items() if s is not source}
        if item is not None:
            self.nodes.takeTopLevelItem(self.nodes.indexOfTopLevelItem(item))
        if self._od_file is source:
            self._od_file = None
            self.od.clear()
        self._remember_files()
        return True

    def may_discard(self) -> bool:
        """Whether every open file's changes can go, asking about each. False is Cancel."""
        return all(self._may_drop(source) for source in list(self._files.values()))

    def _on_file_value(self, source: FileSource, index: int, sub: int, raw, error) -> None:
        if source is not self._od_file:
            return
        if (item := self._od_item(index, sub)) is not None:
            self._show_file_value(item, source, index, sub, raw, error)

    def _show_file_value(self, item, source: FileSource, index: int, sub: int, raw, error) -> None:
        was, self._updating = self._updating, True
        display = source.display(index, sub)
        item.setText(COL_VALUE, str(error) if error else value_text(display, raw))
        edited = source.unsaved_at(index, sub)
        for column in range(COL_VALUE + 1):
            item.setBackground(column, EDITED_COLOUR if edited else QBrush())
        tip = self._describe(display, source.extras(index, sub), index, sub, None if error else raw)
        item.setToolTip(COL_VALUE, tip)
        item.setToolTip(COL_NAME, tip)
        self._updating = was

    def _file_write(self, source: FileSource, item, text: str) -> None:
        """A value typed into the tree, into the file: checked, then held in memory."""
        index, sub = item.data(0, ROLE_INDEX), item.data(0, ROLE_SUB) or 0
        var = source._variable(index, sub)
        raw, why = _typed_value(var, source.display(index, sub), text)
        if why:
            self.ctx.warn(f"{source.label}: {index:04X}:{sub:02X} not changed, {why}")
            self._show_file_value(item, source, index, sub, *source.current(index, sub))
            return
        source.write(index, sub, raw)

    # --- a node by hand, and its access level --------------------------------------
    def _node_menu(self, at) -> None:
        """Everything the buttons under the list do, on the node clicked.

        Right-clicking a node and finding three of the eight things that can
        be done to it is worse than finding none: it reads as a list of what
        is possible here. The separators are the same grouping as the rows
        below -- getting at the node, then its parameters.
        """
        item = self.nodes.itemAt(at)
        if item is not None and item.data(0, ROLE_FILE):
            self.nodes.setCurrentItem(item)
            self.file_menu(self.selected_file()).exec(self.nodes.viewport().mapToGlobal(at))
            return
        node_id = None if item is None else item.data(0, ROLE_INDEX)
        self.node_menu(node_id).exec(self.nodes.viewport().mapToGlobal(at))

    def file_menu(self, source: FileSource) -> QMenu:
        menu = QMenu(self.nodes)
        menu.addAction("Save", lambda: self.save_file(source)).setEnabled(source.unsaved)
        menu.addAction("Save as...", lambda: self.save_file_as(source))
        menu.addSeparator()
        menu.addAction("Close file", lambda: self.close_file(source))
        return menu

    def node_menu(self, node_id: int | None) -> QMenu:
        """Built apart from being shown, so what it offers can be looked at."""
        menu = QMenu(self.nodes)
        menu.addAction("Add node...", self._add_node)
        on_a_node = self._can_act_on(node_id)
        for group in (
            (
                ("Identify", self._identify),
                ("Login...", self._login),
                ("Read access level", self._read_level),
            ),
            (
                ("Load EDS...", self._load_eds_clicked),
                ("Read EDS from node", self._read_stored_eds),
                ("Read PDO config", self._read_pdos),
            ),
            (
                ("Store", self._store),
                ("Restore defaults", self._restore),
                ("Save DCF...", self._save_dcf),
                ("Apply DCF...", self._apply_dcf),
            ),
        ):
            menu.addSeparator()
            for text, slot in group:
                menu.addAction(text, slot).setEnabled(on_a_node)
        return menu

    def _add_node(self) -> None:
        if self.manager.network is None:  # said before a node id is asked for, not after
            self.ctx.warn(f"Add node: {NOT_CONNECTED}")
            return
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

    def _identify(self) -> None:
        """Ask the selected node who it is, again if need be.

        Nothing else re-reads 0x1018. A node is identified once, when its
        row appears, so one that has been reflashed -- or that was added by
        hand before it was powered -- keeps whatever it said then, and with
        automatic identification switched off it never said anything.
        """
        node_id = self.selected_node()
        if node_id is None:
            self.ctx.warn("Identify: no node selected")
            return
        self.manager.identify(node_id)

    def _read_level(self) -> None:
        node_id = self.selected_node()
        if node_id is None:
            self.ctx.warn("Read access level: no node selected")
            return
        self.manager.read_level(node_id)

    @Slot(int, object)
    def on_access_level(self, node_id: int, level) -> None:
        if (item := self._node_item(node_id)) is not None:
            item.setText(COL_LEVEL, "" if level is None else str(level))

    @Slot(object)
    def on_fault_state(self, state) -> None:
        """The Error column: what the node's own error register says.

        What is wrong now, and only when it was read. Where a hook lists
        the active faults that is what is shown, because "which fault" is
        a better answer than "some category is set"; otherwise the error
        register's categories. An emergency that arrived and went is not a
        fault now, and a node nobody has asked gets an empty cell rather
        than a reassuring one.
        """
        item = self._node_item(state.node_id)
        if item is None:
            return
        item.setText(COL_ERROR, state.active_text)
        item.setForeground(COL_ERROR, ERROR_COLOUR if state.faulted else ALIVE_BRUSH)

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
            # The one thing pycangui sends a node unasked, so it is the one
            # thing worth being able to stop. Off, the row keeps its number
            # and waits for Identify or Load EDS.
            if canopen_settings.load(self.ctx).identify:
                self.manager.identify(node_id)
        else:
            item.setText(2, state)  # a fresh heartbeat replaces any "lost" text

    @Slot(int)
    def on_node_lost(self, node_id: int) -> None:
        self._lost.add(node_id)
        self._offer_node_buttons()  # nothing can be asked of it now
        item = self._node_item(node_id)
        if item is not None:
            item.setText(2, f"lost ({item.text(2)})")
            for column in range(item.columnCount()):
                item.setForeground(column, LOST_COLOUR)

    @Slot(int)
    def on_node_back(self, node_id: int) -> None:
        self._lost.discard(node_id)
        self._offer_node_buttons()
        self._ask_the_hooks_again(node_id)
        item = self._node_item(node_id)
        if item is not None:
            for column in range(item.columnCount()):
                item.setForeground(column, ALIVE_BRUSH)

    def _ask_the_hooks_again(self, node_id: int) -> None:
        """A node that went away and came back may not be what it was.

        A controller is reflashed by dropping off the bus and returning, and
        what comes back can want a different EDS or a different name. Its
        row is never removed while that happens -- which node went is the
        news -- so nothing would otherwise reconsider either.

        pycangui does not re-read 0x1018 here. Probing a node that has just
        recovered is precisely the behaviour worth being able to switch off,
        and it would be pycangui deciding to do it. The hooks are asked
        again instead: they can reach the device themselves if they want to,
        and whether a reflashed controller needs looking at again is
        knowledge about that device, which is what a hook is for.

        Only the hooks. The remembered choice, the search of the EDS folder
        and the file dialog are for a node nobody has an answer for yet, and
        a dialog opening every time a heartbeat came back would be a way of
        making people unplug things.
        """
        identity = self._identities.get(node_id, NodeIdentity(node_id))
        if (name := self.hooks.call("canopen", "node_name", identity)) and (
            item := self._node_item(node_id)
        ):
            item.setText(1, name)
        path = self.hooks.call("canopen", "eds_for_node", identity)
        if not path or str(path) == self.manager.eds_path(node_id):
            return  # the same file it already has, so nothing to do
        if Path(path).exists():
            self.ctx.log(f"Node {node_id}: back, and the hook now says {Path(path).name}")
            self.manager.load_eds(node_id, str(path))
        else:
            self.ctx.warn(f"Node {node_id}: EDS not found: {path}")

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

    # --- the EDS a node keeps (0x1021) ------------------------------------------
    def _read_stored_eds(self) -> None:
        node_id = self.selected_node()
        if node_id is None:
            return
        self._close_eds_progress()
        box = QProgressDialog(
            f"Reading the EDS node {node_id} keeps (object 0x1021)...", "Stop", 0, 0, self
        )
        box.setWindowTitle("Read EDS from node")
        box.setWindowModality(Qt.WindowModal)
        box.setMinimumDuration(0)
        box.setAutoClose(False)
        box.setAutoReset(False)
        box.canceled.connect(self.manager.stop_reading_stored_eds)
        box.show()
        self._eds_progress = box
        self.manager.read_stored_eds(node_id)

    def _close_eds_progress(self) -> None:
        box, self._eds_progress = getattr(self, "_eds_progress", None), None
        if box is not None:
            box.canceled.disconnect(self.manager.stop_reading_stored_eds)
            box.close()
            box.deleteLater()

    @Slot(int, int, int)
    def _on_stored_eds_progress(self, node_id: int, done: int, total: int) -> None:
        box = getattr(self, "_eds_progress", None)
        if box is None:
            return
        size = f"{done:,} of {total:,} bytes" if total else f"{done:,} bytes"
        box.setLabelText(f"Reading the EDS node {node_id} keeps (object 0x1021): {size}")
        if total:
            box.setMaximum(total)
            box.setValue(min(done, total))

    @Slot(int, str)
    def _on_stored_eds_failed(self, node_id: int, why: str) -> None:
        self._close_eds_progress()
        if why == STOPPED:
            self.ctx.log(f"Node {node_id}: reading its EDS was stopped")
        else:
            self.ctx.warn(f"Node {node_id}: its EDS could not be read: {why}")

    @Slot(int, object, int)
    def _on_stored_eds(self, node_id: int, data: bytes, kind: int) -> None:
        """Keep what the node sent as a file, and use it for the node."""
        self._close_eds_progress()
        # A device pads the object to the size of the memory it is kept in.
        text = data.rstrip(b"\x00\xff")
        if not text:
            self.ctx.warn(f"Node {node_id}: its EDS object (0x1021) is empty")
            return
        if kind != 0:
            # 0x1022 other than 0 is a compression of the maker's own, and
            # guessing at one is how a file of noise gets loaded as an EDS.
            self.ctx.warn(
                f"Node {node_id} keeps its EDS in a format of its maker's own "
                f"(0x1022 is {kind}), which pycangui cannot read. It can be saved as it came."
            )
            path = folders.save_file(
                self,
                self.ctx,
                folders.EDS,
                f"Save what node {node_id} sent, as it came",
                "All files (*)",
                self.ctx.eds_dir,
                f"node{node_id}_eds_format{kind}.bin",
            )
            if path:
                Path(path).write_bytes(data)
            return
        path = folders.save_file(
            self,
            self.ctx,
            folders.EDS,
            f"Save the EDS read from node {node_id}",
            "EDS files (*.eds)",
            self.ctx.eds_dir,
            self._stored_eds_name(node_id),
        )
        if not path:
            return
        Path(path).write_bytes(text)
        try:
            load_od(path)
        except Exception as exc:
            self.ctx.warn(
                f"Node {node_id}: what it keeps in 0x1021 is saved as {Path(path).name}, "
                f"but is not an EDS pycangui can read ({exc})"
            )
            return
        self.ctx.log(
            f"Node {node_id}: EDS read from the node, {len(text):,} bytes, saved as {path}"
        )
        self.manager.load_eds(node_id, path)

    def _stored_eds_name(self, node_id: int) -> str:
        """Vendor and product where the node has said them: what tells one
        device's file from another's in a folder of them."""
        identity = self._identities.get(node_id)
        if identity and identity.vendor_id is not None and identity.product_code is not None:
            return f"{identity.vendor_id:08X}_{identity.product_code:08X}.eds"
        return f"node{node_id}.eds"

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
        where = workspace_files.shown(path, self.ctx.workspace_dir)
        self.ctx.log(f"EDS loaded for node {node_id}: {where}")
        if self.selected_node() == node_id:
            self._populate_od(node_id)
            self.pdo_config.set_node(node_id)

    def _send_nmt(self) -> None:
        self._nmt(self.nmt_command.currentData())

    def _read_pdos(self) -> None:
        node_id = self.selected_node()
        if node_id is not None:
            self.manager.read_pdo_config(node_id)

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
        if on:
            # The rate comes from the settings rather than a box beside the
            # button: it is a fact about the bus, agreed once.
            # So does the counter, for the devices that use one.
            chosen = canopen_settings.load(self.ctx)
            if chosen.sync_counter_overflow:
                self.manager.start_sync(chosen.sync_period_ms / 1000, chosen.sync_counter_overflow)
            else:
                self.manager.start_sync(chosen.sync_period_ms / 1000)
        else:
            self.manager.stop_sync()

    def _nmt(self, command: str) -> None:
        self.manager.nmt(self.selected_node() or 0, command)

    @Slot()
    def clear(self) -> None:
        """The bus went: its nodes go from the list. Files stay -- they were never on it."""
        for i in reversed(range(self.nodes.topLevelItemCount())):
            if not self.nodes.topLevelItem(i).data(0, ROLE_FILE):
                self.nodes.takeTopLevelItem(i)
        if self._od_file is None:
            self.od.clear()
        self.clear_live_pdos()
        self.clear_emergencies()
        self._identities.clear()
        self._asked.clear()
        self._lost.clear()
        if self.selected_file() is None:  # a file's PDOs are not the bus's to take
            self.pdo_config.set_node(None)
        self.sync_btn.setChecked(False)
        self._offer_node_buttons()  # nothing in the list, so nothing selected

    # --- emergencies ----------------------------------------------------------------
    def _emcy_parent(self, node_id: int) -> QTreeWidgetItem:
        """The row this node's emergencies hang under, made if it is new."""
        parent = self._emcy_nodes.get(node_id)
        if parent is None:
            parent = QTreeWidgetItem([f"Node {node_id}"])
            self._emcy_nodes[node_id] = parent
            self.emcy.addTopLevelItem(parent)
            parent.setExpanded(True)
        return parent

    def _say_how_many(self, node_id: int) -> None:
        """The node's own row says how much of this is still a problem."""
        parent = self._emcy_nodes.get(node_id)
        if parent is None:
            return
        children = [parent.child(i) for i in range(parent.childCount())]
        active = sum(1 for child in children if child.text(COL_EMCY_STATE) == emcy_mod.ACTIVE)
        parent.setText(COL_EMCY_DESCRIPTION, f"{active} active" if active else "all clear")
        parent.setForeground(COL_EMCY_DESCRIPTION, ERROR_COLOUR if active else RESET_COLOUR)

    @Slot(object)
    def on_emcy(self, emergency) -> None:
        """One arrival, under its node, and what it does to the others.

        A reset clears what that node had outstanding and nothing else: one
        drive recovering says nothing about another. The rule is
        ``emcy.states``, which is also what the CSV export uses, so the two
        cannot drift apart.
        """
        parent = self._emcy_parent(emergency.node_id)
        state = emcy_mod.RESET if emergency.is_reset else emcy_mod.ACTIVE
        item = QTreeWidgetItem(
            [
                f"{emergency.timestamp:.3f}" if emergency.timestamp else "",
                f"{emergency.code:04X}",
                emergency.description,
                state,
                f"{emergency.register:02X} ({emergency.register_text})",
                emergency.data_hex,
                emergency.manufacturer_text,
            ]
        )
        item.setForeground(
            COL_EMCY_DESCRIPTION, RESET_COLOUR if emergency.is_reset else ERROR_COLOUR
        )
        if emergency.is_reset:
            for position in range(parent.childCount()):
                child = parent.child(position)
                if child.text(COL_EMCY_STATE) == emcy_mod.ACTIVE:
                    child.setText(COL_EMCY_STATE, emcy_mod.CLEARED)
                    child.setForeground(COL_EMCY_DESCRIPTION, LOST_COLOUR)
        parent.addChild(item)
        self._emcy_rows.append(item)
        self._say_how_many(emergency.node_id)
        self.emcy.scrollToBottom()
        self._trim_emergencies()
        # An emergency is an error, and an emergency reset is the one line
        # in the log that is good news: it says the fault that filled the
        # screen a moment ago has gone.
        self.ctx.log(f"EMCY {emergency}", GOOD if emergency.is_reset else ERROR)

    def _trim_emergencies(self) -> None:
        """Oldest first, across every node, and a node row goes with its last one."""
        while len(self._emcy_rows) > MOST_EMERGENCIES:
            oldest = self._emcy_rows.pop(0)
            parent = oldest.parent()
            if parent is None:
                continue
            parent.removeChild(oldest)
            if parent.childCount() == 0:
                node_id = next(n for n, row in self._emcy_nodes.items() if row is parent)
                self.emcy.takeTopLevelItem(self.emcy.indexOfTopLevelItem(parent))
                self._emcy_nodes.pop(node_id, None)

    def _save_emergencies(self) -> None:
        """Write the history to CSV, each entry with what it is now.

        From the history rather than from the tree: the tree is a view of
        it, and what somebody attaches to a report should be the record.
        """
        history = self.manager.emcy_history
        if not history:
            self.ctx.warn("No emergencies to save.")
            return
        path = folders.save_file(
            self, self.ctx, "emergencies", "Save emergencies", "CSV (*.csv)", "emergencies.csv"
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8", newline="") as handle:
                csv.writer(handle).writerows(emcy_mod.as_rows(history))
        except OSError as exc:
            self.ctx.error(f"Could not write {path}: {exc}")
            return
        self.ctx.log(f"{len(history)} emergencies written to {path}")

    @Slot()
    def clear_emergencies(self) -> None:
        self.emcy.clear()
        self._emcy_nodes.clear()
        self._emcy_rows.clear()
        self.manager.clear_emcy_history()

    # --- object dictionary -------------------------------------------------------
    def _can_act_on(self, node_id: int | None) -> bool:
        """Whether there is a node there to be asked anything.

        A lost node is one whose heartbeat has stopped: it is still in the
        list, and every request to it would sit there until the SDO timeout
        gave up.
        """
        return node_id is not None and node_id not in self._lost

    def _offer_node_buttons(self) -> None:
        """Only with a node to act on. See _node_buttons. A file has its own."""
        on_a_node = self._can_act_on(self.selected_node())
        for button in self._node_buttons:
            button.setEnabled(on_a_node)
        source = self.selected_file() if hasattr(self, "_file_buttons") else None
        for button in getattr(self, "_file_buttons", []):
            button.setVisible(source is not None)
        if source is not None:
            self.save_file_btn.setEnabled(source.unsaved)

    def _show_nmt_target(self) -> None:
        node_id = self.selected_node()
        self.send_nmt.setText(
            "Send NMT to all nodes" if node_id is None else f"Send NMT to node {node_id}"
        )

    def _on_node_selected(self, current: QTreeWidgetItem | None, _previous) -> None:
        self._show_nmt_target()
        if hasattr(self, "log"):  # made after the list, which can select a node first
            self.log.node_changed()
        self._offer_node_buttons()
        node_id = None if current is None else current.data(0, ROLE_INDEX)
        self.faults.set_node(node_id)
        self.clear_live_pdos()
        if (source := self.selected_file()) is not None:
            self.pdo_config.set_file(source)
        else:
            self.pdo_config.set_node(node_id)
        self._populate_od(node_id, source)

    def _populate_od(self, node_id: int | None, source: FileSource | None = None) -> None:
        """The dictionary of the selected node -- or of the selected file, with its values."""
        self._updating = True
        self._od_node = node_id
        self._od_file = source
        self.od.clear()
        node = None if node_id is None else self.manager.node(node_id)
        od = (
            source.object_dictionary
            if source is not None
            else getattr(node, "object_dictionary", None)
        )
        watched = self._watched(node_id)
        if od is not None:
            parent_items: dict[int, QTreeWidgetItem] = {}
            for index, sub, var, name in od_entries(od):
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
                if var is not None and source is None:
                    # Watching is about a device, and a file is not one.
                    item.setCheckState(
                        COL_WATCH,
                        Qt.Checked if (index, sub or 0) in watched else Qt.Unchecked,
                    )
                if var is not None and var.writable:
                    item.setFlags(item.flags() | Qt.ItemIsEditable)
                if var is not None and source is not None:
                    where = (index, sub or 0)
                    self._show_file_value(item, source, *where, *source.current(*where))
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
        if self._od_file is not None and item.childCount() == 0:
            self._od_file.request(item.data(0, ROLE_INDEX), item.data(0, ROLE_SUB) or 0)
            return
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
        if self._od_file is not None:
            self._file_write(self._od_file, item, item.text(COL_VALUE))
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
        return self._describe(
            self.manager.display(node_id, index, sub),
            self.manager.extras(node_id, index, sub),
            index,
            sub,
            raw,
        )

    @staticmethod
    def _describe(display, extras: dict, index: int, sub: int, raw=None) -> str:
        """The tooltip for one object, from a node or from a file alike."""
        lines = [f"{index:04X}:{sub:02X}  {display.name}".rstrip()]
        if display.description:
            lines.append(display.description)
        if raw is not None and display.scaled:
            lines.append(f"Raw: {format_number(raw, None)}")
        if limits := limits_text(display):
            lines.append(f"Range: {limits}")
        for value, meaning in sorted(display.choices.items()):
            lines.append(f"  {value} = {meaning}")
        if extras:
            lines.append("")
            lines.append("From the EDS:")
            lines += [f"  {key} = {value}" for key, value in extras.items()]
        return "\n".join(lines)

    def _read_all(self) -> None:
        if self._od_file is not None:
            self._populate_od(None, self._od_file)  # from the file, as it stands
            return
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


def _typed_value(var, display, text: str):
    """(raw value, None) for what was typed into a file's tree, or (None, why not).

    What a node would be sent, checked the way a write to a node is: in the
    units shown, and against the limits the file itself declares.
    """
    from canopen.objectdictionary import datatypes

    if var is None:
        return None, "not in this file"
    kind = var.data_type
    text = text.strip()
    if kind in (datatypes.VISIBLE_STRING, datatypes.UNICODE_STRING):
        return text, None
    if kind in (datatypes.OCTET_STRING, datatypes.DOMAIN):
        try:
            return bytes.fromhex(text.replace(" ", "")), None
        except ValueError:
            return None, f"{text!r} is not hex bytes"
    number = as_number(text.split()[0]) if text else None
    if number is None:
        return None, f"{text!r} is not a number"
    raw = display.raw(number) if display.scaled else number
    if why := out_of_range(display, raw):
        return None, why
    if kind in datatypes.FLOAT_TYPES:
        return float(raw), None
    if not float(raw).is_integer():
        return None, f"{text} is not a whole number"
    return int(raw), None
