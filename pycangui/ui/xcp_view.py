# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""XCP pane: connect to a slave, load an A2L, read/write characteristics and
measurements, and stream measurements into the Plot via the signal hub."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pycangui.core import workspace_files
from pycangui.core.backends import BACKENDS
from pycangui.core.context import Context
from pycangui.ui import folders, keep_file
from pycangui.xcp import RESOURCE_CAL
from pycangui.xcp.manager import XcpManager

ROLE_NAME = Qt.UserRole
#: Everything a row can be found by, worked out once when it is built.
ROLE_SEARCH = Qt.UserRole + 1


def _specs(kind: str):
    return BACKENDS.specs(kind)


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
            param.datatype,
            param.unit,
            param.description,
            f"0x{param.address:x}",
            param.address,
        )
    ).lower()


class XcpView(QWidget):
    def __init__(self, manager: XcpManager, ctx: Context) -> None:
        super().__init__()
        self.manager = manager
        self.ctx = ctx
        mono = QFont("Consolas", 9)
        cfg = ctx.settings.get("xcp.config", {})

        # --- connection bar --------------------------------------------------
        self.cmd_id = QLineEdit(cfg.get("cmd_id", "7A0"))
        self.cmd_id.setFont(mono)
        self.cmd_id.setFixedWidth(70)
        self.res_id = QLineEdit(cfg.get("res_id", "7A1"))
        self.res_id.setFont(mono)
        self.res_id.setFixedWidth(70)
        self.ext = QCheckBox("29-bit")
        self.ext.setToolTip("Address the slave with 29-bit identifiers rather than 11-bit")
        self.ext.setChecked(cfg.get("ext", False))
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setToolTip(
            "XCP CONNECT to the slave on the identifiers above.\n"
            "This is the XCP session, not the CAN channel."
        )
        self.connect_btn.setCheckable(True)
        self.connect_btn.toggled.connect(self._toggle_connect)
        unlock = QPushButton("Unlock CAL")
        unlock.setToolTip(
            "GET_SEED and UNLOCK for the calibration resource, which most\n"
            "slaves want before a value can be written. The key comes from\n"
            "hooks/xcp.py::compute_key."
        )
        unlock.clicked.connect(lambda: self.manager.unlock(RESOURCE_CAL))
        load = QPushButton("Load A2L...")
        load.setToolTip(
            "Read the measurements and characteristics out of an A2L, so they\n"
            "can be used by name and in physical units rather than by address."
        )
        load.clicked.connect(self._load_a2l)
        self.backend = QComboBox()
        self.backend.setToolTip("XCP implementation (add your own in the backends folder)")
        for spec in _specs("xcp"):
            self.backend.addItem(spec.name, spec.name)
            self.backend.setItemData(self.backend.count() - 1, spec.description, Qt.ToolTipRole)
        index = self.backend.findData(manager.backend_name)
        if index >= 0:
            self.backend.setCurrentIndex(index)
        self.backend.currentTextChanged.connect(manager.set_backend)
        bar = QHBoxLayout()
        bar.addWidget(QLabel("Engine"))
        bar.addWidget(self.backend)
        bar.addWidget(QLabel("Cmd ID"))
        bar.addWidget(self.cmd_id)
        bar.addWidget(QLabel("Resp ID"))
        bar.addWidget(self.res_id)
        bar.addWidget(self.ext)
        bar.addWidget(self.connect_btn)
        bar.addWidget(unlock)
        bar.addWidget(load)
        bar.addStretch()

        # --- parameter tree -----------------------------------------------------
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Parameter", "Kind", "Type", "Value", "Unit", "Plot"])
        self.tree.setFont(mono)
        self.tree.header().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.tree.header().setStretchLastSection(True)
        self.tree.itemDoubleClicked.connect(self._on_double_clicked)
        self.tree.itemChanged.connect(self._on_item_changed)
        self._items: dict[str, QTreeWidgetItem] = {}
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
        manager.value.connect(self._on_value)
        if manager.a2l is not None:
            self._populate()
        self._show_a2l()

    # --- connection -------------------------------------------------------------
    def _config(self) -> None:
        self.manager.set_ids(
            int(self.cmd_id.text(), 16), int(self.res_id.text(), 16), self.ext.isChecked()
        )
        self.ctx.settings.set(
            "xcp.config",
            {
                "cmd_id": self.cmd_id.text(),
                "res_id": self.res_id.text(),
                "ext": self.ext.isChecked(),
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
        if path:
            try:
                self.manager.load_a2l(path)
            except Exception as exc:  # parser is best-effort
                self._append(f"A2L load failed: {exc}")
                return
            self.ctx.settings.set(
                "xcp.a2l", keep_file.offer(self, self.ctx, path, workspace_files.A2L)
            )

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
    def _populate(self) -> None:
        self._updating = True
        self.tree.clear()
        self._items.clear()
        a2l = self.manager.a2l
        if a2l is not None:
            for param in a2l.parameters.values():
                item = QTreeWidgetItem(
                    [param.name, param.kind[:4], param.datatype, "", param.unit, ""]
                )
                item.setData(0, ROLE_NAME, param.name)
                item.setData(0, ROLE_SEARCH, _searchable(param))
                if param.writable:
                    item.setFlags(item.flags() | Qt.ItemIsEditable)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(5, Qt.Unchecked)
                self.tree.addTopLevelItem(item)
                self._items[param.name] = item
        self._updating = False
        self._apply_filter()  # a new A2L arrives into whatever filter is set

    def _apply_filter(self) -> None:
        """Hide what does not match. Nothing is unloaded and nothing is read."""
        needles = self.search.text().lower().split()
        polled_only = self.plotted_only.isChecked()
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if polled_only and item.checkState(5) != Qt.Checked:
                item.setHidden(True)
                continue
            haystack = item.data(0, ROLE_SEARCH) or ""
            item.setHidden(not all(needle in haystack for needle in needles))

    def _on_double_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        if column != 3:
            self.manager.read(item.data(0, ROLE_NAME))

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._updating:
            return
        name = item.data(0, ROLE_NAME)
        if column == 3:
            self.manager.write(name, item.text(3))
        elif column == 5:
            self.manager.set_polled(name, item.checkState(5) == Qt.Checked)
            if self.plotted_only.isChecked():
                self._apply_filter()  # unticking one while showing only those

    def _read_selected(self) -> None:
        for item in self.tree.selectedItems():
            self.manager.read(item.data(0, ROLE_NAME))

    @Slot(str, float)
    def _on_value(self, name: str, value: float) -> None:
        item = self._items.get(name)
        if item is not None:
            self._updating = True
            item.setText(3, f"{value:g}")
            self._updating = False

    @Slot(str)
    def _append(self, text: str) -> None:
        self.output.moveCursor(QTextCursor.End)
        self.output.appendPlainText(text)
        if text.startswith("XCP") or "A2L" in text:
            self.ctx.log(text)
