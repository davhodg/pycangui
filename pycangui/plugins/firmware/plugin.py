# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Firmware download over CANopen.

A plugin rather than part of the tool, deliberately. What a device wants in
order to take new firmware is not part of any protocol pycangui speaks: CiA
302-3 describes one way of doing it, most makers do something else, and the
something else is the interesting half. Installing it puts a copy in the
workspace, which is the copy that runs: edit ``program.py`` to be what the
device actually wants and everything around it -- the pane, the progress, the
reporting -- goes on working.

It is also the first thing built through the plugin API, which was the point of
building it that way: an API with no real screen behind it is a guess.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from pycangui.uds import images
from pycangui.ui import folders
from pycangui.ui.persist import remember

# Relative, so that the copy of program.py sitting beside *this* file is
# the one that runs. Named absolutely, an installed plugin would reach
# back into the one pycangui ships and editing your own would do nothing.
from . import program
from .program import Device

API_VERSION = 1
NAME = "CANopen firmware (CiA 302-3)"
VERSION = "1.1"
DESCRIPTION = "Download firmware to a CANopen node (CiA 302-3)."

WARNING = (
    "While it is being programmed the device answers very little and slowly.\n"
    "Timeouts here are the expected thing rather than a fault, and pulling the\n"
    "power part way through is how a controller is turned into a brick."
)

#: Asked before anything that stops the program: the bootloader and a download.
STOP_TITLE = "Stop the program?"
STOP_TEXT = (
    "Node {node} is about to stop its application, which is how it goes into "
    "its bootloader.\n\n"
    "Whatever that application is controlling stops being controlled, and "
    "while it is in its loader the device answers very little and slowly.\n\n"
    "Stop the program on node {node}?"
)

#: How long each SDO answer may take while a download runs. Clearing a
#: program erases flash, and a device doing that answers when it has finished
#: rather than when asked; so does one writing the last block of an image.
PROGRAMMING_TIMEOUT_S = 10
PATIENCE_TIP = (
    "How long the device may take to answer each request while a download\n"
    "runs. Clearing a program erases its flash and the device answers only\n"
    "when that has finished, which can be many seconds. Everything else\n"
    "uses the SDO timeout set in the CANopen pane."
)

#: How the image goes over SDO. Segmented first: every device takes it.
SEGMENTED = "Segmented"
BLOCK = "Block"
TRANSFERS = (SEGMENTED, BLOCK)
TRANSFER_TIP = (
    "Segmented: 7 bytes a frame, each one answered. Every device takes it.\n"
    "Block: many frames to each answer, and much faster on a device that\n"
    "supports it. One that does not refuses at the start, before anything\n"
    "is written."
)


class NodeDevice(Device):
    """A live node, wearing the methods the sequence needs.

    Everything here runs on a worker thread: an SDO download of a firmware
    image takes minutes, and doing it on the GUI thread would freeze the window
    for all of them.
    """

    def __init__(self, node, block: bool = False) -> None:
        self.node = node
        self.block = block

    @contextmanager
    def patient(self, seconds: float):
        """A longer SDO timeout for as long as this lasts, then the old one back.

        Longer only: a timeout somebody has already set higher than this in the
        CANopen pane is left as it is.
        """
        sdo = self.node.sdo
        before = sdo.RESPONSE_TIMEOUT
        sdo.RESPONSE_TIMEOUT = max(before, seconds)
        try:
            yield
        finally:
            sdo.RESPONSE_TIMEOUT = before

    def write(self, index: int, sub: int, value) -> None:
        self.node.sdo[index][sub].raw = value

    def read(self, index: int, sub: int):
        """Through the EDS where it describes the object, as bytes where not.

        A device in its loader is the usual case of a node nobody loaded an
        EDS for, and what it is running is exactly what is wanted then.
        """
        try:
            entry = self.node.sdo[index]
        except KeyError:
            return self.node.sdo.upload(index, sub)
        if sub == 0 and hasattr(entry, "raw"):
            return entry.raw  # a plain variable, which has no sub-indices
        return entry[sub].raw

    def write_domain(self, index: int, sub: int, data: bytes, progress) -> None:
        # Opened as a file and written in blocks rather than assigned in one
        # go, which is the only way to have anything to report while it runs.
        try:
            sink = self.node.sdo.open(index, sub, "wb", size=len(data), block_transfer=self.block)
        except Exception as exc:
            if self.block:
                raise RuntimeError(
                    f"{exc} -- the device may not support block transfer; try Segmented"
                ) from exc
            raise
        with sink:
            for at in range(0, len(data), program.BLOCK):
                sink.write(data[at : at + program.BLOCK])
                progress(min(at + program.BLOCK, len(data)), len(data))


