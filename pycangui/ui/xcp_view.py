# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""XCP pane: connect to a slave, load an A2L, read/write characteristics and
measurements, and stream measurements into the Plot via the signal hub."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QPalette, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressDialog,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pycangui.core import workspace_files
from pycangui.core.components import COMPONENTS
from pycangui.core.context import Context
from pycangui.ui import folders, fonts, keep_file, messages, seedkey_view
from pycangui.ui.column_widths import ColumnWidths
from pycangui.xcp import RESOURCE_CAL
from pycangui.xcp.manager import XcpManager

ROLE_NAME = Qt.UserRole

#: A big A2L takes seconds to read and longer to list. Nothing is shown for
#: one that is done inside this long, which is nearly all of them.
PROGRESS_AFTER_MS = 500
PROGRESS_STEPS = 1000
#: How much of the time is the reading, the rest being the listing: as
#: measured on an 18 MB file with 44,000 parameters.
READING_SHARE = 0.3
#: How many rows are listed between one report and the next.
ROWS_A_REPORT = 500


class _CancelledError(Exception):
    """Cancel was pressed while an A2L was being read."""


class _LoadProgress:
    """A box that says an A2L is being loaded, for one that takes a while.

    The loading is done on the window's own thread, so the box is what lets
    the window repaint while it goes on: each report is a moment in which it
    can. Modal for the same reason -- a window that repaints is one that
    takes clicks, and Load pressed again half way through a load is not
    something to find out about.

    Cancel is for the reading. Once the file is read it is the one in use,
    and the listing of it is carried through.
    """

    def __init__(self, parent: QWidget, name: str) -> None:
        self.box = QProgressDialog(f"Reading {name}...", "Cancel", 0, PROGRESS_STEPS, parent)
        self.box.setWindowTitle("Load A2L")
        self.box.setWindowModality(Qt.ApplicationModal)
        self.box.setMinimumDuration(PROGRESS_AFTER_MS)
        # Not closed by reaching the end: the end is said by done().
        self.box.setAutoClose(False)
        self.box.setAutoReset(False)
        self._name = name
        self._listing = False
        self.box.setValue(0)

    def reading(self, part: float) -> None:
        self.box.setValue(int(part * READING_SHARE * PROGRESS_STEPS))
        if self.box.wasCanceled():
            raise _CancelledError

    def listing(self, part: float) -> None:
        if not self._listing:
            self._listing = True
            self.box.setCancelButton(None)
            self.box.setLabelText(f"Listing {self._name}...")
        share = READING_SHARE + part * (1 - READING_SHARE)
        self.box.setValue(int(share * PROGRESS_STEPS))

    def done(self) -> None:
        self.box.reset()
        self.box.hide()
        self.box.deleteLater()


CONNECT_TIP = (
    "XCP CONNECT to the slave on the identifiers above.\n"
    "This is the XCP session, not the CAN channel."
)
NO_IDS_TIP = "Fill in the command and response identifiers first."
#: Said on both identifier boxes, because an empty box with no explanation
#: is a worse start than a wrong number: it has to say where the answer
#: comes from.
ID_TIP = (
    "The identifier the slave listens on, and the one it answers with.\n"
    "XCP calls them the command and response identifiers, CCP the CRO\n"
    "and the DTO. Neither protocol standardises a pair: they come from\n"
    "the A2L or from the supplier. The demo devices use 7A0/7A1 for XCP\n"
    "and 7B0/7B1 for CCP.\n"
    "\n"
    "Whether they are 11-bit or 29-bit is in how they are written: anything\n"
    "above 7FF is 29-bit, and so is an id written out in eight digits."
)
FROM_A2L_TIP = (
    "Take the identifiers from the A2L, which gives them for XCP on CAN.\n"
    "Ticked, the two boxes show the A2L's and are not typed into; unticked,\n"
    "they are yours again. Only shown for an A2L that has them."
)
LIMITS_TITLE = "Outside the A2L's limits"
LIMITS_TEXT = (
    "{name} is given limits of {lower:g} to {upper:g}{unit} by the A2L, and\n"
    "{value:g} is outside them.\n"
    "\n"
    "The limits are whoever wrote the A2L saying what the controller is meant\n"
    "to be given. Write {value:g} anyway?"
)


