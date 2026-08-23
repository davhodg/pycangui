"""Signal hub: named numeric time series from any source, for display and plotting.

Sources (DBC decoder, CANopen PDOs, SDO polling, user scripts) call
``push(group, name, t, value)``; consumers (Signals pane, Plot pane) read
``series()`` / ``latest()`` and listen to ``added``.  History is kept here, so
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

    def keys(self) -> list[str]:
        return list(self._series)

    def get(self, key: str) -> SignalSeries | None:
        return self._series.get(key)

    def clear(self) -> None:
        for s in self._series.values():
            s.times.clear()
            s.values.clear()
        self.updated.emit()
