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


@dataclass
class SignalSeries:
    group: str
    name: str
    unit: str = ""
    times: list[float] = field(default_factory=list)
    values: list[float] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.group}/{self.name}"

    @property
    def latest(self) -> float | None:
        return self.values[-1] if self.values else None

    def window(self, t_from: float) -> tuple[np.ndarray, np.ndarray]:
        i = bisect_left(self.times, t_from)
        return np.asarray(self.times[i:], dtype=float), np.asarray(self.values[i:], dtype=float)


class SignalHub(QObject):
    added = Signal(str)  # key of a signal seen for the first time
    updated = Signal()  # at least one value changed since the last emit (batched by caller)

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
        if new:
            self.added.emit(key)
        self.updated.emit()
        return key

    def groups(self) -> list[str]:
        """The sources signals have come from, in the order first seen."""
        return list(dict.fromkeys(s.group for s in self._series.values()))

    def forget_group(self, group: str) -> int:
        """Drop everything from one source. Returns how many series went.

        An imported file is a thing somebody finishes with, and a signals list
        that only ever grows is one nobody can find anything in.
        """
        going = [k for k, s in self._series.items() if s.group == group]
        for key in going:
            del self._series[key]
        if going:
            self.updated.emit()
        return len(going)

    def keys(self) -> list[str]:
        return list(self._series)

    def get(self, key: str) -> SignalSeries | None:
        return self._series.get(key)

    def clear(self) -> None:
        for s in self._series.values():
            s.times.clear()
            s.values.clear()
        self.updated.emit()
