# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""The CANopen pane's Firmware tab: a program downloaded to the node selected.

CiA 302-3 unless the firmware hooks in ``hooks/canopen.py`` say otherwise; see
``pycangui.canopen.firmware``. This is the screen around the sequence: the
image, the program number, the transfer, the progress and the reporting.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from pycangui.canopen.firmware import Firmware, NodeDevice
from pycangui.uds import images
from pycangui.ui import folders, icons
from pycangui.ui.confirm import Confirmations
from pycangui.ui.persist import remember

#: What the pause is held for while a program is downloaded.
FLASHING = "CANopen firmware download"

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
    "uses the SDO timeout set in the CANopen pane's Settings."
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

HOOKS_NOTE = (
    "CiA 302-3 unless your hooks say otherwise. Most devices use a sequence of "
    "their maker's own: write it as the firmware hooks in hooks/canopen.py "
    "(Tools > Open hooks folder), chosen by the node's identity, then Tools > "
    "Reload hooks."
)

#: Settings, and where the plugin this used to be kept them.
TRANSFER_KEY = "canopen.firmware.transfer"
PATIENCE_KEY = "canopen.firmware.programming_timeout_s"
WAS = {
    TRANSFER_KEY: "plugins.firmware.transfer",
    PATIENCE_KEY: "plugins.firmware.programming_timeout_s",
}


