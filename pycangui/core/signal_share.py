# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Chosen signals sent over UDP multicast, for pyscopegui to show and trigger on.

pyscopegui already sends pycangui the CAN frames it decodes; this is the other
way round. The signals ticked *UDP out* in Signals and Plot go out on a group of
their own as two kinds of JSON message:

- **a description**, every second and whenever a tick changes: a short number
  for each signal, with its name, unit, limits and value names, so a receiver
  makes its channels from it whichever program started first;
- **values**, as they arrive: per frame time, the numbers and values decoded
  at it -- the signals of one CAN frame share a time, so it is sent once.

Every message carries this run's id and the description's count, so values are
read against the description that numbered them: a restart, or a tick changed,
cannot make one signal's values pass for another's.

Times are ``time.monotonic`` seconds, the clock pyscopegui lines its own
instruments up on, so the two agree exactly on one computer. The description
also carries that clock and the wall clock read together, for a receiver on
another computer to convert with.
"""

from __future__ import annotations

import json
import math
import secrets
import socket
import time
from collections.abc import Callable

from PySide6.QtCore import QObject, QTimer, Signal

from pycangui.core.signals import SignalHub

#: Where the signals go: a group of their own, clear of the CAN groups below it.
GROUP = "239.74.163.20"
#: python-can's udp_multicast port, which the CAN groups use too.
PORT = 43113
#: The message format. A receiver ignores a version it does not know.
VERSION = 1
#: How often the description goes out while nothing changes.
DESCRIBE_MS = 1000
#: Kept under a typical network's packet size, so nothing is split on the way.
DATAGRAM_BYTES = 1400

#: (lowest, highest) from the database, or None where it gives none.
Limits = Callable[[str], tuple[float, float] | None]


def _number(value: float) -> float | int:
    """As short as JSON will write it: an integer where the value is one."""
    return int(value) if float(value).is_integer() and abs(value) < 2**53 else value


class SignalSender(QObject):
    """Sends the ticked signals, while switched on."""

    #: A ticked signal was ticked or unticked here, for every list to show.
    sent_changed = Signal(str, bool)
    #: Something to say in the Event Log: (text, whether it is a warning).
    said = Signal(str, bool)

    def __init__(
        self,
        hub: SignalHub,
        bus_now: Callable[[], float],
        limits: Limits | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.hub = hub
        #: The window's clock, which the hub's times are on.
        self._bus_now = bus_now
        self._limits = limits or (lambda _key: None)
        self.enabled = False
        self.hop_limit = 0
        self.run = secrets.token_hex(4)
        #: The description's count, one more each time the numbers change.
        self.generation = 0
        #: What is ticked, in the order it was: kept even before a signal has
        #: arrived, as the plot keeps what it plots.
        self.sent: list[str] = []
        #: Key -> its short number, for the ticked signals the hub has.
        self._numbers: dict[str, int] = {}
        self._next = 1
        #: Samples waiting to go: (time, number, value).
        self._queue: list[tuple[float, int, float]] = []
        self._socket: socket.socket | None = None
        self._failed = False
        self._describe_timer = QTimer(self, interval=DESCRIBE_MS, timeout=self.describe)
        self._flush_timer = QTimer(self, singleShot=True, interval=0, timeout=self.flush)
        hub.added.connect(self._on_added)
        hub.removed.connect(self._on_removed)

    # --- switching on and off -------------------------------------------------------
    def set_enabled(self, on: bool) -> None:
        if on == self.enabled:
            return
        self.enabled = on
        if on:
            self._open()
            self.hub.taps.append(self._on_sample)
            self._describe_timer.start()
            self.describe()
            reach = "the local network" if self.hop_limit else "this computer"
            self.said.emit(f"Sending ticked signals to {GROUP}:{PORT}, on {reach}", False)
        else:
            if self._on_sample in self.hub.taps:
                self.hub.taps.remove(self._on_sample)
            self._describe_timer.stop()
            self._flush_timer.stop()
            self._queue.clear()
            self._close()
            self.said.emit("No longer sending signals", False)

    def set_hop_limit(self, hops: int) -> None:
        """How far the messages go: 0 this computer, 1 the local network."""
        self.hop_limit = hops
        if self._socket is not None:
            self._socket.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, hops)

    def _open(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, self.hop_limit)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
        self._socket, self._failed = sock, False

    def _close(self) -> None:
        if self._socket is not None:
            self._socket.close()
            self._socket = None

    # --- what is ticked ---------------------------------------------------------------
    def set_sent(self, key: str, on: bool) -> None:
        if on == (key in self.sent):
            return
        if on:
            self.sent.append(key)
            if self.hub.get(key) is not None:
                self._number(key)
        else:
            self.sent.remove(key)
            self._numbers.pop(key, None)
        self.generation += 1
        self.sent_changed.emit(key, on)
        if self.enabled:
            self.describe()

    def set_all_sent(self, keys) -> None:
        """What a workspace had ticked, put back without a message per signal."""
        self.sent = list(dict.fromkeys(keys))
        self._numbers.clear()
        for key in self.sent:
            if self.hub.get(key) is not None:
                self._number(key)
        self.generation += 1

    def _number(self, key: str) -> int:
        if key not in self._numbers:
            self._numbers[key] = self._next
            self._next += 1
        return self._numbers[key]

    def _on_added(self, key: str) -> None:
        if key in self.sent and key not in self._numbers:
            self._number(key)
            self.generation += 1
            if self.enabled:
                self.describe()

    def _on_removed(self, keys: list) -> None:
        """A forgotten signal stays ticked, and goes again when it comes back."""
        if any(self._numbers.pop(key, None) is not None for key in keys):
            self.generation += 1
            if self.enabled:
                self.describe()

    # --- sending ----------------------------------------------------------------------
    def _on_sample(self, key: str, t: float, value: float) -> None:
        number = self._numbers.get(key)
        if number is None or not math.isfinite(value):
            return
        self._queue.append((t, number, value))
        if not self._flush_timer.isActive():
            self._flush_timer.start()

    def _clock(self) -> float:
        """What to add to a hub time for ``time.monotonic``."""
        return time.monotonic() - self._bus_now()

    def flush(self) -> None:
        """Send what has arrived since last time, grouped by frame time."""
        if not self._queue:
            return
        offset = self._clock()
        frames: list[list] = []
        for t, number, value in self._queue:
            at = round(t + offset, 6)
            if frames and frames[-1][0] == at:
                frames[-1][1].append([number, _number(value)])
            else:
                frames.append([at, [[number, _number(value)]]])
        self._queue.clear()
        head = {"v": VERSION, "k": "values", "run": self.run, "gen": self.generation}
        for part in _parts(frames, head, "frames"):
            self._send(part)

    def describe(self) -> None:
        if not self.enabled:
            return
        signals = []
        for key in self.sent:
            number, series = self._numbers.get(key), self.hub.get(key)
            if number is None or series is None:
                continue
            entry = {"n": number, "group": series.group, "name": series.name}
            if series.unit:
                entry["unit"] = series.unit
            limits = self._limits(key)
            if limits is not None and limits[0] != limits[1]:
                entry["min"], entry["max"] = _number(limits[0]), _number(limits[1])
            if series.choices:
                entry["names"] = {str(k): v for k, v in series.choices.items()}
            signals.append(entry)
        head = {
            "v": VERSION,
            "k": "description",
            "run": self.run,
            "gen": self.generation,
            "clock": [round(time.monotonic(), 6), round(time.time(), 6)],
        }
        parts = list(_parts(signals, head, "signals")) or [{**head, "signals": []}]
        for index, part in enumerate(parts):
            part["part"], part["parts"] = index + 1, len(parts)
            self._send(part)

    def _send(self, message: dict) -> None:
        if self._socket is None:
            return
        data = json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode()
        try:
            self._socket.sendto(data, (GROUP, PORT))
        except OSError as exc:
            if not self._failed:  # once, not at every value
                self._failed = True
                self.said.emit(f"Sending signals failed: {exc}", True)
        else:
            self._failed = False


def _parts(items: list, head: dict, field: str):
    """The items in messages of no more than DATAGRAM_BYTES each."""
    room = DATAGRAM_BYTES - len(json.dumps({**head, field: [], "part": 99, "parts": 99}))
    chunk: list = []
    size = 0
    for item in items:
        length = len(json.dumps(item, separators=(",", ":"), ensure_ascii=False)) + 1
        if chunk and size + length > room:
            yield {**head, field: chunk}
            chunk, size = [], 0
        chunk.append(item)
        size += length
    if chunk:
        yield {**head, field: chunk}
