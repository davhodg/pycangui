# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Firmware download over CANopen.

A plugin rather than part of the tool, deliberately.  What a device wants in
order to take new firmware is not part of any protocol pycangui speaks: CiA
302-3 describes one way of doing it, most makers do something else, and the
something else is the interesting half.  Installing it puts a copy in the
workspace, which is the copy that runs: edit ``program.py`` to be what the
device actually wants and everything around it -- the pane, the progress, the
reporting -- goes on working.

It is also the first thing built through the plugin API, which was the point of
building it that way: an API with no real screen behind it is a guess.
"""

from __future__ import annotations

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

# Relative, so that the copy of program.py sitting beside *this* file is
# the one that runs.  Named absolutely, an installed plugin would reach
# back into the one pycangui ships and editing your own would do nothing.
from . import program
from .program import Device

API_VERSION = 1
NAME = "CANopen firmware (CiA 302-3)"
VERSION = "1.0"
DESCRIPTION = "Download firmware to a CANopen node (CiA 302-3)."

WARNING = (
    "While it is being programmed the device answers very little and slowly.\n"
    "Timeouts here are the expected thing rather than a fault, and pulling the\n"
    "power part way through is how a controller is turned into a brick."
)


class NodeDevice(Device):
    """A live node, wearing the two methods the sequence needs.

    Everything here runs on a worker thread: an SDO download of a firmware
    image takes minutes, and doing it on the GUI thread would freeze the window
    for all of them.
    """

    def __init__(self, node) -> None:
        self.node = node

    def write(self, index: int, sub: int, value) -> None:
        self.node.sdo[index][sub].raw = value

    def read(self, index: int, sub: int):
        return self.node.sdo[index][sub].raw

    def write_domain(self, index: int, sub: int, data: bytes, progress) -> None:
        # Opened as a file and written in blocks rather than assigned in one
        # go, which is the only way to have anything to report while it runs.
        with self.node.sdo.open(index, sub, "wb", size=len(data)) as sink:
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
            "Which program on the device.  CiA 302-3 numbers them from 1, and\n"
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

        where = QGridLayout()
        where.addWidget(QLabel("Node:"), 0, 0)
        where.addWidget(self.node, 0, 1)
        where.addWidget(QLabel("Program:"), 0, 2)
        where.addWidget(self.program, 0, 3)
        where.setColumnStretch(4, 1)

        self.download = QPushButton("Download")
        self.download.setToolTip(
            "Stop the program, clear it, write the image and start it again.\n\n" + WARNING
        )
        self.download.clicked.connect(self._download)
        stop = QPushButton("Stop program")
        stop.setToolTip("Stop the application, which is what puts most devices in their loader.")
        stop.clicked.connect(lambda: self._one("Stopping the program", program.enter_bootloader))
        start = QPushButton("Start program")
        start.setToolTip("Start the application again.")
        start.clicked.connect(lambda: self._one("Starting the program", program.start_application))
        ask = QPushButton("What is on it?")
        ask.setToolTip(
            "Read the software identification and the flash status, where the device keeps them."
        )
        ask.clicked.connect(self._identify)

        buttons = QHBoxLayout()
        for button in (self.download, stop, start, ask):
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
            "Most devices use a sequence of their maker's own instead.  This "
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
        app.canopen.node_seen.connect(lambda *_a: self._fill_nodes())

    # --- what to program, and what with ------------------------------------------------
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
        return NodeDevice(node)

    def _one(self, what: str, action) -> None:
        """Stop or start, which is one write and worth no ceremony."""
        device = self._device()
        if device is None or self._busy:
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
            return program.identification(device, number), program.flash_status(device, number)

        def done(result, error):
            self._busy = False
            if error:
                self.app.warn(f"Reading the identification failed: {error}")
                return
            software, status = result
            self.state.setText(
                f"Software identification: {software or 'not kept by this device'}    "
                f"Flash status: {status or 'not kept by this device'}"
            )

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
        number = self.program.value()

        def job():
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
        # Called from the worker thread.  Qt marshals a queued setValue for us
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
