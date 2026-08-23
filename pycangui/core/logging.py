"""Recording traffic to a file and replaying it.

Both directions use python-can's own readers and writers, so every format it
supports works from one file dialog: ``.blf`` (Vector binary), ``.asc``
(Vector ASCII), ``.trc`` (PEAK), ``.csv``, ``.log`` (candump), ``.db``
(SQLite).  The format is chosen from the file extension.

* **Recording** attaches a writer to the shared ``can.Notifier``, so it records
  exactly what is on the bus regardless of what the trace is filtering, and
  costs the GUI thread nothing.
* **Replay** runs on a worker thread and honours the recorded timing.  (The
  timing loop is written out rather than using ``can.MessageSync`` so that the
  speed can be scaled and a long replay can be stopped promptly.)  It can
  either *transmit* the frames, or feed them straight into pycangui without
  touching the bus -- useful for looking at a recording from a colleague with
  no hardware attached.
"""

from __future__ import annotations

import time
from pathlib import Path

import can
from PySide6.QtCore import QObject, QThread, Signal, Slot

from pycangui.core.bus import BusManager, Frame

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


class Recorder(QObject):
    """Writes every frame on the bus to a log file."""

    state = Signal(bool, str)  # recording?, path (empty when stopped)
    error = Signal(str)

    def __init__(self, bus: BusManager) -> None:
        super().__init__()
        self._bus = bus
        self._writer: can.Listener | None = None
        self.path: Path | None = None
        self._started = 0.0
        bus.disconnected.connect(self.stop)

    @property
    def is_recording(self) -> bool:
        return self._writer is not None

    def start(self, path: str | Path) -> bool:
        self.stop()
        if not self._bus.is_connected:
            self.error.emit("Record: not connected to a bus")
            return False
        try:
            self._writer = can.Logger(str(path))
        except Exception as exc:  # unknown extension, unwritable path, ...
            self.error.emit(f"Record: cannot write {path}: {exc}")
            return False
        self._bus.add_listener(self._writer)
        self.path = Path(path)
        self._started = time.monotonic()
        self.state.emit(True, str(path))
        return True

    @Slot()
    def stop(self) -> None:
        if self._writer is None:
            return
        writer, self._writer = self._writer, None
        self._bus.remove_listener(writer)
        try:
            writer.stop()  # flushes and closes the file
        except Exception as exc:  # report it; the file may still be usable
            self.error.emit(f"Record: closing the file failed: {exc}")
        self.state.emit(False, "")

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._started if self.is_recording else 0.0


class Player(QThread):
    """Replays a log file with its original timing."""

    frames = Signal(list)  # batches of Frame, when not transmitting
    progress = Signal(int, float)  # messages played, seconds of log time
    finished_playing = Signal(str)  # reason: "end", "stopped", or an error

    BATCH_PERIOD_S = 0.02

    def __init__(
        self,
        path: str | Path,
        bus: BusManager,
        *,
        transmit: bool,
        speed: float = 1.0,
        loop: bool = False,
        max_gap_s: float = 2.0,
    ) -> None:
        super().__init__()
        self.path = Path(path)
        self._bus = bus
        self.transmit = transmit
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
            last_emit = time.monotonic()
            batch: list[Frame] = []
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
                if self.transmit:
                    self._bus.send(
                        message.arbitration_id,
                        bytes(message.data),
                        extended=message.is_extended_id,
                        fd=message.is_fd,
                    )
                else:
                    batch.append(
                        Frame(
                            timestamp=message.timestamp - first,
                            channel=self.path.name,
                            can_id=message.arbitration_id,
                            extended=message.is_extended_id,
                            fd=message.is_fd,
                            rx=True,
                            data=bytes(message.data),
                        )
                    )
                now = time.monotonic()
                if batch and now - last_emit >= self.BATCH_PERIOD_S:
                    self.frames.emit(batch)
                    batch = []
                    last_emit = now
                if count % 25 == 0:
                    self.progress.emit(count, message.timestamp - first)
            if batch:
                self.frames.emit(batch)
            self.progress.emit(count, (previous - first) if previous and first else 0.0)