class FirmwareTab(QWidget):
    """Download, the bootloader and the version, for the node selected above."""

    #: Bytes written so far, and of how many: from the worker thread, so a
    #: signal, which Qt delivers on the GUI thread.
    progressed = Signal(int, int)

    def __init__(self, manager, hooks, ctx, selected_node, confirm=None) -> None:
        super().__init__()
        self.manager = manager
        self.hooks = hooks
        self.ctx = ctx
        self.selected_node = selected_node
        self.confirm = confirm or Confirmations()
        self.image = None
        self._busy = False
        for key, old in WAS.items():  # what the plugin remembered, kept
            if ctx.settings.get(key) is None and ctx.settings.get(old) is not None:
                ctx.settings.set(key, ctx.settings.get(old))

        self.node = QLabel("")
        self.node.setToolTip("The node selected in the list above.")
        self.program = QSpinBox()
        self.program.setRange(1, 127)
        self.program.setToolTip(
            "Which program on the device. CiA 302-3 numbers them from 1, and\n"
            "a controller with two processors takes two images."
        )

        self.path = QLineEdit()
        self.path.setPlaceholderText("Intel HEX, S-record or binary...")
        self.path.editingFinished.connect(self._reload_image)
        browse = icons.beside(QPushButton("Browse..."), icons.BROWSE)
        browse.clicked.connect(self._choose)
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        picked = QHBoxLayout()
        picked.addWidget(QLabel("Image:"))
        picked.addWidget(self.path, 1)
        picked.addWidget(browse)

        self.transfer = QComboBox()
        self.transfer.addItems(TRANSFERS)
        self.transfer.setToolTip(TRANSFER_TIP)
        remember(ctx, TRANSFER_KEY, self.transfer)
        self.patience = QSpinBox()
        self.patience.setRange(1, 300)
        self.patience.setValue(PROGRAMMING_TIMEOUT_S)
        self.patience.setSuffix(" s")
        self.patience.setToolTip(PATIENCE_TIP)
        remember(ctx, PATIENCE_KEY, self.patience)

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

        self.download = icons.beside(QPushButton("Download"), icons.DOWNLOAD)
        self.download.setToolTip(
            "Write the image to the node: by default stop the program, clear it,\n"
            "write the image and start it again.\n\n" + WARNING
        )
        self.download.clicked.connect(self._download)
        self.enter = icons.beside(QPushButton("Enter bootloader"), icons.ENTER)
        self.enter.setToolTip(
            "Put the node in its loader: by default stop the program (0x1F51).\n"
            "A device with a way of its own is the enter_bootloader hook."
        )
        self.enter.clicked.connect(self.enter_bootloader)
        self.leave = icons.beside(QPushButton("Exit bootloader"), icons.LEAVE)
        self.leave.setToolTip(
            "Leave the loader: by default start the program again (0x1F51).\n"
            "A device with a way of its own is the exit_bootloader hook."
        )
        self.leave.clicked.connect(self.exit_bootloader)
        self.version = icons.beside(QPushButton("Read version"), icons.READ)
        self.version.setToolTip(
            "Read the manufacturer software version (0x100A), and the software\n"
            "identification (0x1F56) and flash status (0x1F57) where the device\n"
            "keeps them -- or what the firmware_version and flash_status hooks say."
        )
        self.version.clicked.connect(self.read_version)

        buttons = QHBoxLayout()
        for button in (self.download, self.enter, self.leave, self.version):
            buttons.addWidget(button)
        buttons.addStretch()

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progressed.connect(lambda done, _total: self.progress.setValue(done))
        self.state = QLabel("")
        self.state.setWordWrap(True)

        note = QLabel(HOOKS_NOTE)
        note.setWordWrap(True)
        note.setEnabled(False)

        layout = QVBoxLayout(self)
        layout.addLayout(where)
        layout.addLayout(picked)
        layout.addWidget(self.summary)
        layout.addLayout(buttons)
        layout.addWidget(self.progress)
        layout.addWidget(self.state)
        layout.addWidget(note)
        layout.addStretch()
        self.node_changed()

    # --- what to program, and what with ------------------------------------------------
    def node_changed(self) -> None:
        """The selection above moved: this tab is about the node now selected."""
        node_id = self.selected_node()
        self.node.setText("none selected" if node_id is None else f"{node_id}")
        enabled = node_id is not None and not self._busy
        for button in (self.download, self.enter, self.leave, self.version):
            button.setEnabled(enabled)

    def _say(self, text: str) -> None:
        self.ctx.events.information(f"Firmware: {text}")

    def _warn(self, text: str) -> None:
        self.ctx.events.warning(f"Firmware: {text}")

    def _choose(self) -> None:
        path = folders.open_file(
            self, self.ctx, folders.IMAGE, "Firmware image", images.READ_FILTER, self.ctx.user_dir
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
            self._warn(f"{Path(path).name}: {exc}")
            return
        self.summary.setText(self.image.summary())

    # --- doing it ------------------------------------------------------------------------
    def _target(self) -> tuple[int, NodeDevice, Firmware] | None:
        node_id = self.selected_node()
        node = self.manager.node(node_id) if node_id is not None else None
        if node is None:
            self._warn("No node selected, or it is not on the bus.")
            return None
        device = NodeDevice(node, block=self.transfer.currentText() == BLOCK)
        return node_id, device, Firmware(self.hooks, self.manager.identity(node_id))

    def _agreed_to_stop(self, node_id: int) -> bool:
        """Asked once a session for each node, as enabling a drive is.

        Stopping the program is the same class of thing: whatever it was
        controlling stops being controlled. Keyed on the node, so agreeing for
        a bench unit is not agreeing for the machine beside it.
        """
        return self.confirm.ask(
            self, f"firmware.stop.{node_id}", STOP_TITLE, STOP_TEXT.format(node=node_id)
        )

    def enter_bootloader(self) -> None:
        target = self._target()
        if target is None or self._busy:
            return
        node_id, device, firmware = target
        if not self._agreed_to_stop(node_id):
            return
        number = self.program.value()
        self._one("Requesting the bootloader", lambda: firmware.enter(device, number))

    def exit_bootloader(self) -> None:
        target = self._target()
        if target is None or self._busy:
            return
        _node_id, device, firmware = target
        number = self.program.value()
        self._one("Leaving the bootloader", lambda: firmware.leave(device, number))

    def _one(self, what: str, job) -> None:
        self._start(what)
        self.manager.background(job, lambda _r, error: self._finished(what, error))

    def read_version(self) -> None:
        target = self._target()
        if target is None or self._busy:
            return
        node_id, device, firmware = target
        number = self.program.value()

        def job():
            return (firmware.version(device, number), *firmware.status(device, number))

        def done(result, error):
            self._busy = False
            self.node_changed()
            if error:
                self._warn(f"reading the version failed: {error}")
                return
            version, software, status = result
            missing = "not kept by this device"
            self.state.setText(
                f"Software version: {version or missing}\n"
                f"Software identification: {software or missing}    "
                f"Flash status: {status or missing}"
            )
            self._say(f"node {node_id} software version: {version or missing}")

        self._busy = True
        self.node_changed()
        self.manager.background(job, done)

    def _download(self) -> None:
        target = self._target()
        if target is None or self._busy:
            return
        if self.image is None:
            self._warn("no image chosen.")
            return
        node_id, device, firmware = target
        image = self.image
        if not self._agreed_to_stop(node_id):  # the first step stops the program
            return
        number = self.program.value()
        seconds = self.patience.value()

        def job():
            if refused := firmware.check(device, image, number):
                raise RuntimeError(f"refused: {refused}")
            # The whole sequence, not only the erase: a device may be as slow to
            # answer the last block, or the start that checks the image.
            # All of it worked out before the first step runs: an image the
            # sequence cannot take is refused with nothing written.
            sequence = firmware.steps(device, image, number, self.progressed.emit)
            with device.patient(seconds):
                for step in sequence:
                    self.ctx.events.information(f"Firmware: {step.what}")
                    step.run()
            return firmware.version(device, number)

        def done(version, error):
            self._finished("Programming", error)
            if not error and version:
                self._say(f"node {node_id} now runs {version}")

        self._start("Programming")
        # The rest of pycangui's traffic held back while the program goes in:
        # a node rebooting into its loader would otherwise be identified, and
        # its PDOs read, over SDO in the middle of it.
        self.ctx.pause.hold(FLASHING)
        self.progress.setRange(0, max(1, firmware.size(image)))
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self.manager.background(job, done)

    def _start(self, what: str) -> None:
        self._busy = True
        self.state.setText(f"{what}...")
        self.node_changed()

    def _finished(self, what: str, error) -> None:
        self._busy = False
        self.ctx.pause.release(FLASHING)  # nothing if it was not held
        self.node_changed()
        self.progress.setVisible(False)
        if error:
            self.state.setText(f"{what} failed: {error}")
            self._warn(f"{what} failed: {error}")
            return
        self.state.setText(f"{what}: done.")
        self._say(f"{what}: done.")
