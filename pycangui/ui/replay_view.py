"""Replay pane: play a recorded log back, either onto the bus or straight
into pycangui's decoders without transmitting anything."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pycangui.core.bus import BusManager
from pycangui.core.context import Context
from pycangui.core.logging import READ_FILTER, Player

SPEEDS = ((0.1, "0.1x"), (0.5, "0.5x"), (1.0, "1x"), (2.0, "2x"), (5.0, "5x"), (20.0, "20x"))


class ReplayView(QWidget):
    #: Frames from an *offline* replay.  The main window feeds these to the
    #: trace and the decoders, exactly as if they had come from the bus.
    frames_replayed = Signal(list)

    def __init__(self, bus: BusManager, ctx: Context) -> None:
        super().__init__()
        self.bus = bus
        self.ctx = ctx
        self.player: Player | None = None

        self.path = QLineEdit(ctx.settings.get("replay.path", ""))
        self.path.setPlaceholderText("log file (.blf, .asc, .trc, .log, .csv, .db)")
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._browse)

        self.transmit = QCheckBox("Transmit onto the bus")
        self.transmit.setToolTip(
            "Off: the frames are only fed to the trace and decoders, nothing is sent"
        )
        self.transmit.setChecked(bool(ctx.settings.get("replay.transmit", False)))
        self.speed = QComboBox()
        for value, label in SPEEDS:
            self.speed.addItem(label, value)
        self.speed.setCurrentIndex(2)
        self.loop = QCheckBox("Loop")
        self.play_btn = QPushButton("Play")
        self.play_btn.setCheckable(True)
        self.play_btn.toggled.connect(self._toggle)

        self.status = QLabel("idle")
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)  # busy indicator; log length is unknown up front
        self.bar.setVisible(False)

        top = QHBoxLayout()
        top.addWidget(QLabel("File"))
        top.addWidget(self.path, 1)
        top.addWidget(browse)
        controls = QHBoxLayout()
        controls.addWidget(self.transmit)
        controls.addWidget(QLabel("Speed"))
        controls.addWidget(self.speed)
        controls.addWidget(self.loop)
        controls.addWidget(self.play_btn)
        controls.addStretch()
        controls.addWidget(self.status)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.addLayout(top)
        layout.addLayout(controls)
        layout.addWidget(self.bar)
        layout.addStretch()

    # --- controls ---------------------------------------------------------------
    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Replay a log", str(self.ctx.user_dir), READ_FILTER
        )
        if path:
            self.path.setText(path)

    @Slot(bool)
    def _toggle(self, on: bool) -> None:
        if on:
            self.start()
        else:
            self.stop()

    def start(self) -> None:
        path = Path(self.path.text().strip())
        if not path.is_file():
            self.ctx.log(f"Replay: no such file: {path}")
            self._reset("no file")
            return
        if self.transmit.isChecked() and not self.bus.is_connected:
            self.ctx.log("Replay: connect to a bus first, or untick Transmit")
            self._reset("not connected")
            return
        self.ctx.settings.set("replay.path", str(path))
        self.ctx.settings.set("replay.transmit", self.transmit.isChecked())
        self.player = Player(
            path,
            self.bus,
            transmit=self.transmit.isChecked(),
            speed=self.speed.currentData(),
            loop=self.loop.isChecked(),
        )
        self.player.progress.connect(self._on_progress)
        self.player.finished_playing.connect(self._on_finished)
        if not self.transmit.isChecked():
            self.player.frames.connect(self.frames_replayed)  # signal-to-signal
        self.player.start()
        self.play_btn.setText("Stop")
        self.bar.setVisible(True)
        how = "transmitting" if self.transmit.isChecked() else "offline"
        self.ctx.log(f"Replay started ({how}): {path.name}")

    def stop(self) -> None:
        if self.player is not None:
            self.player.stop()
            self.player = None
        self._reset("stopped")

    def _reset(self, text: str) -> None:
        self.play_btn.blockSignals(True)
        self.play_btn.setChecked(False)
        self.play_btn.setText("Play")
        self.play_btn.blockSignals(False)
        self.bar.setVisible(False)
        self.status.setText(text)

    @Slot(int, float)
    def _on_progress(self, count: int, seconds: float) -> None:
        self.status.setText(f"{count} messages, {seconds:.1f} s of log")

    @Slot(str)
    def _on_finished(self, reason: str) -> None:
        self.player = None
        self._reset(reason)
        if reason not in ("end", "stopped"):
            self.ctx.log(f"Replay failed: {reason}")
