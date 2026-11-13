# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""The XCP pane's Transfer tab: flash a file, read memory out, sum it.

The UDS pane's Transfer tab does the same job for a bootloader that speaks
UDS; this one is for a slave flashed over XCP or CCP. The difference worth
knowing is where the addresses come from. A UDS download sends each segment
of the file where the file says. Here the A2L also has a say: it lists the
areas that may be programmed, and the order they are erased and written in,
and only the file's bytes inside them are sent.

The areas are a table rather than a fact, because the A2L is not always
right and not always there. Untick one to leave it alone, edit its address,
size or order, add one the A2L does not list, or take the file's own
segments as the areas -- each is how a test bench gets flashed with
something the A2L was not written for. *From A2L* puts the file's back.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pycangui.core.context import Context
from pycangui.uds import images
from pycangui.ui import folders, fonts, icons
from pycangui.ui.confirm import Confirmations
from pycangui.ui.persist import remember
from pycangui.xcp import checksum
from pycangui.xcp import transfer as plans
from pycangui.xcp.a2l import Area
from pycangui.xcp.manager import (
    DOWNLOAD,
    PROGRAM,
    VERIFY_CHECKSUM,
    VERIFY_NONE,
    VERIFY_READ_BACK,
    FlashOptions,
    XcpManager,
)

#: What the tab can do, as (data, text). Program and Download both write the
#: file; they differ in the commands, and so in what memory they suit.
OPERATIONS = (
    (PROGRAM, "Program (flash)"),
    (DOWNLOAD, "Download (RAM)"),
    ("upload", "Upload to file"),
    ("checksum", "Checksum"),
)
OPERATION_TIP = (
    "Program erases each area the file has bytes for and writes them with\n"
    "PROGRAM, as flash needs. Download writes with DOWNLOAD and erases\n"
    "nothing, for memory that is written like RAM. Upload reads the ticked\n"
    "areas into a file. Checksum has the slave sum the ticked areas -- or,\n"
    "with a file chosen, the file's bytes in them -- and compares."
)

COL_NAME, COL_ADDRESS, COL_SIZE, COL_ERASE, COL_WRITE, COL_IN_FILE, COL_FROM = range(7)
HEADERS = ("Area", "Address", "Size", "Erase", "Write", "In file", "From")
AREAS_TIP = (
    "What is erased and written, and in which order: from the A2L's\n"
    "programming sectors, or its flash memory segments where it has no\n"
    "sectors. Untick an area to leave it alone. Address, size and both\n"
    "orders can be edited -- in hex, and the orders as numbers, lowest first."
)
FROM_A2L_TIP = "Put back the areas the A2L gives, and forget any edits."
FROM_FILE_TIP = (
    "One area for each segment of the chosen file, so the file is flashed\n"
    "as it is, whatever the A2L says."
)
ADD_TIP = "An area the A2L does not list, to be typed in."
OUTSIDE_TIP = (
    "Write the file's bytes that are in no area too, each run of them as an\n"
    "area of its own, after the rest. Without this they are left out, and\n"
    "the log says which."
)
ERASE_TIP = (
    "Erase each area the file has bytes for, whole, before any is written:\n"
    "PROGRAM_CLEAR over XCP, CLEAR_MEMORY over CCP. Flash can only be\n"
    "written once it has been erased. An area the file has nothing for is\n"
    "never erased."
)
UNLOCK_TIP = (
    "Seed and key first: the PGM resource to program, CAL to download. The\n"
    "key comes from hooks/xcp.py::compute_key, or the seed and key DLL."
)
VERIFY_TIP = (
    "How what was written is checked. Checksum has the slave sum each run\n"
    "it was sent and compares that with the file. Read back reads it all\n"
    "out again, slower but certain, and says where it first differs."
)
CHECKSUM_TIP = (
    "Which algorithm the slave's checksum is compared with. Left as it is,\n"
    "that is the one an XCP slave says it used, or else the one the A2L\n"
    "names: XCP's CHECKSUM block, or CHECKSUM_PARAM in CCP's TP_BLOB, whose\n"
    "procedure numbers are XCP's. Choose one to override both."
)
RESET_TIP = "PROGRAM_RESET once it is done, so the slave starts what it was given. XCP only."
CANCEL_TIP = (
    "Stop after the piece being sent now. What was erased and not yet\nwritten stays erased."
)
NOT_CONNECTED_TIP = "Connect to the slave first, with the bar above."
#: The checksum choices: the slave's own first, then each XCP type.
SLAVE_SAYS = "As the slave or A2L says"