def is_extended(typed: str) -> bool:
    """Whether an id, as it is written, is a 29-bit one: above 7FF, or eight digits.

    The rule CAN Transmit and the ASCII Log have, in place of a tick box.
    """
    typed = typed.strip()
    try:
        return int(typed, 16) > 0x7FF or len(typed) == 8
    except ValueError:
        return False


def id_text(can_id: int, extended: bool) -> str:
    return f"{can_id:08X}" if extended else f"{can_id:03X}"


STATION_TIP = (
    "Which controller on these identifiers is being talked to. CCP\n"
    "addresses a station as well as a pair of ids, so several can share\n"
    "one pair and answer in turn. XCP has no such thing, so the box is\n"
    "only shown for a CCP engine."
)
SEED_KEY_TIP = (
    "The standard seed and key DLL, which is how an ECU's unlock algorithm\n"
    "ships. Used when hooks/xcp.py::compute_key returns None, so a hook\n"
    "always wins where there is one.\n"
    "\n"
    "The same DLL serves UDS SecurityAccess: one ECU ships one algorithm,\n"
    "and naming the file twice would be pycangui's filing system showing."
)
ENGINE_TIP = (
    "The XCP or CCP engine, which is a replaceable component. Add one of\n"
    "your own, or replace this one, with a file in the folder Tools > Open\n"
    "custom components folder opens."
)
#: Everything a row can be found by, worked out once when it is built.
ROLE_SEARCH = Qt.UserRole + 1


def _specs(kind: str):
    return COMPONENTS.specs(kind)


def _searchable(param) -> str:
    """What a row can be found by: name, kind, type, unit and address.

    The address in both shapes somebody might have it in -- a map file says
    0x1234, a spreadsheet says 4660 -- so neither has to be converted before
    it can be looked for.
    """
    return " ".join(
        str(part)
        for part in (
            param.name,
            param.kind,
            param.shape,
            param.datatype,
            param.unit,
            param.description,
            f"0x{param.address:x}",
            param.address,
        )
    ).lower()


def _kind(param) -> str:
    """MEAS or CHAR for a single value; what it is instead for anything else."""
    if param.kind == "CHARACTERISTIC" and param.shape != "VALUE":
        return param.shape
    return param.kind[:4]


def _type(param) -> str:
    """The data type, and for an array how many of it: ``UWORD[16]``, ``UBYTE[2][24]``."""
    if not param.is_array:
        return param.datatype
    return param.datatype + "".join(f"[{size}]" for size in param.sizes)


def _about(param) -> str:
    """A parameter's tooltip: what it is, where, and why it is not read if it is not."""
    lines = [param.description] if param.description else []
    where = f"0x{param.address:X}" + (f", extension {param.extension}" if param.extension else "")
    lines.append(where + (f", {param.datatype}" if param.datatype else ""))
    if param.is_array:
        lines.append(
            f"{param.count} values. Double-click to read them all;\n"
            "expand the row to read, write or plot one."
        )
        if len(param.sizes) > 1:
            shape = " by ".join(str(size) for size in param.sizes)
            wrote = " ".join(str(size) for size in param.written)
            how = "column by column" if param.column_dir else "row by row"
            lines.append(
                f"{shape}, named in the order they are stored: the last index\n"
                f"changes fastest. The A2L gives the sizes as {wrote}, stored {how}."
            )
    if param.bit_mask:
        lines.append(f"Bit mask 0x{param.bit_mask:X}")
    if param.lower is not None and param.upper is not None:
        lines.append(f"{param.lower:g} to {param.upper:g}")
    if param.conversion is not None and not param.conversion.exact:
        lines.append(
            f"Shown raw: its conversion, {param.conversion.name} ({param.conversion.kind}),\n"
            "is not one pycangui works out."
        )
    if param.read_only:
        lines.append("Marked read-only in the A2L.")
    if param.unreadable:
        lines.append(f"Not read: {param.unreadable}.")
    return "\n".join(lines)


