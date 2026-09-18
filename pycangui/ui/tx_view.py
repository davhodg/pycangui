# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Transmit pane: messages to send once or cyclically.

One list holds both kinds of message, so everything being transmitted is
visible at a glance (tabs or two lists would hide half of it -- easy to leave
something cycling unnoticed):

* a **raw** row: you type the id and the data bytes;
* a **DBC** row: picked from a loaded database, its id and length come from the
  database and its data is *encoded from signal values*. Expand the row and
  edit signals in physical units; the encoded bytes update, and if the message
  is cycling the running transmission is updated too.
* a **CANopen RPDO** row: the same idea using a node's RPDO mapping, so a node's
  process data can be driven without hand-packing bytes.

The list is saved in settings.json ("tx.messages") and restored on start.
There can be more than one transmit pane -- a list of background traffic left
running and a scratch list to fiddle with is the case -- and each keeps its own
messages. What is *not* per pane is Stop all cyclic: the button says all, and
a big red stop that stopped half of what was going onto a live bus would be the
worst kind of wrong.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QFont, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMenu,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pycangui.canopen.manager import CanopenManager
from pycangui.core import tx_fields
from pycangui.core.bus import BusManager
from pycangui.core.context import Context
from pycangui.core.dbc import DbcDecoder
from pycangui.core.tx_fields import Checksum, Counter
from pycangui.ui.confirm import Confirmations, is_real
from pycangui.ui.tx_fields_dialog import TxFieldsDialog

COL_NAME, COL_ID, COL_EXT, COL_FD, COL_DATA, COL_PERIOD, COL_CYCLIC, COL_UNIT, COL_FIELDS = range(9)
HEADERS = (
    "Message / Signal",
    "ID",
    "Ext",
    "FD",
    "Data / Value",
    "Period ms",
    "Cyclic",
    "Unit",
    "Counter / checksum",
)
ROLE_KIND = Qt.UserRole  # "raw" | "dbc" | "rpdo" on message rows, "signal" on children
ROLE_MESSAGE = Qt.UserRole + 1  # DBC message name
ROLE_NODE = Qt.UserRole + 2  # CANopen node id
ROLE_PDO = Qt.UserRole + 3  # CANopen RPDO number
ROLE_COUNTER = Qt.UserRole + 4  # the counter configuration, as a dict
ROLE_CHECKSUM = Qt.UserRole + 5  # the checksum configuration, as a dict

COMPUTED_TIP = (
    "Worked out as each frame is sent, so whatever is here is ignored.\n"
    "Change it in the Fields dialog, or stop computing it there."
)
DEFAULT_RAW = {"kind": "raw", "id": "123", "data": "00 11 22 33", "period": 100, "name": ""}


def parse_hex_bytes(text: str) -> bytes:
    return bytes.fromhex(text.replace(",", " ").replace("0x", ""))


class MessagePicker(QDialog):
    """Pick a message from the loaded databases."""

    def __init__(self, dbc: DbcDecoder, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add message from database")
        self.resize(420, 460)
        self.search = QLineEdit()
        self.search.setPlaceholderText("filter...")
        self.search.textChanged.connect(self._filter)
        self.list = QListWidget()
        self.list.setFont(QFont("Consolas", 9))
        self.list.itemDoubleClicked.connect(lambda _i: self.accept())
        for msg in dbc.messages():
            ident = f"{msg.frame_id:08X}" if msg.is_extended_frame else f"{msg.frame_id:03X}"
            self.list.addItem(f"{msg.name}  [{ident}]  {len(msg.signals)} signals")
        if not self.list.count():
            # Said here rather than in the Event Log. A button that opens
            # nothing and writes a line into a pane you may have closed looks
            # from the outside exactly like a button that does not work.
            self.list.addItem("No database loaded.")
            self.list.addItem("File > Load DBC... to load one, then try again.")
            self.list.setEnabled(False)
            self.search.setEnabled(False)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.search)
        layout.addWidget(self.list)
        layout.addWidget(buttons)

    def _filter(self, text: str) -> None:
        needle = text.lower()
        for i in range(self.list.count()):
            item = self.list.item(i)
            item.setHidden(needle not in item.text().lower())

    def chosen(self) -> str | None:
        if not self.list.isEnabled():  # the list is holding an explanation
            return None
        item = self.list.currentItem()
        return item.text().split(" ")[0] if item is not None else None


