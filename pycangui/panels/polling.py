"""Reading the same objects over and over, no faster than they answer.

An object that is not mapped to a PDO can only be read by asking for it, and
watching one change means asking again.  That is what this does: a round of
requests, and when the answers are all in, another round.

**The requested rate is a ceiling, not a promise, and the difference is the
point.**  An SDO read is a request and a response on the bus, through a queue,
against a controller that answers when it feels like it; asking for twelve
objects at 100 Hz is asking for twelve hundred round trips a second, which no
node is going to do.  So the achieved rate is measured and shown.  A tool that
displayed the number somebody typed would be reporting their hopes back to
them, and a value read at 12 Hz that looks like it was read at 100 is the sort
of thing people build conclusions on.

The other half of that is not queueing.  Firing a timer faster than the
answers come back builds a backlog that grows until the tool is showing values
from a minute ago, and it looks exactly like a slow bus rather than like a
mistake.  A round is only started when the previous one has finished, so a
request is never outstanding twice and the reported rate is the truth about
what the bus is doing.
"""

from __future__ import annotations

import time
from collections.abc import Iterable

from PySide6.QtCore import QObject, QTimer, Signal

#: Round times kept for the achieved rate.  Enough to be steady, few enough to
#: notice a bus that has just slowed down.
RATE_SAMPLES = 8

#: A round is abandoned after this, and the next one started.  Every request
#: normally produces exactly one answer -- an abort is an answer -- so this is
#: for the case where something has gone wrong enough that one never arrives:
#: without it the poller would wait for it forever and look like a hang.
ROUND_TIMEOUT_S = 5.0

#: What the box offers.  The bottom is one read a minute, for something that
#: changes slowly and should not be hammered; the top is well past what any
#: SDO poll will really manage, which is the point -- ask for it and the
#: achieved figure tells you what you actually got.
MIN_HZ = 0.02
MAX_HZ = 50.0
DEFAULT_HZ = 2.0


class Poller(QObject):
    """One set of objects, read over and over at up to a requested rate."""

    #: Ask for this object.  Whatever the panel is bound to answers it.
    read = Signal(int, int)
    #: Requested rate, and the rate actually being achieved (0 until a round
    #: has completed).
    rate = Signal(float, float)
    #: Polling started or stopped.
    running_changed = Signal(bool)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._objects: list[tuple[int, int]] = []
        self._outstanding: set[tuple[int, int]] = set()
        self._hz = DEFAULT_HZ
        self._running = False
        self._round_started = 0.0
        self._rounds: list[float] = []  # how long recent rounds took
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._round)
        self._watchdog = QTimer(self)
        self._watchdog.setSingleShot(True)
        self._watchdog.timeout.connect(self._give_up_on_round)

    # --- what to read, and how often --------------------------------------------------
    def set_objects(self, objects: Iterable[tuple[int, int]]) -> None:
        """The objects to read each round.  Takes effect at the next one."""
        self._objects = list(dict.fromkeys(objects))  # in order, without repeats

    @property
    def objects(self) -> list[tuple[int, int]]:
        return list(self._objects)

    @property
    def running(self) -> bool:
        return self._running

    @property
    def hz(self) -> float:
        return self._hz

    def set_rate(self, hz: float) -> None:
        self._hz = max(MIN_HZ, min(float(hz), MAX_HZ))
        self._announce()

    def start(self, hz: float | None = None) -> None:
        if hz is not None:
            self._hz = max(MIN_HZ, min(float(hz), MAX_HZ))
        if self._running:
            return
        self._running = True
        self._rounds.clear()
        self.running_changed.emit(True)
        self._announce()
        self._round()

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._timer.stop()
        self._watchdog.stop()
        self._outstanding.clear()
        self.running_changed.emit(False)
        self._announce()

    # --- a round ------------------------------------------------------------------------
    def _round(self) -> None:
        if not self._running:
            return
        if not self._objects:
            # Nothing to read: keep the clock running rather than stopping, so
            # that a panel which gains a field starts polling it without
            # anybody having to press the button again.
            self._timer.start(self._interval_ms())
            return
        self._round_started = time.monotonic()
        self._outstanding = set(self._objects)
        self._watchdog.start(int(ROUND_TIMEOUT_S * 1000))
        for index, sub in self._objects:
            self.read.emit(index, sub)

    def answered(self, index: int, sub: int) -> None:
        """An answer arrived.  When they all have, the round is done."""
        if not self._running:
            return
        self._outstanding.discard((index, sub))
        if self._outstanding:
            return
        self._watchdog.stop()
        self._finish_round(time.monotonic() - self._round_started)

    def _give_up_on_round(self) -> None:
        """Something never answered.  Start again rather than wait forever."""
        if not self._running:
            return
        self._outstanding.clear()
        self._finish_round(time.monotonic() - self._round_started)

    def _finish_round(self, took: float) -> None:
        self._rounds.append(max(took, 0.0))
        del self._rounds[:-RATE_SAMPLES]
        self._announce()
        # Whichever is longer: the rate that was asked for, or the time the bus
        # actually needs.  Asking for more than it can do is answered by going
        # as fast as it can, not by queueing up the difference.
        wait = max(0.0, (1.0 / self._hz) - took)
        self._timer.start(int(wait * 1000))

    def _interval_ms(self) -> int:
        return max(1, int(1000.0 / self._hz))

    # --- what it is actually managing ------------------------------------------------------
    @property
    def achieved(self) -> float:
        """Rounds a second, over the recent ones.  0 until one has completed."""
        if not self._running or not self._rounds:
            return 0.0
        mean = sum(self._rounds) / len(self._rounds)
        # The gap between rounds counts too: a poller asked for 1 Hz and
        # managing 100 ms rounds is achieving 1 Hz, not 10.
        cycle = max(mean, 1.0 / self._hz)
        return 1.0 / cycle if cycle > 0 else 0.0

    def _announce(self) -> None:
        self.rate.emit(self._hz, self.achieved)


def rate_text(requested: float, achieved: float, running: bool) -> str:
    """What to put on screen beside the box.

    Says the achieved figure and nothing else while the two agree, because
    repeating the number somebody just typed tells them nothing.  It is when
    they disagree that it matters, and then it says so plainly.
    """
    if not running:
        return ""
    if achieved <= 0:
        return "starting..."
    shown = f"{achieved:.2f} Hz" if achieved < 1 else f"{achieved:.1f} Hz"
    # Within a tenth is the bus keeping up; the figure wobbles a little either
    # way and calling that a shortfall would cry wolf.
    if achieved >= requested * 0.9:
        return shown
    return f"{shown} (asked for {requested:g})"