def _hex(value: int) -> str:
    return f"{value:X}"


class TransferTab(QWidget):
    def __init__(self, manager: XcpManager, ctx: Context, confirm: Confirmations, say) -> None:
        super().__init__()
        self.manager = manager
        self.ctx = ctx
        self.confirm = confirm
        self._say = say
        self._image: images.Image | None = None
        self._filling = False
        mono = fonts.mono()

        self.operation = QComboBox()
        self.operation.setToolTip(OPERATION_TIP)
        for data, text in OPERATIONS:
            self.operation.addItem(text, data)

        self.local = QLineEdit()
        self.local.setPlaceholderText("Intel HEX, S-record or raw binary")
        self.local.editingFinished.connect(self._reload_image)
        browse = icons.beside(QPushButton("Browse..."), icons.BROWSE)
        browse.setToolTip("Choose the file on this computer")
        browse.clicked.connect(self._browse)
        self.address = QLineEdit()
        self.address.setFont(mono)
        self.address.setFixedWidth(fonts.width_for(self.address, "FFFFFFFF"))
        self.address.setToolTip(
            "Where a raw binary starts, in hex. A hex or S-record file carries\n"
            "its own addresses, and fills this in."
        )
        self.address.editingFinished.connect(self._reload_image)

        # --- the areas ----------------------------------------------------------------
        self.table = QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.setToolTip(AREAS_TIP)
        self.table.setFont(mono)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemChanged.connect(self._area_edited)
        self.from_a2l = icons.beside(QPushButton("From A2L"), icons.RELOAD)
        self.from_a2l.setToolTip(FROM_A2L_TIP)
        self.from_a2l.clicked.connect(self._areas_from_a2l)
        self.from_file = icons.beside(QPushButton("From file"), icons.OPEN)
        self.from_file.setToolTip(FROM_FILE_TIP)
        self.from_file.clicked.connect(self._areas_from_file)
        add = icons.beside(QPushButton("Add"), icons.ADD)
        add.setToolTip(ADD_TIP)
        add.clicked.connect(self._add_area)
        self.remove = icons.beside(QPushButton("Remove"), icons.REMOVE)
        self.remove.setToolTip("Take the selected areas out of the table.")
        self.remove.clicked.connect(self._remove_areas)
        self.outside = QCheckBox("Write bytes in no area too")
        self.outside.setToolTip(OUTSIDE_TIP)
        self.outside.toggled.connect(lambda _on: self._refresh())
        #: What it will do, in one line, worked out again whenever anything changes.
        self.summary = QLabel()
        self.summary.setWordWrap(True)

        # --- how ----------------------------------------------------------------------
        self.erase = QCheckBox("Erase first")
        self.erase.setChecked(True)
        self.erase.setToolTip(ERASE_TIP)
        self.unlock = QCheckBox("Unlock")
        self.unlock.setChecked(True)
        self.unlock.setToolTip(UNLOCK_TIP)
        self.verify = QComboBox()
        self.verify.setToolTip(VERIFY_TIP)
        for data, text in (
            (VERIFY_CHECKSUM, "Checksum"),
            (VERIFY_READ_BACK, "Read back"),
            (VERIFY_NONE, "None"),
        ):
            self.verify.addItem(text, data)
        self.checksum = QComboBox()
        self.checksum.setToolTip(CHECKSUM_TIP)
        self.checksum.addItem(SLAVE_SAYS, None)
        for kind, name in checksum.NAMES.items():
            if kind != checksum.USER_DEFINED:
                self.checksum.addItem(name, kind)
        self.reset = QCheckBox("Reset after")
        self.reset.setToolTip(RESET_TIP)

        self.start = icons.beside(QPushButton("Program"), icons.DOWNLOAD)
        self.start.clicked.connect(self._start)
        self.bar = QProgressBar()
        self.bar.setTextVisible(True)
        self.stop = icons.beside(QPushButton("Cancel"), icons.CANCEL)
        self.stop.setToolTip(CANCEL_TIP)
        self.stop.setEnabled(False)
        self.stop.clicked.connect(manager.cancel_transfer)

        # --- laid out -------------------------------------------------------------------
        def row(*items, stretch: int | None = None) -> QHBoxLayout:
            line = QHBoxLayout()
            for item in items:
                line.addWidget(QLabel(item) if isinstance(item, str) else item)
            if stretch is None:
                line.addStretch()
            else:
                line.setStretch(stretch, 1)
            return line

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(row("Operation", self.operation))
        layout.addLayout(row("File", self.local, browse, "Address", self.address, stretch=1))
        layout.addLayout(
            row(QLabel("Areas"), self.from_a2l, self.from_file, add, self.remove, self.outside)
        )
        layout.addWidget(self.table, 1)
        layout.addWidget(self.summary)
        layout.addLayout(
            row(
                self.erase,
                self.unlock,
                "Verify",
                self.verify,
                "Checksum",
                self.checksum,
                self.reset,
            )
        )
        layout.addLayout(row(self.start, self.bar, self.stop, stretch=1))

        for key, widget in (
            ("xcp.transfer.operation", self.operation),
            ("xcp.transfer.erase", self.erase),
            ("xcp.transfer.unlock", self.unlock),
            ("xcp.transfer.verify", self.verify),
            ("xcp.transfer.checksum", self.checksum),
            ("xcp.transfer.reset", self.reset),
        ):
            remember(ctx, key, widget)
        self.operation.currentIndexChanged.connect(lambda _i: self._on_operation())

        manager.progress.connect(self._on_progress)
        manager.transferring.connect(self._on_transferring)
        manager.connected.connect(lambda _on: self._on_operation())
        manager.a2l_loaded.connect(lambda _n: self._a2l_changed())

        saved = ctx.settings.get(EDITED_KEY)
        if saved:
            self._fill(*_areas_from_settings(saved))
        else:
            self._areas_from_a2l(said=False)
        self._on_operation()

    # --- what the controls are for ----------------------------------------------------------
    def _op(self) -> str:
        return self.operation.currentData()

    @Slot()
    def _on_operation(self) -> None:
        """Grey out what this operation has no use for, as the UDS tab does."""
        op = self._op()
        writes = op in (PROGRAM, DOWNLOAD)
        self.address.setEnabled(op != "upload" and images.looks_binary(self.local.text()))
        self.outside.setEnabled(writes)
        self.erase.setEnabled(op == PROGRAM)
        self.unlock.setEnabled(writes)
        self.verify.setEnabled(writes)
        self.checksum.setEnabled(op != "upload")
        self.reset.setEnabled(
            op == PROGRAM and bool(getattr(self.manager.engine, "can_reset", False))
        )
        self.start.setText(dict(OPERATIONS)[op].split(" (")[0])
        pictures = {"upload": icons.UPLOAD, "checksum": icons.CALCULATE}
        icons.beside(self.start, pictures.get(op, icons.DOWNLOAD))
        self._show_busy()
        self._refresh()

    def _show_busy(self) -> None:
        busy = self.manager.is_transferring
        ready = self.manager.is_connected and not busy
        self.start.setEnabled(ready)
        self.start.setToolTip("" if self.manager.is_connected else NOT_CONNECTED_TIP)
        self.stop.setEnabled(busy)
        self.operation.setEnabled(not busy)

    @Slot(bool)
    def _on_transferring(self, running: bool) -> None:
        self._show_busy()
        if not running:
            self.bar.reset()
            self.bar.setFormat("")

    @Slot(str, int, int)
    def _on_progress(self, label: str, done: int, total: int) -> None:
        self.bar.setMaximum(total or 0)
        self.bar.setValue(done)
        self.bar.setFormat(f"{label} %p%")

    # --- the file ------------------------------------------------------------------------------
    def _browse(self) -> None:
        if self._op() == "upload":
            path = folders.save_file(
                self, self.ctx, folders.IMAGE, "Save to", images.WRITE_FILTER, self.ctx.user_dir
            )
        else:
            path = folders.open_file(
                self, self.ctx, folders.IMAGE, "File to send", images.READ_FILTER, self.ctx.user_dir
            )
        if path:
            self.local.setText(path)
            self._reload_image()

    def _reload_image(self) -> None:
        """Read the chosen file now, so what it holds is in the table before anything is sent."""
        self._image = None
        path = self.local.text().strip()
        if self._op() != "upload" and path:
            typed = self.address.text().strip()
            try:
                start = int(typed, 16) if typed else None
            except ValueError:
                start = None
            try:
                self._image = images.read(path, start)
            except images.ImageError as exc:
                self._say(f"Transfer: {exc}")
            else:
                self.address.setText(_hex(self._image.address))
                self._say(f"Transfer: {self._image.summary()}")
        self._on_operation()

    # --- the areas ------------------------------------------------------------------------------
    def _a2l_changed(self) -> None:
        """A new A2L brings its own areas, unless the table has been made by hand."""
        if not self.ctx.settings.get(EDITED_KEY):
            self._areas_from_a2l(said=False)

    def _areas_from_a2l(self, said: bool = True) -> None:
        a2l = self.manager.a2l
        areas = a2l.flash_areas() if a2l is not None else []
        self.ctx.settings.set(EDITED_KEY, None)
        self._fill(areas)
        if said:
            self._say(f"Transfer: {len(areas)} area(s) from the A2L")

    def _areas_from_file(self) -> None:
        if self._image is None:
            self._say("Transfer: choose a file first, and its segments become the areas")
            return
        self._fill(plans.from_segments(self._image.segments))
        self._edited()

    def _add_area(self) -> None:
        self._fill([*self.all_areas(), Area("new", 0, 0, *(self._next_orders()), source="typed")])
        self._edited()
        self.table.editItem(self.table.item(self.table.rowCount() - 1, COL_ADDRESS))

    def _next_orders(self) -> tuple[int, int]:
        areas = self.all_areas()
        return (
            max((a.clear_order for a in areas), default=-1) + 1,
            max((a.program_order for a in areas), default=-1) + 1,
        )

    def _remove_areas(self) -> None:
        rows = {index.row() for index in self.table.selectionModel().selectedRows()}
        if not rows:
            return
        kept = [a for n, a in enumerate(self.all_areas()) if n not in rows]
        ticked = [t for n, t in enumerate(self._ticks()) if n not in rows]
        self._fill(kept, ticked)
        self._edited()

    def _ticks(self) -> list[bool]:
        return [
            self.table.item(r, COL_NAME).checkState() == Qt.Checked
            for r in range(self.table.rowCount())
        ]

    def _fill(self, areas: list[Area], ticked: list[bool] | None = None) -> None:
        self._filling = True
        self.table.setRowCount(0)
        for n, area in enumerate(areas):
            r = self.table.rowCount()
            self.table.insertRow(r)
            name = QTableWidgetItem(area.name)
            name.setFlags(name.flags() | Qt.ItemIsUserCheckable)
            on = ticked[n] if ticked is not None and n < len(ticked) else True
            name.setCheckState(Qt.Checked if on else Qt.Unchecked)
            name.setData(Qt.UserRole, area)
            self.table.setItem(r, COL_NAME, name)
            for column, text in (
                (COL_ADDRESS, _hex(area.address)),
                (COL_SIZE, _hex(area.size)),
                (COL_ERASE, str(area.clear_order)),
                (COL_WRITE, str(area.program_order)),
            ):
                self.table.setItem(r, column, QTableWidgetItem(text))
            for column, text in ((COL_IN_FILE, ""), (COL_FROM, area.source)):
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(r, column, item)
        self._filling = False
        self._refresh()

    def _area_edited(self, item: QTableWidgetItem) -> None:
        if self._filling or item.column() in (COL_IN_FILE, COL_FROM):
            return
        self._edited()
        self._refresh()

    def _edited(self) -> None:
        """The table is now somebody's own, kept until From A2L puts the file's back."""
        self.ctx.settings.set(
            EDITED_KEY,
            [
                {**_area_to_settings(a), "on": on}
                for a, on in zip(self.all_areas(), self._ticks(), strict=True)
            ],
        )

    def all_areas(self) -> list[Area]:
        """Every row as an area, ticked or not; a row that does not read is passed over."""
        found = []
        for r in range(self.table.rowCount()):
            area = self._row(r)
            if area is not None:
                found.append(area)
        return found

    def areas(self) -> list[Area]:
        """The ticked rows: what will be used."""
        return [
            a
            for r, a in ((r, self._row(r)) for r in range(self.table.rowCount()))
            if a is not None and self.table.item(r, COL_NAME).checkState() == Qt.Checked
        ]

    def _row(self, r: int) -> Area | None:
        try:
            old = self.table.item(r, COL_NAME).data(Qt.UserRole)
            return Area(
                self.table.item(r, COL_NAME).text().strip() or f"area {r + 1}",
                int(self.table.item(r, COL_ADDRESS).text(), 16),
                int(self.table.item(r, COL_SIZE).text(), 16),
                int(self.table.item(r, COL_ERASE).text()),
                int(self.table.item(r, COL_WRITE).text()),
                memory=old.memory if old else "",
                source=self.table.item(r, COL_FROM).text(),
                extension=old.extension if old else 0,
            )
        except (AttributeError, ValueError):
            return None

    def _refresh(self) -> None:
        """How much of the file each area takes, and what the button will do."""
        self._filling = True
        plan = None
        if self._image is not None and self._op() != "upload":
            plan = plans.make(self._image.segments, self.areas(), self.outside.isChecked())
        taken: dict[str, int] = {}
        for run in plan.runs if plan else ():
            taken[run.area] = taken.get(run.area, 0) + len(run.data)
        for r in range(self.table.rowCount()):
            area = self._row(r)
            text = "" if plan is None or area is None else _hex(taken.get(area.name, 0))
            self.table.item(r, COL_IN_FILE).setText(text)
        self._filling = False
        self.remove.setEnabled(self.table.rowCount() > 0)
        self.summary.setText(self._describe(plan))

    def _describe(self, plan) -> str:
        op, areas = self._op(), self.areas()
        if op == "upload":
            total = sum(a.size for a in areas)
            return f"Reads {len(areas)} area(s), {total} bytes, into the file."
        if plan is None:
            if op == "checksum":
                return f"Sums {len(areas)} area(s), whole: choose a file to compare with it."
            return "Choose a file to send."
        if not plan.runs:
            return "Nothing in the file is inside the areas ticked."
        line = f"{plan.size} bytes in {len(plan.clears)} area(s)"
        if op == PROGRAM and self.erase.isChecked():
            line = f"Erases {len(plan.clears)} area(s), then writes {plan.size} bytes"
        elif op == "checksum":
            line = f"Sums the file's {plan.size} bytes in {len(plan.clears)} area(s)"
        else:
            line = f"Writes {line}"
        if plan.outside:
            left = sum(size for _address, size in plan.outside)
            line += f"; {left} bytes of the file are in no area, and left out"
        return line + "."

    # --- go ------------------------------------------------------------------------------------
    def _start(self) -> None:
        op, areas = self._op(), self.areas()
        if op == "upload":
            path = self.local.text().strip()
            if not path:
                self._say("Upload: nowhere to put it -- choose a file first")
                return
            self.manager.upload(path, areas)
            return
        if self._image is None:
            self._reload_image()
        if op == "checksum":
            self.manager.build_checksums(areas, self._image, self.checksum.currentData())
            return
        if self._image is None:
            self._say("Transfer: no file to send")
            return
        options = self.options()
        plan = self.manager.plan(self._image, areas, options.outside_too)
        if not plan.runs:
            self._say("Transfer: nothing in the file is inside the areas ticked")
            return
        if not self._agree(op, plan):
            return
        self.manager.flash(self._image, areas, options)

    def options(self) -> FlashOptions:
        op = self._op()
        return FlashOptions(
            write_with=op,
            clear=op == PROGRAM and self.erase.isChecked(),
            unlock=self.unlock.isChecked(),
            verify=self.verify.currentData(),
            checksum_kind=self.checksum.currentData(),
            reset=self.reset.isEnabled() and self.reset.isChecked(),
            outside_too=self.outside.isChecked(),
        )

    def _agree(self, op: str, plan) -> bool:
        """Ask once a session before changing what is in the slave's memory."""
        lines = [self._image.summary(), ""]
        if op == PROGRAM and self.erase.isChecked():
            lines.append("Erased, in this order: " + ", ".join(a.name for a in plan.clears) + ".")
        names = list(dict.fromkeys(r.area for r in plan.runs))
        lines.append("Written, in this order: " + ", ".join(names) + ".")
        if plan.outside:
            lines.append(f"{len(plan.outside)} stretch(es) of the file are in no area, left out.")
        lines += ["", "An interrupted or wrong image can leave the slave unable to start."]
        return self.confirm.ask(self, f"xcp.transfer.{op}", "Write to the slave?", "\n".join(lines))


#: Where a table made by hand is kept. Absent while the table follows the A2L.
EDITED_KEY = "xcp.transfer.areas"


def _area_to_settings(area: Area) -> dict:
    return {
        "name": area.name,
        "address": _hex(area.address),
        "size": _hex(area.size),
        "erase": area.clear_order,
        "write": area.program_order,
        "from": area.source,
        "extension": area.extension,
    }


def _areas_from_settings(saved) -> tuple[list[Area], list[bool]]:
    """The table as it was left: the areas, and which were ticked."""
    areas, ticked = [], []
    for entry in saved if isinstance(saved, list) else ():
        try:
            areas.append(
                Area(
                    str(entry["name"]),
                    int(str(entry["address"]), 16),
                    int(str(entry["size"]), 16),
                    int(entry.get("erase", 0)),
                    int(entry.get("write", 0)),
                    source=str(entry.get("from", "typed")),
                    extension=int(entry.get("extension", 0)),
                )
            )
        except (KeyError, TypeError, ValueError, AttributeError):
            continue  # a hand-edited settings.json
        ticked.append(bool(entry.get("on", True)))
    return areas, ticked
