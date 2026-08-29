"""XCP pane: connect to a slave, load an A2L, read/write characteristics and
measurements, and stream measurements into the Plot via the signal hub."""

from __future__ import annotations

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
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

from pycangui.core.backends import BACKENDS
from pycangui.core.context import Context
from pycangui.xcp import RESOURCE_CAL
from pycangui.xcp.manager import XcpManager

ROLE_NAME = Qt.UserRole


def _specs(kind: str):
    return BACKENDS.specs(kind)


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
            "slaves want before a value can be written.  The key comes from\n"
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
            "Read the selected measurements once.  Tick Poll to keep reading\n"
            "them into the signal hub, where they can be plotted."
        )
        read_btn.clicked.connect(self._read_selected)
        row = QHBoxLayout()

        row.addStretch()
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
        manager.value.connect(self._on_value)
        if manager.a2l is not None:
            self._populate()

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
        path, _ = QFileDialog.getOpenFileName(
            self, "Load A2L", str(self.ctx.user_dir), "A2L (*.a2l);;All files (*)"
        )
        if path:
            try:
                self.manager.load_a2l(path)
                self.ctx.settings.set("xcp.a2l", path)
            except Exception as exc:  # parser is best-effort
                self._append(f"A2L load failed: {exc}")

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
                if param.writable:
                    item.setFlags(item.flags() | Qt.ItemIsEditable)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(5, Qt.Unchecked)
                self.tree.addTopLevelItem(item)
                self._items[param.name] = item
        self._updating = False

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