class FirmwareView(QWidget):
    def __init__(self, app) -> None:
        super().__init__()
        self.app = app
        self.image = None
        self._busy = False

        self.node = QComboBox()
        self.node.setToolTip("Which node to program.")
        self.program = QSpinBox()
        self.program.setRange(1, 127)
        self.program.setToolTip(
            "Which program on the device. CiA 302-3 numbers them from 1, and\n"
            "a controller with two processors takes two images."
        )

        self.path = QLineEdit()
        self.path.setPlaceholderText("Intel HEX, S-record or binary...")
        self.path.editingFinished.connect(self._reload_image)
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._choose)
        self.summary = QLabel("")
        self.summary.setWordWrap(True)

        picked = QHBoxLayout()
        picked.addWidget(self.path, 1)
        picked.addWidget(browse)

        self.transfer = QComboBox()
        self.transfer.addItems(TRANSFERS)
        self.transfer.setToolTip(TRANSFER_TIP)
        remember(app.ctx, "plugins.firmware.transfer", self.transfer)
        self.patience = QSpinBox()
        self.patience.setRange(1, 300)
        self.patience.setValue(PROGRAMMING_TIMEOUT_S)
        self.patience.setSuffix(" s")
        self.patience.setToolTip(PATIENCE_TIP)
        remember(app.ctx, "plugins.firmware.programming_timeout_s", self.patience)

        where = QGridLayout()
        where.addWidget(QLabel("Node:"), 0, 0)
        where.addWidget(self.node, 0, 1)
        where.addWidget(QLabel("Program:"), 0, 2)
        where.addWidget(self.program, 0, 3)
        where.addWidget(QLabel("Transfer:"), 0, 4)
        where.addWidget(self.transfer, 0, 5)
        where.addWidget(QLabel("Programming timeout:"), 1, 0, 1, 3)
        where.addWidget(self.patience, 1, 3)
        where.setColumnStretch(6, 1)

        self.download = QPushButton("Download")
        self.download.setToolTip(
            "Stop the program, clear it, write the image and start it again.\n\n" + WARNING
        )
        self.download.clicked.connect(self._download)
        self.enter = QPushButton("Enter bootloader")
        self.enter.setToolTip(
            "Stop the program (0x1F51), which is how a CiA 302-3 device goes into its\n"
            "loader. A device with a way of its own is enter_bootloader in program.py."
        )
        self.enter.clicked.connect(
            lambda: self._one("Requesting the bootloader", program.enter_bootloader, ask=True)
        )
        self.leave = QPushButton("Exit bootloader")
        self.leave.setToolTip(
            "Start the program again (0x1F51), which is how the loader is left.\n"
            "A device with a way of its own is exit_bootloader in program.py."
        )
        self.leave.clicked.connect(
            lambda: self._one("Leaving the bootloader", program.exit_bootloader)
        )
        self.version = QPushButton("Read version")
        self.version.setToolTip(
            "Read the manufacturer software version (0x100A), and the software\n"
            "identification (0x1F56) and flash status (0x1F57) where the device keeps them."
        )
        self.version.clicked.connect(self._identify)

        buttons = QHBoxLayout()
        for button in (self.download, self.enter, self.leave, self.version):
            buttons.addWidget(button)
        buttons.addStretch()

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.state = QLabel("")
        self.state.setWordWrap(True)

        box = QGroupBox("Program download (CiA 302-3)")
        inside = QVBoxLayout(box)
        inside.addLayout(where)
        inside.addLayout(picked)
        inside.addWidget(self.summary)
        inside.addLayout(buttons)
        inside.addWidget(self.progress)
        inside.addWidget(self.state)

        note = QLabel(
            "Most devices use a sequence of their maker's own instead. This "
            "plugin is installed in your workspace: edit program.py in it to be "
            "what yours wants, then Plugins > Reload plugins."
        )
        note.setWordWrap(True)
        note.setEnabled(False)

        layout = QVBoxLayout(self)
        layout.addWidget(box)
        layout.addWidget(note)
        layout.addStretch()

        self._fill_nodes()
        # A bound method, not a lambda: the manager outlives this pane, and
        # Qt drops a bound method of a widget when the widget is destroyed
        # while it keeps a lambda alive to reach for widgets that have gone.
        app.canopen.node_seen.connect(self._on_node_seen)

    # --- what to program, and what with ------------------------------------------------
    def _on_node_seen(self, _node_id: int, _state: str) -> None:
        self._fill_nodes()

    def _fill_nodes(self) -> None:
        chosen = self.node.currentData()
        self.node.clear()
        for node_id in self.app.canopen.nodes():
            self.node.addItem(f"Node {node_id}", node_id)
        at = self.node.findData(chosen)
        self.node.setCurrentIndex(max(at, 0))

    def _choose(self) -> None:
        path = folders.open_file(
            self,
            self.app.ctx,
            folders.IMAGE,
            "Firmware image",
            images.READ_FILTER,
            self.app.ctx.user_dir,
        )
        if path:
            self.path.setText(path)
            self._reload_image()

    def _reload_image(self) -> None:
        path = self.path.text().strip()
        self.image = None
        if not path:
            self.summary.setText("")
            return
        try:
            self.image = images.read(path, address=0 if images.looks_binary(path) else None)
        except Exception as exc:  # bincopy raises its own family of these
            self.summary.setText(str(exc))
            self.app.warn(f"{Path(path).name}: {exc}")
            return
        self.summary.setText(self.image.summary())

    # --- doing it ------------------------------------------------------------------------
    def _device(self) -> NodeDevice | None:
        node_id = self.node.currentData()
        node = self.app.canopen.node(node_id) if node_id is not None else None
        if node is None:
            self.app.warn("No node selected, or it is not on the bus.")
            return None
        return NodeDevice(node, block=self.transfer.currentText() == BLOCK)

    def _agreed_to_stop(self) -> bool:
        """Asked once a session for each node, as enabling a drive is.

        Stopping the program is the same class of thing: whatever it was
        controlling stops being controlled. Keyed on the node, so agreeing for
        a bench unit is not agreeing for the machine beside it.
        """
        node_id = self.node.currentData()
        return self.app.confirm.ask(
            self, f"firmware.stop.{node_id}", STOP_TITLE, STOP_TEXT.format(node=node_id)
        )

    def _one(self, what: str, action, ask: bool = False) -> None:
        """One write: into the loader, which asks first, or out of it."""
        device = self._device()
        if device is None or self._busy:
            return
        if ask and not self._agreed_to_stop():
            return
        number = self.program.value()
        self._start(what)
        self.app.run_in_background(
            lambda: action(device, number), lambda _r, error: self._finished(what, error)
        )

    def _identify(self) -> None:
        device = self._device()
        if device is None or self._busy:
            return
        number = self.program.value()

        def job():
            return (
                program.software_version(device),
                program.identification(device, number),
                program.flash_status(device, number),
            )

        def done(result, error):
            self._busy = False
            if error:
                self.app.warn(f"Reading the version failed: {error}")
                return
            version, software, status = result
            missing = "not kept by this device"
            self.state.setText(
                f"Software version: {version or missing}\n"
                f"Software identification: {software or missing}    "
                f"Flash status: {status or missing}"
            )
            self.app.log(f"Node {self.node.currentData()} software version: {version or missing}")

        self._busy = True
        self.app.run_in_background(job, done)

    def _download(self) -> None:
        device = self._device()
        if device is None or self._busy:
            return
        if self.image is None:
            self.app.warn("No image chosen.")
            return
        try:
            data = program.image_bytes(self.image)
        except ValueError as exc:
            self.app.warn(str(exc))
            return
        if not self._agreed_to_stop():  # the first step stops the program
            return
        number = self.program.value()
        seconds = self.patience.value()

        def job():
            # The whole sequence, not only the erase: a device may be as slow to
            # answer the last block, or the start that checks the image.
            with device.patient(seconds):
                for step in program.steps(device, number, data, self._progress):
                    self.app.log(step.what)
                    step.run()
            return len(data)

        self._start("Programming")
        self.progress.setRange(0, len(data))
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self.app.run_in_background(job, lambda written, error: self._finished("Programming", error))

    def _progress(self, done: int, total: int) -> None:
        # Called from the worker thread. Qt marshals a queued setValue for us
        # because the bar lives on the GUI thread and this is a signal.
        self.progress.setValue(done)

    def _start(self, what: str) -> None:
        self._busy = True
        self.state.setText(f"{what}...")
        self.download.setEnabled(False)

    def _finished(self, what: str, error) -> None:
        self._busy = False
        self.download.setEnabled(True)
        self.progress.setVisible(False)
        if error:
            self.state.setText(f"{what} failed: {error}")
            self.app.warn(f"{what} failed: {error}")
            return
        self.state.setText(f"{what}: done.")
        self.app.log(f"{what}: done.")


def register(app) -> None:
    app.add_pane("main", "CANopen firmware", lambda _name: FirmwareView(app), area="right")
