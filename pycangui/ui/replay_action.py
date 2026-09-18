# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Replay, as a toolbar button beside Record.

Recording and replaying are the two halves of one idea, so they sit together
rather than replay having a pane of its own.

There is no "transmit onto the bus" option because the channel already says
it: replaying onto a ``virtual`` channel puts the frames into the trace and
the decoders and nowhere else, which is how a recording is examined with no
hardware attached, while replaying onto a real channel is real traffic on a
real bus. That is also why replaying onto a real bus asks first.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Slot
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QFileDialog, QMenu, QMessageBox, QToolBar, QToolButton

from pycangui.core.bus import BusManager
from pycangui.core.channels import Channels
from pycangui.core.context import Context
from pycangui.core.logging import READ_FILTER, Player
from pycangui.ui import folders, messages
from pycangui.ui.confirm import Confirmations, is_real

SPEEDS = (0.1, 0.5, 1.0, 2.0, 5.0, 20.0)
RECENT_MAX = 5
VIRTUAL_CHANNEL = "Virtual"


class ReplayAction(QObject):
    """The Replay button, its menu, and the player behind them."""

    def __init__(
        self,
        toolbar: QToolBar,
        channels: Channels,
        ctx: Context,
        confirm: Confirmations | None = None,
    ) -> None:
        super().__init__(toolbar)
        self.channels = channels
        self.ctx = ctx
        self.confirm = confirm if confirm is not None else Confirmations()
        self.player: Player | None = None
        #: Pinned when the replay starts, so selecting a different channel
        #: while it runs cannot redirect the frames onto another bus.
        self._playing_on = ""

        self.action = QAction("Replay", self)
        self.action.setCheckable(True)
        self.action.toggled.connect(self._toggle)

        self.menu = QMenu(toolbar)
        browse = self.menu.addAction("Choose file...")
        browse.triggered.connect(self._browse)
        self._recent_before = self.menu.addSeparator()
        speed_menu = self.menu.addMenu("Speed")
        self._speed_group = QActionGroup(self)
        for value in SPEEDS:
            item = speed_menu.addAction(f"{value:g}x")
            item.setCheckable(True)
            item.setData(value)
            item.setChecked(value == 1.0)
            self._speed_group.addAction(item)
        self.loop_action = self.menu.addAction("Loop")
        self.loop_action.setCheckable(True)
        self.loop_action.setChecked(bool(ctx.settings.get("replay.loop", False)))

        self.button = QToolButton(toolbar)
        self.button.setDefaultAction(self.action)
        self.button.setMenu(self.menu)
        self.button.setPopupMode(QToolButton.MenuButtonPopup)
        toolbar.addWidget(self.button)

        self._recent = [p for p in ctx.settings.get("replay.recent", []) if Path(p).is_file()]
        self._path = Path(self._recent[0]) if self._recent else None
        self._refresh_recent()
        self._refresh_text()

    # --- the file ------------------------------------------------------------------
    @property
    def speed(self) -> float:
        chosen = self._speed_group.checkedAction()
        return chosen.data() if chosen else 1.0

    def _browse(self) -> bool:
        # Its own recent list first -- that is a file, not a folder, and more
        # specific than either. Otherwise wherever a log was last recorded or
        # replayed, which is usually the same place.
        if self._path is not None:
            path, chosen_type = QFileDialog.getOpenFileName(
                self.button,
                "Replay a log",
                str(self._path.parent),
                READ_FILTER,
                folders.remembered_type(self.ctx, folders.LOG, READ_FILTER),
            )
            if path:
                folders.remember(self.ctx, folders.LOG, path, chosen_type)
        else:
            path = folders.open_file(
                self.button,
                self.ctx,
                folders.LOG,
                "Replay a log",
                READ_FILTER,
                self.ctx.user_dir,
            )
        if not path:
            return False
        self._remember(Path(path))
        return True

    def _remember(self, path: Path) -> None:
        self._path = path
        text = str(path)
        self._recent = [text, *[p for p in self._recent if p != text]][:RECENT_MAX]
        self.ctx.settings.set("replay.recent", self._recent)
        self._refresh_recent()
        self._refresh_text()

    def _refresh_recent(self) -> None:
        """Rebuild the recent files, between "Choose file..." and the settings.

        Replaying the same log several times over is the normal way this gets
        used, so the last few are one click away.
        """
        for action in list(self.menu.actions()):
            if action.data() == "recent":
                self.menu.removeAction(action)
        for text in self._recent:
            action = QAction(Path(text).name, self.menu)
            action.setData("recent")
            action.setToolTip(text)
            action.triggered.connect(lambda _checked=False, t=text: self._remember(Path(t)))
            self.menu.insertAction(self._recent_before, action)

    def _refresh_text(self) -> None:
        # The file name lives in the tooltip: it is far too long for a toolbar
        # button, and is only wanted when you stop to wonder what is loaded.
        if self.player is not None:
            self.action.setText("Replaying...")
            self.action.setToolTip(
                f"Replaying {self._path} onto {self._playing_on}. Click to stop."
            )
        elif self._path is not None:
            self.action.setText("Replay")
            self.action.setToolTip(f"Replay {self._path}\nUse the arrow to choose another.")
        else:
            self.action.setText("Replay")
            self.action.setToolTip("Replay a recorded log file onto the selected channel")

    # --- playing -------------------------------------------------------------------
    @Slot(bool)
    def _toggle(self, on: bool) -> None:
        if on:
            self.start()
        else:
            self.stop()

    def start(self) -> None:
        if (self._path is None or not self._path.is_file()) and not self._browse():
            self._reset()
            return
        bus = self._target_channel()
        if bus is None:
            self._reset()
            return
        self.ctx.settings.set("replay.loop", self.loop_action.isChecked())
        self.player = Player(self._path, bus, speed=self.speed, loop=self.loop_action.isChecked())
        self._playing_on = bus.channel_name
        self.player.finished_playing.connect(self._on_finished)
        self.player.start()
        self._refresh_text()
        self.ctx.log(f"Replay started on {self._playing_on} at {self.speed:g}x: {self._path.name}")

    def stop(self) -> None:
        if self.player is not None:
            self.player.stop()
            self.player = None
        self._reset()

    def _target_channel(self) -> BusManager | None:
        """The channel to replay onto, or None if the user backed out."""
        bus = self.channels.active_bus()
        if bus is None or not bus.is_connected:
            return self._offer_virtual()
        if not is_real(bus.interface):
            return bus
        name = self._path.name if self._path else "the log"
        # Keyed separately from the Transmit pane's question: agreeing to send
        # one frame by hand is not agreeing to pour a whole log onto the bus.
        allowed = self.confirm.ask(
            self.button,
            f"replay:{bus.channel_name}:{bus.description}",
            "Replay onto a real CAN bus?",
            f"{bus.channel_name} is connected to {bus.description}.\n\n"
            f"Replaying puts every frame in {name} onto that bus, and the "
            "devices on it will act on them.\n\n"
            "To replay without transmitting, connect a virtual channel and "
            "select that instead.",
        )
        return bus if allowed else None

    def _offer_virtual(self) -> BusManager | None:
        """Nothing is connected: offer the virtual channel a replay needs.

        Looking at a recording with no hardware attached is the commonest
        reason to replay at all, and it should not require knowing that a
        virtual channel is how you do it.
        """
        answer = messages.question(
            self.button,
            "No channel connected",
            "A replay needs a channel to play onto.\n\n"
            "Connect a virtual one? The frames then go to the trace and the "
            "decoders without any hardware, which is what you want for looking "
            "at a recording.",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Yes,
        )
        if answer != QMessageBox.Yes:
            return None
        bus = self.channels.add(VIRTUAL_CHANNEL)
        self.channels.set_active(VIRTUAL_CHANNEL)
        bus.connect_bus("virtual", "pycangui_replay", 500000, False)
        return bus if bus.is_connected else None

    def _reset(self) -> None:
        self._playing_on = ""
        self.action.blockSignals(True)
        self.action.setChecked(False)
        self.action.blockSignals(False)
        self._refresh_text()

    @Slot(str)
    def _on_finished(self, reason: str) -> None:
        # Waited for before the reference is dropped. The signal is emitted
        # from the last line of the player's run(), so when this arrives the
        # thread is a moment from exiting but has not exited: letting the
        # object be collected there destroys a QThread that is still
        # running, which aborts the process rather than raising. That is
        # what killed a CI worker here more than once.
        player, self.player = self.player, None
        if player is not None:
            player.wait(2000)
            player.deleteLater()
        self._reset()
        name = self._path.name if self._path else ""
        if reason in ("end", "stopped"):
            self.ctx.log(f"Replay {reason}: {name}")
        else:
            self.ctx.warn(f"Replay failed: {reason}")