class RpdoPicker(QDialog):
    """Pick an RPDO of a CANopen node whose configuration has been read."""

    def __init__(self, canopen: CanopenManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add CANopen RPDO")
        self.resize(440, 360)
        self.list = QListWidget()
        self.list.setFont(QFont("Consolas", 9))
        self.list.itemDoubleClicked.connect(lambda _i: self.accept())
        self.choices: list[tuple[int, int]] = []
        network = canopen.network
        for node_id in sorted(network.nodes) if network is not None else []:
            for number, name, variables in canopen.rpdos(node_id):
                self.choices.append((node_id, number))
                self.list.addItem(f"node {node_id}  {name}  ({', '.join(variables) or 'unmapped'})")
        if not self.choices:
            self.list.addItem("No RPDOs known.")
            self.list.addItem("Select the node in the CANopen pane and load its EDS;")
            self.list.addItem("use 'Read RPDO config' there if the node was remapped.")
            self.list.setEnabled(False)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("A node receives its RPDOs, so pycangui transmits them."))
        layout.addWidget(self.list)
        layout.addWidget(buttons)

    def chosen(self) -> tuple[int, int] | None:
        row = self.list.currentRow()
        return self.choices[row] if 0 <= row < len(self.choices) else None


class TxView(QWidget):
    #: Stop all cyclic was pressed here. Emitted rather than acted on across
    #: panes, because a transmit list has no business knowing that there are
    #: other transmit lists -- the window does.
    stop_all_requested = Signal()

    def __init__(
        self,
        bus: BusManager,
        ctx: Context,
        dbc: DbcDecoder,
        canopen: CanopenManager,
        confirm: Confirmations | None = None,
        key: str = "tx",
        hooks=None,
    ) -> None:
        super().__init__()
        self.bus = bus
        self.ctx = ctx
        #: Where this list is kept. There can be two transmit panes, and the
        #: second one holding the first one's messages would be one list shown
        #: twice rather than a second list.
        self.key = key
        self.confirm = confirm if confirm is not None else Confirmations()
        self.dbc = dbc
        self.canopen = canopen
        #: Where a bespoke checksum comes from. Optional: a transmit pane
        #: built by a plugin or stood up in a test has no hooks, and every
        #: checksum in the list then comes from the named algorithms.
        self.hooks = hooks
        self._tasks: dict[int, object] = {}  # top-level row -> periodic task
        #: Rows pycangui times itself, because every frame of them differs.
        #: See _start_row: an adapter repeating fixed bytes cannot carry a
        #: counter, and updating a free-running task races it.
        self._timers: dict[int, QTimer] = {}
        #: How many frames each row has sent, which is what the counter counts.
        self._sent: dict[int, int] = {}
        self._loading = False

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(HEADERS)
        self.tree.setFont(QFont("Consolas", 9))
        self.tree.setRootIsDecorated(True)
        self.tree.header().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(COL_DATA, QHeaderView.Stretch)
        self.tree.header().setStretchLastSection(True)
        # Send selected and Remove selected were always written to work on
        # several rows; the tree was left on single selection, so they never
        # could. Ticking Cyclic in bulk is the same selection, one key.
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.installEventFilter(self)
        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.itemDoubleClicked.connect(self._on_double_clicked)
        self.tree.setToolTip(
            "Double-click a message to send it once.\n"
            "Tick Cyclic to send it over and over at its period.\n"
            "Select several rows and press space to tick or untick them together;\n"
            "Ctrl+A selects the lot.\n"
            "Expand a DBC or RPDO row to edit its signals in physical units."
        )

        # Send and Stop all are the two buttons that put something on the bus
        # or take it off again; the rest only edit the list, so they follow.
        send = QPushButton("Send selected")
        send.clicked.connect(self.send_selected)
        stop_all = QPushButton("Stop all cyclic")
        stop_all.setToolTip("Stop every repeating transmission at once, in every transmit pane.")
        stop_all.clicked.connect(self._on_stop_all_pressed)
        # Three buttons that differed only in where the message came from are
        # one button and a menu: the choice is which source, not which button.
        add = QPushButton("Add")
        add.setToolTip("Add a message: raw bytes, one from the DBC, or a CANopen RPDO")
        self.add_menu = QMenu(add)
        self.add_menu.setToolTipsVisible(True)
        raw_action = self.add_menu.addAction("Raw message")
        raw_action.triggered.connect(lambda _=False: self.add_message(dict(DEFAULT_RAW)))
        dbc_action = self.add_menu.addAction("From DBC...")
        dbc_action.setToolTip("Pick a message from the loaded database and edit it by signal")
        dbc_action.triggered.connect(lambda _=False: self._add_from_dbc())
        rpdo_action = self.add_menu.addAction("CANopen RPDO...")
        rpdo_action.setToolTip(
            "Send a node's receive PDO, filling in its mapped objects by name.\n"
            "The node's PDO configuration has to be known first: load its EDS,\n"
            "or press Read from node in the CANopen pane."
        )
        rpdo_action.triggered.connect(lambda _=False: self._add_rpdo())
        add.setMenu(self.add_menu)
        fields = QPushButton("Counter / checksum...")
        fields.setToolTip(
            "Give the selected message a rolling counter, a checksum over\n"
            "its own bytes, or both -- computed fresh for every frame sent.\n"
            "Without them a receiver that checks either one rejects the lot."
        )
        fields.clicked.connect(self.edit_fields_selected)
        remove = QPushButton("Remove selected")
        remove.clicked.connect(self.remove_selected)
        bar = QHBoxLayout()
        for b in (send, stop_all, add, fields, remove):
            bar.addWidget(b)
        bar.addStretch()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.addLayout(bar)
        layout.addWidget(self.tree)

        bus.disconnected.connect(self._on_disconnected)
        self._load()

    # --- rows -------------------------------------------------------------------
    def message_count(self) -> int:
        return self.tree.topLevelItemCount()

    def item(self, row: int) -> QTreeWidgetItem:
        return self.tree.topLevelItem(row)

    def add_message(self, spec: dict) -> int:
        self._loading = True
        kind = spec.get("kind", "raw")
        item = QTreeWidgetItem(
            [
                spec.get("name", ""),
                spec.get("id", ""),
                "",
                "",
                spec.get("data", ""),
                str(spec.get("period", 100)),
                "",
                "",
            ]
        )
        item.setData(0, ROLE_KIND, kind)
        item.setData(0, ROLE_COUNTER, spec.get("counter"))
        item.setData(0, ROLE_CHECKSUM, spec.get("checksum"))
        flags = item.flags() | Qt.ItemIsEditable
        item.setFlags(flags)
        item.setCheckState(COL_EXT, Qt.Checked if spec.get("ext") else Qt.Unchecked)
        item.setCheckState(COL_FD, Qt.Checked if spec.get("fd") else Qt.Unchecked)
        item.setCheckState(COL_CYCLIC, Qt.Unchecked)
        self.tree.addTopLevelItem(item)
        item.setText(
            COL_FIELDS,
            tx_fields.describe(
                tx_fields.counter_from_dict(spec.get("counter")),
                tx_fields.checksum_from_dict(spec.get("checksum")),
            ),
        )

        if kind == "dbc":
            name = spec.get("message", "")
            item.setData(0, ROLE_MESSAGE, name)
            item.setText(COL_NAME, name)
            msg = self.dbc.message_by_name(name)
            if msg is None:
                item.setText(COL_UNIT, "database not loaded")
            else:
                item.setText(
                    COL_ID,
                    f"{msg.frame_id:08X}" if msg.is_extended_frame else f"{msg.frame_id:03X}",
                )
                item.setCheckState(COL_EXT, Qt.Checked if msg.is_extended_frame else Qt.Unchecked)
                stored = spec.get("signals", {})
                for signal in msg.signals:
                    value = stored.get(signal.name, _default_value(signal))
                    child = QTreeWidgetItem(
                        [signal.name, "", "", "", _format(value), "", "", signal.unit or ""]
                    )
                    child.setData(0, ROLE_KIND, "signal")
                    child.setFlags(child.flags() | Qt.ItemIsEditable)
                    if signal.choices:
                        # Both spellings work, so say what the names are.
                        names = "\n".join(f"  {v} = {n}" for v, n in sorted(signal.choices.items()))
                        child.setToolTip(COL_DATA, f"Type a number or a name:\n{names}")
                    item.addChild(child)
                item.setExpanded(bool(spec.get("expanded", False)))
            # message rows driven by a database are not edited directly
            item.setFlags(flags & ~Qt.ItemIsEditable)
        elif kind == "rpdo":
            node_id = int(spec.get("node", 0))
            number = int(spec.get("pdo", 1))
            item.setData(0, ROLE_NODE, node_id)
            item.setData(0, ROLE_PDO, number)
            entry = next((r for r in self.canopen.rpdos(node_id) if r[0] == number), None)
            label = entry[1] if entry else f"RPDO{number}"
            item.setText(COL_NAME, f"node {node_id} {label}")
            if entry is None:
                item.setText(COL_UNIT, "node not configured")
            else:
                stored = spec.get("signals", {})
                for variable in entry[2]:
                    child = QTreeWidgetItem(
                        [variable, "", "", "", _format(stored.get(variable, 0)), "", "", ""]
                    )
                    child.setData(0, ROLE_KIND, "signal")
                    child.setFlags(child.flags() | Qt.ItemIsEditable)
                    item.addChild(child)
                item.setExpanded(bool(spec.get("expanded", False)))
            item.setFlags(flags & ~Qt.ItemIsEditable)
        self._loading = False
        if kind in ("dbc", "rpdo"):
            self._mark_computed(self.tree.indexOfTopLevelItem(item))
            self._encode_row(self.tree.indexOfTopLevelItem(item))
        self._save()
        return self.tree.indexOfTopLevelItem(item)

    def _add_from_dbc(self) -> None:
        dialog = MessagePicker(self.dbc, self)
        if dialog.exec() == QDialog.Accepted and (name := dialog.chosen()):
            self.add_message({"kind": "dbc", "message": name, "period": 100, "expanded": True})

    def _add_rpdo(self) -> None:
        dialog = RpdoPicker(self.canopen, self)
        if dialog.exec() == QDialog.Accepted and (choice := dialog.chosen()):
            node_id, number = choice
            self.add_message(
                {"kind": "rpdo", "node": node_id, "pdo": number, "period": 100, "expanded": True}
            )

    def remove_selected(self) -> None:
        rows = sorted({self._row_of(i) for i in self.tree.selectedItems()}, reverse=True)
        for row in rows:
            if row is not None:
                self._stop_row(row)
                self.tree.takeTopLevelItem(row)
        self._rebuild_tasks()
        self._save()

    def _row_of(self, item: QTreeWidgetItem) -> int | None:
        top = item if item.parent() is None else item.parent()
        row = self.tree.indexOfTopLevelItem(top)
        return None if row < 0 else row

    # --- specs and encoding ---------------------------------------------------------
    def _spec(self, row: int) -> dict:
        item = self.item(row)
        spec = {
            "kind": item.data(0, ROLE_KIND),
            "id": item.text(COL_ID),
            "ext": item.checkState(COL_EXT) == Qt.Checked,
            "fd": item.checkState(COL_FD) == Qt.Checked,
            "data": item.text(COL_DATA),
            "period": item.text(COL_PERIOD),
            "name": item.text(COL_NAME),
            "counter": item.data(0, ROLE_COUNTER),
            "checksum": item.data(0, ROLE_CHECKSUM),
        }
        if spec["kind"] in ("dbc", "rpdo"):
            spec["expanded"] = item.isExpanded()
            spec["signals"] = {
                item.child(i).text(COL_NAME): item.child(i).text(COL_DATA)
                for i in range(item.childCount())
            }
        if spec["kind"] == "dbc":
            spec["message"] = item.data(0, ROLE_MESSAGE)
        elif spec["kind"] == "rpdo":
            spec["node"] = item.data(0, ROLE_NODE)
            spec["pdo"] = item.data(0, ROLE_PDO)
        return spec

    def _child_values(self, item: QTreeWidgetItem, names: bool = False) -> dict[str, object]:
        """The signal values as typed.

        With ``names``, text that is not a number is passed through as text:
        a DBC signal with a VAL_ table takes "Run" as readily as 1, and
        cantools maps it back. A name that is not in the table then fails the
        encode and says so, which beats the alternative -- this used to
        substitute 0.0 for anything it could not parse, so a typo in a signal
        value silently transmitted zero.
        """
        values: dict[str, object] = {}
        for i in range(item.childCount()):
            child = item.child(i)
            text = child.text(COL_DATA).strip()
            try:
                values[child.text(COL_NAME)] = float(text)
            except ValueError:
                values[child.text(COL_NAME)] = text if names else 0.0
        return values

    def _encode_row(self, row: int) -> None:
        """Rebuild a row's data (and for an RPDO its id) from the child values."""
        item = self.item(row)
        kind = item.data(0, ROLE_KIND)
        can_id = None
        if kind == "dbc":
            msg = self.dbc.message_by_name(item.data(0, ROLE_MESSAGE) or "")
            if msg is None:
                return
            try:
                # padding=False: unused bits stay 0 so the hex matches what was typed
                values = self._child_values(item, names=True)
                data = msg.encode(values, padding=False, strict=False)
            except Exception as exc:  # cantools raises for out-of-range / bad signals
                self.ctx.warn(f"TX {msg.name}: encode failed: {exc}")
                return
        elif kind == "rpdo":
            encoded = self.canopen.encode_rpdo(
                item.data(0, ROLE_NODE), item.data(0, ROLE_PDO), self._child_values(item)
            )
            if encoded is None:
                return
            can_id, data = encoded
        else:
            return
        self._loading = True
        item.setText(COL_DATA, data.hex(" ").upper())
        if can_id is not None:
            item.setText(COL_ID, f"{can_id:03X}")
        self._loading = False

    def _message(self, row: int) -> tuple[int, bytes, bool, bool, float]:
        """Parse a row; raises ValueError with a readable message."""
        spec = self._spec(row)
        label = spec.get("message") or f"row {row + 1}"
        try:
            can_id = int(spec["id"], 16)
            data = parse_hex_bytes(spec["data"])
            period = float(spec["period"] or 0) / 1000
        except ValueError as exc:
            raise ValueError(f"{label}: {exc}") from exc
        if len(data) > (64 if spec["fd"] else 8):
            raise ValueError(f"{label}: {len(data)} data bytes is too many")
        return can_id, data, spec["ext"], spec["fd"], period

    def _may_transmit(self) -> bool:
        """Ask before the first frame goes onto a real bus this session.

        The gate is on transmitting rather than on adding a row: a row that is
        sitting in the list has done nothing yet, and asking when it is built
        would train the answer out of the user before it mattered. Keyed on
        the connection, so pointing the channel at a different bus asks again.
        """
        if not is_real(self.bus.interface):
            return True
        return self.confirm.ask(
            self,
            f"transmit:{self.bus.channel_name}:{self.bus.description}",
            "Transmit onto a real CAN bus?",
            f"{self.bus.channel_name} is connected to {self.bus.description}.\n\n"
            "Sending puts these frames onto that bus, and the devices on it will "
            "act on them.\n\n"
            "Transmit on this channel?",
        )

    # --- counters and checksums -----------------------------------------------------
    def fields(self, row: int) -> tuple[Counter | None, Checksum | None]:
        """What this row computes for itself, if anything."""
        item = self.item(row)
        return (
            tx_fields.counter_from_dict(item.data(0, ROLE_COUNTER)),
            tx_fields.checksum_from_dict(item.data(0, ROLE_CHECKSUM)),
        )

    def computes(self, row: int) -> bool:
        return any(self.fields(row))

    def _payload(self, row: int, data: bytes, can_id: int) -> bytes:
        """The bytes for this row's next frame, counter and checksum included.

        Every call advances the counter, because every call is a frame going
        out. A one-shot send of a counted message counts too -- a receiver
        does not know or care which button sent it, and a manual send that
        repeated the last value would be rejected like any other repeat.
        """
        counter, checksum = self.fields(row)
        if counter is None and checksum is None:
            return data
        sent = self._sent.get(row, 0)
        self._sent[row] = sent + 1
        if (counter and counter.signal) or (checksum and checksum.signal):
            return self._payload_by_signal(row, can_id, counter, checksum, sent) or data
        # In two passes, so the hook is handed the same bytes the built-in
        # algorithms would see: the counter goes in first, and only then is
        # anybody asked what the checksum over them should be. Handing over
        # the payload as typed would be the very trap the ordering exists to
        # avoid, one layer further out.
        data = tx_fields.apply(data, counter, None, sent=sent)
        computed = None
        if checksum is not None and self.hooks is not None:
            computed = self._ask_hook(row, can_id, data)
        return tx_fields.apply(data, None, checksum, computed=computed)

    def _ask_hook(self, row: int, can_id: int, data: bytes) -> int | None:
        """A maker's checksum is nobody's standard: the hook decides the
        arithmetic, and the configuration still decides where it goes."""
        name = self.item(row).text(COL_NAME) or self.item(row).data(0, ROLE_MESSAGE) or ""
        return self.hooks.call("transmit", "checksum", name, can_id, data)

    def _payload_by_signal(
        self,
        row: int,
        can_id: int,
        counter: Counter | None,
        checksum: Checksum | None,
        sent: int,
    ) -> bytes | None:
        """The frame for a row whose fields are named database signals.

        Re-encoded rather than patched. A signal is not necessarily
        byte-aligned -- it may be three bits straddling a byte boundary, and
        in either of the two bit-numbering conventions -- so there is no byte
        to poke. The database knows where the bits go; asking it twice is
        cheaper than reimplementing it once.

        Returns None where the message is not in the database any more, and
        the caller sends the bytes it already had rather than nothing.
        """
        item = self.item(row)
        message = self.dbc.message_by_name(item.data(0, ROLE_MESSAGE) or "")
        if message is None:
            self.ctx.warn(f"TX row {row + 1}: its message is not in the loaded database")
            return None
        values = self._child_values(item, names=True)
        hook = None
        if checksum is not None and self.hooks is not None:
            hook = lambda frame: self._ask_hook(row, can_id, frame)  # noqa: E731
        try:
            return tx_fields.apply_signals(
                lambda v: message.encode(v, padding=False, strict=False),
                values,
                counter,
                checksum,
                sent=sent,
                counter_bits=_signal_bits(message, counter.signal) if counter else None,
                hook=hook,
            )
        except Exception as exc:  # a renamed signal, or one out of range
            self.ctx.warn(f"TX {message.name}: {exc}")
            return None

    def _mark_computed(self, row: int) -> None:
        """Grey out the signals a counter or checksum is going to overwrite.

        An expanded database row lists every signal as an editable value, and
        two of them may not be values at all: whatever is typed there is
        replaced as the frame is sent. A box that takes an edit and ignores
        it is a box that has lied, so these stop taking edits and say in the
        Counter / checksum column what they are instead.
        """
        item = self.item(row)
        counter, checksum = self.fields(row)
        computed = {}
        if counter is not None and counter.signal:
            computed[counter.signal] = "counter"
        if checksum is not None and checksum.signal:
            computed[checksum.signal] = checksum.algorithm
        message = (
            self.dbc.message_by_name(item.data(0, ROLE_MESSAGE) or "")
            if item.data(0, ROLE_KIND) == "dbc"
            else None
        )
        grey = self.palette().brush(QPalette.Disabled, QPalette.Text)
        for i in range(item.childCount()):
            child = item.child(i)
            label = computed.get(child.text(COL_NAME))
            child.setText(COL_FIELDS, label or "")
            if label:
                child.setFlags(child.flags() & ~Qt.ItemIsEditable)
                child.setForeground(COL_DATA, grey)
                child.setToolTip(COL_DATA, COMPUTED_TIP)
            else:
                child.setFlags(child.flags() | Qt.ItemIsEditable)
                child.setData(COL_DATA, Qt.ForegroundRole, None)
                child.setToolTip(COL_DATA, _choices_tip(message, child.text(COL_NAME)))

    def _clash(self, row: int) -> str | None:
        """Whether this row's counter and checksum would overwrite each other.

        Checked before sending as well as in the dialog, because a settings
        file written before the dialog refused this -- or edited by hand --
        should not quietly send frames with the counter stamped out of them.
        """
        return tx_fields.conflict(*self.fields(row))

    def _describe_fields(self, row: int) -> None:
        counter, checksum = self.fields(row)
        self.item(row).setText(COL_FIELDS, tx_fields.describe(counter, checksum))

    def edit_fields(self, row: int) -> bool:
        """The dialog, for one row. True if something was changed."""
        try:
            can_id, data, _ext, _fd, _period = self._message(row)
        except ValueError:
            can_id, data = 0, b"\x00" * 8
        counter, checksum = self.fields(row)
        item = self.item(row)
        message = (
            self.dbc.message_by_name(item.data(0, ROLE_MESSAGE) or "")
            if item.data(0, ROLE_KIND) == "dbc"
            else None
        )
        extra = {}
        if message is not None:
            # Offered by name, and previewed through the database, so the
            # bytes on screen are the bytes the database will pack.
            extra = {
                "signals": [s.name for s in message.signals],
                "encode": lambda v: message.encode(v, padding=False, strict=False),
                "values": self._child_values(item, names=True),
                "bits": {s.name: s.length for s in message.signals},
            }
        dialog = TxFieldsDialog(
            self,
            data,
            counter,
            checksum,
            label=item.text(COL_NAME) or (message.name if message else f"{can_id:X}"),
            **extra,
        )
        if dialog.exec() != QDialog.Accepted:
            return False
        item.setData(0, ROLE_COUNTER, tx_fields.counter_to_dict(dialog.counter()))
        item.setData(0, ROLE_CHECKSUM, tx_fields.checksum_to_dict(dialog.checksum()))
        self._describe_fields(row)
        self._mark_computed(row)
        # Restart it if it was running: which timer it belongs on has just
        # changed, and so has the payload.
        if row in self._tasks or row in self._timers:
            self._stop_row(row)
            self._start_row(row)
        self._save()
        return True

    @Slot()
    def edit_fields_selected(self) -> None:
        rows = sorted({r for i in self.tree.selectedItems() if (r := self._row_of(i)) is not None})
        if not rows:
            self.ctx.warn("TX: select a message first")
            return
        self.edit_fields(rows[0])

    # --- sending -----------------------------------------------------------------------
    def send_row(self, row: int) -> None:
        if not self._may_transmit():
            return
        if (clash := self._clash(row)) is not None:
            self.ctx.warn(f"TX row {row + 1}: {clash} -- not sent. See Counter / checksum...")
            return
        try:
            can_id, data, ext, fd, _ = self._message(row)
        except ValueError as exc:
            self.ctx.warn(f"TX {exc}")
            return
        self.bus.send(can_id, self._payload(row, data, can_id), extended=ext, fd=fd)

    @Slot()
    def send_selected(self) -> None:
        for row in sorted(
            {r for i in self.tree.selectedItems() if (r := self._row_of(i)) is not None}
        ):
            self.send_row(row)

    def eventFilter(self, watched, event):
        """Space over the list ticks or unticks Cyclic on everything selected.

        Qt's own space toggles one checkbox, and only when the cursor happens
        to be in the Cyclic column. Selecting a run of rows and pressing space
        is how you start or stop a whole set of messages at once.
        """
        if (
            watched is self.tree
            and event.type() == QEvent.KeyPress
            and event.key() == Qt.Key_Space
            and self._toggle_selected_cyclic()
        ):
            return True
        return super().eventFilter(watched, event)

    def _toggle_selected_cyclic(self) -> bool:
        rows = sorted({r for i in self.tree.selectedItems() if (r := self._row_of(i)) is not None})
        if not rows:
            return False
        # One unticked row among them means "tick them all", so select-all then
        # space starts everything and pressing it again stops everything --
        # rather than inverting each row and leaving a mixture either way.
        state = (
            Qt.Checked
            if any(self.item(r).checkState(COL_CYCLIC) != Qt.Checked for r in rows)
            else Qt.Unchecked
        )
        for row in rows:
            if self.item(row).checkState(COL_CYCLIC) != state:
                # Not _set_cyclic: that suppresses the change so nothing starts.
                self.item(row).setCheckState(COL_CYCLIC, state)
        return True

    def _start_row(self, row: int) -> None:
        if not self._may_transmit():
            self._set_cyclic(row, False)
            return
        if (clash := self._clash(row)) is not None:
            self.ctx.warn(f"TX row {row + 1}: {clash} -- not started. See Counter / checksum...")
            self._set_cyclic(row, False)
            return
        try:
            can_id, data, ext, fd, period = self._message(row)
            if period <= 0:
                raise ValueError(f"row {row + 1}: period must be > 0 for cyclic")
        except ValueError as exc:
            self.ctx.warn(f"TX {exc}")
            self._set_cyclic(row, False)
            return
        if self.computes(row):
            # Our own timer, one frame at a time. The adapter's cyclic task
            # repeats fixed bytes, and a counter has to differ on every frame
            # -- modifying a free-running task instead would race it, so some
            # frames would carry a repeated count and some would skip one,
            # which is exactly what the receiver is checking for. The cost is
            # the GUI's jitter in place of the adapter's timing.
            self._sent.setdefault(row, 0)
            timer = QTimer(self, interval=max(1, round(period * 1000)))
            timer.timeout.connect(lambda r=row: self._tick(r))
            timer.start()
            self._timers[row] = timer
            return
        task = self.bus.send_periodic(can_id, data, period, extended=ext, fd=fd)
        if task is None:
            self._set_cyclic(row, False)
            return
        self._tasks[row] = task

    def _tick(self, row: int) -> None:
        """One frame of a counted message.

        Stops itself rather than complaining once per period: a row that
        cannot be built now will not build in ten milliseconds either, and a
        warning at the period is a log nobody can read.
        """
        try:
            can_id, data, ext, fd, _period = self._message(row)
        except ValueError as exc:
            self.ctx.warn(f"TX {exc}")
            self._stop_row(row)
            self._set_cyclic(row, False)
            return
        self.bus.send(can_id, self._payload(row, data, can_id), extended=ext, fd=fd)

    def _stop_row(self, row: int) -> None:
        task = self._tasks.pop(row, None)
        if task is not None:
            task.stop()
        timer = self._timers.pop(row, None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()

    def _rebuild_tasks(self) -> None:
        """Row numbers shift when rows are removed; restart from the checkboxes."""
        for task in self._tasks.values():
            task.stop()
        for timer in self._timers.values():
            timer.stop()
            timer.deleteLater()
        self._tasks = {}
        self._timers = {}
        for row in range(self.message_count()):
            if self.item(row).checkState(COL_CYCLIC) == Qt.Checked:
                self._start_row(row)

    def _set_cyclic(self, row: int, on: bool) -> None:
        self._loading = True
        self.item(row).setCheckState(COL_CYCLIC, Qt.Checked if on else Qt.Unchecked)
        self._loading = False

    @Slot()
    def _on_stop_all_pressed(self) -> None:
        """Stop this list, and ask for every other transmit pane to stop too.

        Its own first, so that pressing it does the obvious thing even where
        nothing is listening -- a transmit pane on its own in a test, or one
        built by a plugin.
        """
        self.stop_all()
        self.stop_all_requested.emit()

    def cyclic_count(self) -> int:
        """How many messages in this list are repeating right now."""
        return len(self._tasks) + len(self._timers)

    @Slot()
    def stop_all(self) -> None:
        """Stop everything repeating in *this* list."""
        for row in [*self._tasks, *self._timers]:
            self._stop_row(row)
            self._set_cyclic(row, False)

    @Slot()
    def _on_disconnected(self) -> None:
        self._tasks.clear()  # the bus stops its own tasks
        for timer in self._timers.values():  # ours it does not know about
            timer.stop()
            timer.deleteLater()
        self._timers.clear()
        for row in range(self.message_count()):
            self._set_cyclic(row, False)

    # --- edits ---------------------------------------------------------------------------
    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._loading:
            return
        row = self._row_of(item)
        if row is None:
            return
        if item.parent() is not None:  # a signal value changed
            self._encode_row(row)
        if column == COL_CYCLIC and item.parent() is None:
            if item.checkState(COL_CYCLIC) == Qt.Checked:
                self._start_row(row)
            else:
                self._stop_row(row)
        elif row in self._tasks:  # live edit of a running cyclic message
            self._stop_row(row)
            self._start_row(row)
        self._save()

    def _on_double_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        if item.parent() is None and not (
            item.flags() & Qt.ItemIsEditable and column in (COL_NAME, COL_ID, COL_DATA, COL_PERIOD)
        ):
            row = self._row_of(item)
            if row is not None:
                self.send_row(row)

    # --- persistence ------------------------------------------------------------------------
    def _save(self) -> None:
        if not self._loading:
            self.ctx.settings.set(
                f"{self.key}.messages", [self._spec(r) for r in range(self.message_count())]
            )

    def _load(self) -> None:
        for spec in self.ctx.settings.get(f"{self.key}.messages", []):
            self.add_message(spec)

    @Slot()
    def refresh_sources(self) -> None:
        """Rebuild database / RPDO rows after a DBC or node configuration changes."""
        specs = [self._spec(r) for r in range(self.message_count())]
        if not any(spec["kind"] in ("dbc", "rpdo") for spec in specs):
            return
        self.stop_all()
        self._loading = True
        self.tree.clear()
        self._loading = False
        for spec in specs:
            self.add_message(spec)


def _signal_bits(message, name: str) -> int | None:
    """How many bits a named signal has, so a counter knows where to wrap.

    A placement knows it is a nibble; a signal does not say so itself, and a
    four-bit counter that counted to 255 would be rejected by the receiver at
    the sixteenth frame.
    """
    for signal in message.signals:
        if signal.name == name:
            return signal.length
    return None


def _choices_tip(message, name: str) -> str:
    """The "type a number or a name" tooltip, or nothing.

    Rebuilt rather than remembered, because a signal that stops being a
    computed field has to get its own tooltip back.
    """
    if message is None:
        return ""
    for signal in message.signals:
        if signal.name == name and signal.choices:
            names = "\n".join(f"  {v} = {n}" for v, n in sorted(signal.choices.items()))
            return f"Type a number or a name:\n{names}"
    return ""


def _default_value(signal) -> float:
    if signal.initial is not None:
        return signal.initial
    if signal.minimum is not None and signal.minimum > 0:
        return signal.minimum
    return 0


def _format(value) -> str:
    """Text for a signal value.

    A signal with a VAL_ table and a start value hands back cantools'
    NamedSignalValue -- "Run" rather than 1. It is not a str subclass and it
    cannot be formatted as a number, so a bare f"{value:g}" raises on any DBC
    that names its enumerations, which most real ones do.
    """
    if isinstance(value, (int, float)):
        return f"{value:g}"
    return str(value)
