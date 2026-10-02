# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Signal hub: named numeric time series from any source, for display and plotting.

Sources (DBC decoder, CANopen PDOs, SDO polling, user scripts) call
``push(group, name, t, value)``; consumers (Signals pane, Plot pane) read
``series()`` / ``latest()`` and listen to ``added``. History is kept here, so
a plot opened later still shows what happened before.

GUI thread only: sources on other threads must hand over via a Qt signal
first (that is already how bus frames and PDO updates arrive).
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass, field

import numpy as np
from PySide6.QtCore import QObject, Signal

MAX_SAMPLES = 200_000  # per signal; trimmed back to this from 1.5x

#: How far back a signal's rate is measured: the last second of its samples.
RATE_WINDOW_S = 1.0


@dataclass
class SignalSeries:
    group: str
    name: str
    unit: str = ""
    times: list[float] = field(default_factory=list)
    values: list[float] = field(default_factory=list)
    #: Kept as each sample arrives, which is one addition and two comparisons,
    #: and so for every sample ever received: trimming the history to its
    #: newest samples does not make a signal's count, or its extremes, smaller.
    count: int = 0
    minimum: float | None = None
    maximum: float | None = None

    @property
    def key(self) -> str:
        return f"{self.group}/{self.name}"

    def note(self, value: float) -> None:
        """Count a sample, and widen the extremes to hold it."""
        self.count += 1
        if self.minimum is None or value < self.minimum:
            self.minimum = value
        if self.maximum is None or value > self.maximum:
            self.maximum = value

    def forget_statistics(self) -> None:
        self.count, self.minimum, self.maximum = 0, None, None

    def rate(self) -> float | None:
        """Samples a second, over the last second of samples there is.

        Of the signal's own time stamps, not of the clock: it is how fast the
        signal was arriving when it last arrived. Worked out when asked for,
        so it costs nothing while nobody is looking. None with fewer than two
        samples to measure between.
        """
        if len(self.times) < 2:
            return None
        first = bisect_left(self.times, self.times[-1] - RATE_WINDOW_S)
        first = min(first, len(self.times) - 2)
        span = self.times[-1] - self.times[first]
        return (len(self.times) - 1 - first) / span if span > 0 else None

    @property
    def latest(self) -> float | None:
        return self.values[-1] if self.values else None

    def window(self, t_from: float) -> tuple[np.ndarray, np.ndarray]:
        i = bisect_left(self.times, t_from)
        return np.asarray(self.times[i:], dtype=float), np.asarray(self.values[i:], dtype=float)


class SignalHub(QObject):
    added = Signal(str)  # key of a signal seen for the first time
    updated = Signal()  # at least one value changed since the last emit (batched by caller)
    removed = Signal(list)  # keys of signals forgotten, for lists and plots to drop

    def __init__(self) -> None:
        super().__init__()
        self._series: dict[str, SignalSeries] = {}

    def push(self, group: str, name: str, t: float, value: float, unit: str = "") -> None:
        key = f"{group}/{name}"
        s = self._series.get(key)
        if s is None:
            s = self._series[key] = SignalSeries(group, name, unit)
            self.added.emit(key)
        elif unit and not s.unit:
            s.unit = unit
        s.times.append(t)
        s.values.append(float(value))
        s.note(s.values[-1])
        if len(s.times) > MAX_SAMPLES * 1.5:
            del s.times[:-MAX_SAMPLES]
            del s.values[:-MAX_SAMPLES]

    def push_many(
        self, group: str, t: float, values: dict[str, float], units: dict | None = None
    ) -> None:
        units = units or {}
        for name, value in values.items():
            if isinstance(value, int | float) and not isinstance(value, bool):
                self.push(group, name, t, value, units.get(name, ""))
        self.updated.emit()

    def set_series(self, group: str, name: str, times, values, unit: str = "") -> str:
        """Put a whole series in at once, as read from a file.

        Not ``push`` in a loop, and the difference is the trimming: a live
        signal is trimmed to the newest MAX_SAMPLES because the old values
        stop mattering, and an imported one trimmed the same way would lose
        its *beginning* -- which for something being analysed is the half
        somebody is usually looking for. A file is finite, so it is kept
        whole.
        """
        key = f"{group}/{name}"
        series = self._series.get(key)
        if series is None:
            series = self._series[key] = SignalSeries(group, name, unit)
            new = True
        else:
            new = False
        series.unit = unit or series.unit
        series.times = list(times)
        series.values = [float(v) for v in values]
        series.count = len(series.values)
        series.minimum = min(series.values, default=None)
        series.maximum = max(series.values, default=None)
        if new:
            self.added.emit(key)
        self.updated.emit()
        return key

    def groups(self) -> list[str]:
        """The sources signals have come from, in the order first seen."""
        return list(dict.fromkeys(s.group for s in self._series.values()))

    def forget_group(self, group: str) -> int:
        """Drop everything from one source. Returns how many series went."""
        return self.forget_groups([group])

    def forget_groups(self, groups) -> int:
        """Drop everything from these sources. Returns how many series went.

        A database removed, an imported file finished with: a signals list
        that only ever grows is one nobody can find anything in, and a
        signal still listed after its database has gone reads as one that is
        still being decoded. ``removed`` says which, so the list and the plot
        drop them too.
        """
        wanted = set(groups)
        going = [k for k, s in self._series.items() if s.group in wanted]
        for key in going:
            del self._series[key]
        if going:
            self.removed.emit(going)
            self.updated.emit()
        return len(going)

    def keys(self) -> list[str]:
        return list(self._series)

    def get(self, key: str) -> SignalSeries | None:
        return self._series.get(key)

    def stored(self) -> tuple[int, int]:
        """How many signals are held, and how many samples between them."""
        return len(self._series), sum(len(s.times) for s in self._series.values())

    def clear(self) -> None:
        for s in self._series.values():
            s.times.clear()
            s.values.clear()
            s.forget_statistics()
        self.updated.emit()