def _as_written(cfg: dict, key: str) -> str:
    """A saved identifier, written so that it says how wide it is.

    Saved while there was a 29-bit box, a short id could have the box ticked;
    that one is written out in eight digits, which is what the tick meant.
    """
    typed = str(cfg.get(key, "") or "")
    if cfg.get("ext") and not is_extended(typed):
        try:
            return id_text(int(typed, 16), True)
        except ValueError:
            pass
    return typed


class XcpView(QWidget):
    def __init__(self, manager: XcpManager, ctx: Context) -> None:
        super().__init__()
        self.manager = manager
        self.ctx = ctx
        mono = fonts.mono()
        cfg = ctx.settings.get("xcp.config", {})

        # --- connection bar --------------------------------------------------
        # Empty until somebody says otherwise. XCP on CAN fixes no
        # identifiers, so any default here is a guess dressed up as a
        # setting: it would be sent to whatever happens to answer on it,
        # and it would label frames in the trace as XCP that are not.
        self.cmd_id = QLineEdit(_as_written(cfg, "cmd_id"))
        self.cmd_id.setPlaceholderText("none")
        self.cmd_id.setToolTip(ID_TIP)
        self.cmd_id.setFont(mono)
        self.cmd_id.setFixedWidth(fonts.width_for(self.cmd_id, "1FFFFFFF"))
        self.cmd_id.textChanged.connect(lambda _t: self._ids_changed())
        self.res_id = QLineEdit(_as_written(cfg, "res_id"))
        self.res_id.setPlaceholderText("none")
        self.res_id.setToolTip(ID_TIP)
        self.res_id.setFont(mono)
        self.res_id.setFixedWidth(fonts.width_for(self.res_id, "1FFFFFFF"))
        self.res_id.textChanged.connect(lambda _t: self._ids_changed())
        self.station = QLineEdit(str(cfg.get("station", "1")))
        self.station.setToolTip(STATION_TIP)
        self.station.setFont(mono)
        self.station.setFixedWidth(fonts.width_for(self.station, "FFFF"))
        self.station_label = QLabel("Station")
        # No 29-bit box: how an identifier is written says how wide it is, as
        # it does in CAN Transmit. What is asked instead is whose identifiers
        # these are -- typed here, or the A2L's.
        self.from_a2l = QCheckBox("IDs from A2L")
        self.from_a2l.setToolTip(FROM_A2L_TIP)
        self.from_a2l.setChecked(bool(cfg.get("from_a2l", False)))
        self.from_a2l.toggled.connect(lambda _on: self._whose_ids())
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setCheckable(True)
        self.connect_btn.toggled.connect(self._toggle_connect)
        unlock = QPushButton("Unlock CAL")
        unlock.setToolTip(
            "GET_SEED and UNLOCK for the calibration resource, which most\n"
            "slaves want before a value can be written. The key comes from\n"
            "hooks/xcp.py::compute_key, and where that returns None, from\n"
            "the seed and key DLL beside this button."
        )
        unlock.clicked.connect(lambda: self.manager.unlock(RESOURCE_CAL))
        seed_key = QPushButton("Seed and key DLL...")
        seed_key.setToolTip(SEED_KEY_TIP)
        seed_key.clicked.connect(lambda: seedkey_view.ask(self.ctx, self))
        load = QPushButton("Load A2L...")
        load.setToolTip(
            "Read the measurements and characteristics out of an A2L, so they\n"
            "can be used by name and in physical units rather than by address."
        )
        load.clicked.connect(self._load_a2l)
        self.engine_box = QComboBox()
        self.engine_box.setToolTip(ENGINE_TIP)
        for spec in _specs("xcp"):
            self.engine_box.addItem(spec.name, spec.name)
            last = self.engine_box.count() - 1
            self.engine_box.setItemData(last, spec.description, Qt.ToolTipRole)
        index = self.engine_box.findData(manager.component_name)
        if index >= 0:
            self.engine_box.setCurrentIndex(index)
        self.engine_box.currentTextChanged.connect(manager.set_component)
        bar = QHBoxLayout()
        bar.addWidget(QLabel("Engine"))
        bar.addWidget(self.engine_box)
        bar.addWidget(QLabel("Tx ID"))
        bar.addWidget(self.cmd_id)
        bar.addWidget(QLabel("Rx ID"))
        bar.addWidget(self.res_id)
        bar.addWidget(self.station_label)
        bar.addWidget(self.station)
        bar.addWidget(self.from_a2l)
        bar.addWidget(self.connect_btn)
        bar.addWidget(unlock)
        bar.addWidget(seed_key)
        bar.addWidget(load)
        bar.addStretch()

        # --- parameter tree -----------------------------------------------------
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Parameter", "Kind", "Type", "Value", "Unit", "Plot"])
        self.tree.setFont(mono)
        self.widths = ColumnWidths(self.tree, ctx.settings, "xcp")
        self.tree.itemDoubleClicked.connect(self._on_double_clicked)
        self.tree.itemExpanded.connect(self._fill)
        self.tree.itemChanged.connect(self._on_item_changed)
        self._items: dict[str, QTreeWidgetItem] = {}
        #: Told how far the listing of an A2L has got, while one is being
        #: loaded with the box up. See ``load_a2l``.
        self._listing_progress = None
        #: What each value last read as, to put back when a write is not gone through with.
        self._last: dict[str, str] = {}
        self._updating = False

        read_btn = QPushButton("Read selected")
        read_btn.setToolTip(
            "Read the selected measurements once. Tick Plot to keep reading\n"
            "them into the signal hub, where they can be plotted."
        )
        read_btn.clicked.connect(self._read_selected)
        # An A2L from a real ECU runs to hundreds of parameters, and the one
        # wanted is known by name or by address. Same shape as the CANopen
        # pane's object filter, because it is the same problem.
        self.search = QLineEdit()
        self.search.setPlaceholderText("filter parameters...")
        self.search.setToolTip(
            "Match on the name, the address, the type or the unit; several\n"
            "words must all match, and MEASUREMENT or CHARACTERISTIC narrows\n"
            "it to one kind. Nothing is discarded -- clearing the box brings\n"
            "the rest back."
        )
        self.search.textChanged.connect(lambda _t: self._apply_filter())
        self.plotted_only = QPushButton("Plotted")
        self.plotted_only.setCheckable(True)
        self.plotted_only.setToolTip(
            "Show only the parameters ticked for the plot, which is what a\n"
            "filter would otherwise hide while they went on being polled."
        )
        self.plotted_only.toggled.connect(lambda _on: self._apply_filter())
        # Which A2L the names came from, and the way to be rid of it. Without
        # this the file is remembered for ever with nothing on screen saying
        # which one it is, and a file that has moved can only be replaced.
        self.a2l_label = QLabel()
        self.remove_a2l_btn = QPushButton("Remove A2L")
        self.remove_a2l_btn.setToolTip(
            "Forget this A2L. The parameters go with it; the connection and\n"
            "the identifiers stay as they are."
        )
        self.remove_a2l_btn.clicked.connect(self._remove_a2l)
        row = QHBoxLayout()
        row.addWidget(self.a2l_label)
        row.addWidget(self.remove_a2l_btn)
        row.addWidget(self.search, 1)
        row.addWidget(self.plotted_only)
        row.addWidget(read_btn)

        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setFont(mono)
        self.output.setMaximumBlockCount(1000)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.addLayout(bar)
        layout.addLayout(row)
        layout.addWidget(self.tree, 1)
        layout.addWidget(self.output)

        manager.result.connect(self._append)
        manager.connected.connect(self._on_connected)
        manager.a2l_loaded.connect(lambda _n: self._populate())
        manager.a2l_loaded.connect(lambda _n: self._show_a2l())
        manager.a2l_loaded.connect(lambda _n: self._whose_ids())
        manager.value.connect(self._on_value)
        manager.connected.connect(lambda _on: self._ids_changed())
        self.engine_box.currentTextChanged.connect(lambda _n: self._engine_changed())
        if manager.a2l is not None:
            self._populate()
        self._show_a2l()
        self._ids_changed()
        self._engine_changed()
        self._whose_ids()

    # --- connection -------------------------------------------------------------
    def _engine_changed(self) -> None:
        """Show what this engine needs and hide what it does not.

        A station box on an XCP pane is a box nobody can answer, and its
        absence on a CCP pane is a controller nobody can reach. The engine
        says which it is; the pane does not know the protocols.
        """
        wanted = self.manager.needs_station
        self.station.setVisible(wanted)
        self.station_label.setVisible(wanted)
        if hasattr(self, "from_a2l"):
            self._whose_ids()  # the A2L's are XCP's, and no use to a CCP engine

    def _a2l_ids(self):
        """The identifiers the A2L gives, where it gives them and they are this engine's."""
        a2l = self.manager.a2l
        if a2l is None or a2l.xcp_on_can is None or self.manager.protocol != "XCP":
            return None
        return a2l.xcp_on_can

    def _whose_ids(self) -> None:
        """Show the A2L's identifiers in the boxes, or hand the boxes back.

        The tick box is only there for an A2L that has identifiers to give.
        One that has none leaves the boxes as they were, typed.
        """
        ids = self._a2l_ids()
        self.from_a2l.setVisible(ids is not None)
        using = ids is not None and self.from_a2l.isChecked()
        if using:
            self.cmd_id.setText(id_text(ids.command_id, ids.extended))
            self.res_id.setText(id_text(ids.response_id, ids.extended))
        for box in (self.cmd_id, self.res_id):
            box.setReadOnly(using)
            box.setEnabled(not using)

    def _ids_changed(self) -> None:
        """Connect is offered only once there is somewhere to connect to.

        Better than letting it be pressed and answering with an error: the
        button itself says what the pane is waiting for, and its tooltip
        says where the numbers come from.
        """
        ready = self._id(self.cmd_id) is not None and self._id(self.res_id) is not None
        self.connect_btn.setEnabled(ready or self.manager.is_connected)
        self.connect_btn.setToolTip(CONNECT_TIP if ready else NO_IDS_TIP)

    @staticmethod
    def _id(box: QLineEdit) -> int | None:
        """What is typed, as an identifier, or None while it is not one."""
        try:
            value = int(box.text().strip(), 16)
        except ValueError:
            return None
        return value if 0 <= value <= 0x1FFFFFFF else None

    def _config(self) -> None:
        # One width for the pair: a slave is not addressed on an 11-bit id
        # and answered on a 29-bit one, so either being 29-bit says both are.
        extended = is_extended(self.cmd_id.text()) or is_extended(self.res_id.text())
        self.manager.set_ids(int(self.cmd_id.text(), 16), int(self.res_id.text(), 16), extended)
        self.manager.set_station(int(self.station.text().strip() or "0", 16))
        self.ctx.settings.set(
            "xcp.config",
            {
                "cmd_id": self.cmd_id.text(),
                "res_id": self.res_id.text(),
                # Kept, for a pycangui from before the box went.
                "ext": extended,
                "from_a2l": self.from_a2l.isChecked(),
                "station": self.station.text(),
            },
        )

    @Slot(bool)
    def _toggle_connect(self, on: bool) -> None:
        if on:
            try:
                self._config()
            except ValueError as exc:
                self._append(f"XCP: bad id: {exc}")
                self.connect_btn.setChecked(False)
                return
            self.manager.connect_slave()
        else:
            self.manager.disconnect_slave()

    @Slot(bool)
    def _on_connected(self, on: bool) -> None:
        self.connect_btn.blockSignals(True)
        self.connect_btn.setChecked(on)
        self.connect_btn.setText("Disconnect" if on else "Connect")
        self.connect_btn.blockSignals(False)

    def _load_a2l(self) -> None:
        path = folders.open_file(
            self, self.ctx, folders.A2L, "Load A2L", "A2L (*.a2l);;All files (*)", self.ctx.user_dir
        )
        if not path:
            return

        value = keep_file.offer_and_load(self, self.ctx, path, workspace_files.A2L, self.load_a2l)
        if value is not None:
            self.ctx.settings.set("xcp.a2l", value)

    def _show_a2l(self) -> None:
        """Name the A2L in use, or say there is none."""
        remembered = str(self.ctx.settings.get("xcp.a2l", "") or "")
        loaded = self.manager.a2l is not None
        if loaded:
            name = Path(self.manager.a2l.path).name if self.manager.a2l.path else "an A2L"
            self.a2l_label.setText(f"A2L: {name}")
        elif remembered:
            # Remembered and not loaded means it has moved or been deleted.
            # Named here as well as in the log, because this is where somebody
            # is when they wonder why the parameters have gone.
            self.a2l_label.setText(f"A2L: {Path(remembered).name} (missing)")
        else:
            self.a2l_label.setText("No A2L loaded")
        self.a2l_label.setToolTip(remembered)
        self.remove_a2l_btn.setEnabled(loaded or bool(remembered))

    def _remove_a2l(self) -> None:
        """Forget the A2L, whether or not the file is still where it was."""
        self.manager.clear_a2l()
        self.ctx.settings.remove("xcp.a2l")
        self._populate()
        self._show_a2l()
        self._append("A2L removed")

    # --- parameter tree --------------------------------------------------------------
    def load_a2l(self, path: str) -> bool:
        """Load an A2L, saying so if it takes a while. False if it was not loaded.

        Cancelled, or not readable, and the A2L there was is still the one in use.
        """
        progress = _LoadProgress(self, Path(path).name)
        self._listing_progress = progress.listing
        try:
            self.manager.load_a2l(path, progress.reading)
        except _CancelledError:
            self._append("A2L load cancelled")
            return False
        except Exception as exc:  # parser is best-effort
            self._append(f"A2L load failed: {exc}")
            return False
        finally:
            self._listing_progress = None
            progress.done()
        return True

    def _populate(self) -> None:
        self._updating = True
        self.tree.clear()
        self._items.clear()
        self._last.clear()
        a2l = self.manager.a2l
        if a2l is not None:
            grey = self.palette().brush(QPalette.Disabled, QPalette.Text)
            report, rows = self._listing_progress, len(a2l.parameters)
            for row, param in enumerate(a2l.parameters.values()):
                if report is not None and not row % ROWS_A_REPORT:
                    report(row / rows)
                item = QTreeWidgetItem([param.name, _kind(param), _type(param), "", param.unit, ""])
                item.setData(0, ROLE_NAME, param.name)
                item.setData(0, ROLE_SEARCH, _searchable(param))
                item.setToolTip(0, _about(param))
                if param.writable:
                    item.setFlags(item.flags() | Qt.ItemIsEditable)
                if param.is_array:
                    # Its values are rows of their own, made when the row is
                    # opened: a big file has a hundred thousand of them, and
                    # anybody looks at a handful.
                    item.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
                elif param.readable:
                    item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                    item.setCheckState(5, Qt.Unchecked)
                else:
                    # Listed, so the file is seen whole; grey, and nothing to
                    # tick, because there is nothing here that reads it.
                    for column in range(self.tree.columnCount()):
                        item.setForeground(column, grey)
                        item.setToolTip(column, _about(param))
                self.tree.addTopLevelItem(item)
                self._items[param.name] = item
        self._updating = False
        self._apply_filter()  # a new A2L arrives into whatever filter is set

    def _fill(self, item: QTreeWidgetItem) -> None:
        """Give an array's row a row for each of its values, the first time it is wanted."""
        a2l = self.manager.a2l
        array = a2l.find(item.data(0, ROLE_NAME) or "") if a2l is not None else None
        if array is None or not array.is_array or item.childCount():
            return
        was_updating, self._updating = self._updating, True
        for element in a2l.elements(array):
            child = QTreeWidgetItem(
                [element.name, _kind(element), element.datatype, "", element.unit, ""]
            )
            child.setData(0, ROLE_NAME, element.name)
            child.setToolTip(0, _about(element))
            flags = child.flags() | Qt.ItemIsUserCheckable
            child.setFlags(flags | Qt.ItemIsEditable if element.writable else flags)
            child.setCheckState(5, Qt.Unchecked)
            item.addChild(child)
            self._items[element.name] = child
        self._updating = was_updating

    def _apply_filter(self) -> None:
        """Hide what does not match. Nothing is unloaded and nothing is read."""
        needles = self.search.text().lower().split()
        polled_only = self.plotted_only.isChecked()
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            # An array is plotted through its values, so it is one of those
            # being plotted when any of them is.
            plotted = item.checkState(5) == Qt.Checked or any(
                item.child(c).checkState(5) == Qt.Checked for c in range(item.childCount())
            )
            if polled_only and not plotted:
                item.setHidden(True)
                continue
            haystack = item.data(0, ROLE_SEARCH) or ""
            item.setHidden(not all(needle in haystack for needle in needles))

    def _on_double_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        if column != 3:
            self._read(item)

    def _read(self, item: QTreeWidgetItem) -> None:
        """Read a row: one value, or every value of an array, into rows made for them."""
        self._fill(item)
        if item.childCount():
            item.setExpanded(True)
        self.manager.read(item.data(0, ROLE_NAME))

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._updating:
            return
        name = item.data(0, ROLE_NAME)
        if column == 3:
            if not self._within_limits_or_meant(name, item.text(3)):
                self._updating = True
                item.setText(3, self._last.get(name, ""))  # as it was: nothing was written
                self._updating = False
                return
            self.manager.write(name, item.text(3), beyond_limits=True)
        elif column == 5:
            self.manager.set_polled(name, item.checkState(5) == Qt.Checked)
            if self.plotted_only.isChecked():
                self._apply_filter()  # unticking one while showing only those

    def _within_limits_or_meant(self, name: str, text: str) -> bool:
        """Whether to go ahead with a write: it is inside the limits, or was agreed to.

        Asked every time and never remembered. The limits are the one thing
        in the file that says what the controller is meant to be given, and
        the next value outside them is a different mistake from this one.
        """
        outside = self.manager.beyond_limits(name, text)
        if outside is None:
            return True
        value, lower, upper = outside
        param = self.manager.a2l.find(name)
        unit = f" {param.unit}" if param is not None and param.unit else ""
        answer = messages.question(
            self,
            LIMITS_TITLE,
            LIMITS_TEXT.format(name=name, lower=lower, upper=upper, unit=unit, value=value),
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        return answer == QMessageBox.Yes

    def _read_selected(self) -> None:
        for item in self.tree.selectedItems():
            self._read(item)

    @Slot(str, float)
    def _on_value(self, name: str, value: float) -> None:
        item = self._items.get(name)
        if item is not None:
            # Put back as it was, not to False: a value can arrive while the
            # list is being filled, which is updating of its own.
            was_updating, self._updating = self._updating, True
            item.setText(3, self.manager.shown(name, value))
            self._updating = was_updating
        self._last[name] = self.manager.shown(name, value)

    @Slot(str)
    def _append(self, text: str) -> None:
        self.output.moveCursor(QTextCursor.End)
        self.output.appendPlainText(text)
        if text.startswith("XCP") or "A2L" in text:
            self.ctx.log(text)
