"""Recording traffic to a file and replaying it.

Both directions use python-can's own readers and writers, so every format it
supports works from one file dialog: ``.blf`` (Vector binary), ``.asc``
(Vector ASCII), ``.trc`` (PEAK), ``.csv``, ``.log`` (candump), ``.db``
(SQLite).  The format is chosen from the file extension.

* **Recording** attaches a writer to every connected channel's ``can.Notifier``,
  so it records exactly what is on the bus regardless of what the trace is
  filtering, and costs the GUI thread nothing.
* **Replay** runs on a worker thread and honours the recorded timing.  (The
  timing loop is written out rather than using ``can.MessageSync`` so that the
  speed can be scaled and a long replay can be stopped promptly.)  It always
  transmits, onto one channel chosen when it starts; replaying onto a
  ``virtual`` channel is how a recording is examined with no hardware attached,
  and needs no separate offline mode because a virtual bus loops its own
  traffic back into the trace and the decoders.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

import can
from PySide6.QtCore import QObject, QThread, Signal, Slot

from pycangui.core.bus import BusManager

if TYPE_CHECKING:
    from pycangui.core.channels import Channels

# Extensions python-can can write / read, best first for the file dialogs.
WRITE_FILTER = (
    "Vector binary (*.blf);;Vector ASCII (*.asc);;PEAK trace (*.trc);;"
    "candump log (*.log);;CSV (*.csv);;SQLite (*.db);;All files (*)"
)
READ_FILTER = (
    "CAN logs (*.blf *.asc *.trc *.log *.csv *.db);;Vector binary (*.blf);;"
    "Vector ASCII (*.asc);;PEAK trace (*.trc);;candump log (*.log);;CSV (*.csv);;"
    "SQLite (*.db);;All files (*)"
)


class _StampedWriter(can.Listener):
    """Hands messages to the log writer tagged with the channel they came in on.

    Every log format python-can writes carries a channel field, so a recording
    of two channels can be told apart afterwards -- but python-can only fills
    it in for some backends, and never with the name shown in the trace.

    The name is written with its whitespace replaced, because the text formats
    are space separated: a channel called "CAN 1" produces a candump line with
    one field too many, which python-can's own reader then refuses.
    """

    def __init__(self, writer: can.Listener, channel: str) -> None:
        self._writer = writer
        self._channel = "_".join(channel.split()) or "CAN"

    def on_message_received(self, msg: can.Message) -> None:
        msg.channel = self._channel
        self._writer.on_message_received(msg)

    def stop(self) -> None:
        """Does nothing: the file belongs to the recorder, which closes it once."""


class Recorder(QObject):
    """Writes every frame on every connected channel to a log file.

    Deliberately *not* tied to the selected channel.  A recording runs in the
    background for minutes while the selection is used to drive the protocol
    panes, so following the selection meant a recording silently stopped when
    the selection moved off the channel it started on.  What gets recorded is
    what the trace shows.
    """

    state = Signal(bool, str)  # recording?, path (empty when stopped)
    error = Signal(str)
    note = Signal(str)  # worth mentioning, but not a failure

    def __init__(self, channels: Channels) -> None:
        super().__init__()
        self._channels = channels
        self._writer: can.Listener | None = None
        self._attached: dict[str, _StampedWriter] = {}
        self.path: Path | None = None
        self._started = 0.0
        channels.state_changed.connect(self._on_channel_state)

    @property
    def is_recording(self) -> bool:
        return self._writer is not None

    @property
    def channels_recorded(self) -> list[str]:
        return list(self._attached)

    def start(self, path: str | Path) -> bool:
        self.stop()
        if not self._channels.any_connected:
            self.error.emit("Record: connect a channel first")
            return False
        try:
            self._writer = can.Logger(str(path))
        except Exception as exc:  # unknown extension, unwritable path, ...
            self.error.emit(f"Record: cannot write {path}: {exc}")
            self._writer = None
            return False
        for name in self._channels.names():
            self._attach(name)
        self.path = Path(path)
        self._started = time.monotonic()
        self.state.emit(True, str(path))
        return True

    @Slot()
    def stop(self) -> None:
        if self._writer is None:
            return
        for name in list(self._attached):
            self._detach(name)
        writer, self._writer = self._writer, None
        try:
            writer.stop()  # flushes and closes the file
        except Exception as exc:  # report it; the file may still be usable
            self.error.emit(f"Record: closing the file failed: {exc}")
        self.state.emit(False, "")

    # --- following the channels, not the selection ---------------------------------
    def _attach(self, name: str) -> None:
        bus = self._channels.get(name)
        if self._writer is None or name in self._attached or bus is None or not bus.is_connected:
            return
        listener = _StampedWriter(self._writer, name)
        bus.add_listener(listener)
        self._attached[name] = listener

    def _detach(self, name: str) -> None:
        listener = self._attached.pop(name, None)
        bus = self._channels.get(name)
        if listener is not None and bus is not None:
            bus.remove_listener(listener)

    @Slot(str, bool)
    def _on_channel_state(self, name: str, connected: bool) -> None:
        """A channel coming or going mid recording joins or leaves the file.

        The recording itself carries on either way: stopping it because a
        channel dropped would truncate the file without saying so.
        """
        if not self.is_recording:
            return
        if connected:
            self._attach(name)
            self.note.emit(f"Record: {name} connected, now also being recorded")
        elif name in self._attached:
            self._detach(name)
            self.note.emit(f"Record: {name} disconnected, still recording to {self.path}")

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._started if self.is_recording else 0.0


class Player(QThread):
    """Replays a log file onto one channel, with its original timing.

    The channel is passed in and held for the whole run, rather than being
    looked up as each frame goes out: a replay lasts minutes, and the channel
    the user selects meanwhile is none of its business.
    """

    progress = Signal(int, float)  # messages played, seconds of log time
    finished_playing = Signal(str)  # reason: "end", "stopped", or an error

    def __init__(
        self,
        path: str | Path,
        bus: BusManager,
        *,
        speed: float = 1.0,
        loop: bool = False,
        max_gap_s: float = 2.0,
    ) -> None:
        super().__init__()
        self.path = Path(path)
        self._bus = bus
        self.speed = max(0.01, speed)
        self.loop = loop
        self.max_gap_s = max_gap_s
        self._stop = False

    def stop(self) -> None:
        self._stop = True
        self.wait(2000)

    def run(self) -> None:
        reason = "end"
        try:
            while not self._stop:
                self._play_once()
                if not self.loop or self._stop:
                    break
        except Exception as exc:  # unreadable or corrupt file
            reason = f"{type(exc).__name__}: {exc}"
        self.finished_playing.emit("stopped" if self._stop else reason)

    def _play_once(self) -> None:
        with can.LogReader(str(self.path)) as reader:
            count = 0
            first = None
            previous: float | None = None
            for message in reader:
                if self._stop:
                    break
                if first is None:
                    first = message.timestamp
                # Sleep the recorded gap, scaled by speed and capped so long
                # idle stretches do not stall the replay.
                if previous is not None:
                    gap = min(max(message.timestamp - previous, 0.0), self.max_gap_s) / self.speed
                    if gap > 0.0005:
                        time.sleep(gap)
                previous = message.timestamp
                count += 1
                self._bus.send(
                    message.arbitration_id,
                    bytes(message.data),
                    extended=message.is_extended_id,
                    fd=message.is_fd,
                )
                if count % 25 == 0:
                    self.progress.emit(count, message.timestamp - first)
            self.progress.emit(count, (previous - first) if previous and first else 0.0)
