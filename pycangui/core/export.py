# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Decoded signals out to a spreadsheet.

Recording writes raw CAN, which is the right thing for a recording and the
wrong thing for analysis: whoever opens it has to repeat the decode, with the
same databases, to get back what pycangui already had.  This writes the values.

**One time column per signal, with a blank column between signals.**  The
alternative is a single time column and a value per signal, which sounds tidier
and is a lie: signals do not arrive together.  A DBC signal lands when its
message does, a CANopen value when its PDO does, a polled object when the SDO
comes back -- so a shared timeline can only be built by interpolating, or by
holding the last value, or by inventing a grid.  All three put numbers in the
file that were never on the bus.

Giving each signal its own pair of columns means every number in the file was
measured, at the time written beside it.  The blank column between pairs is
what stops a spreadsheet reading two signals as one series when you select a
block and ask for a chart.

    Time (s),DBC Engine/Speed (rpm),,Time (s),XCP/current (A)
    0.010000,1500,,0.012000,12.4
    0.020000,1520,,0.022000,12.6
    0.030000,1490,,,

Signals run out at different points, and a signal that has finished leaves its
cells empty rather than repeating its last value, for the same reason.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Iterator, Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pycangui.core.signals import SignalSeries

TIME_HEADER = "Time (s)"

#: Six decimals is a microsecond, which is finer than any CAN timestamp, and
#: it keeps the column reading as a column rather than drifting into
#: exponents for the first few samples.
TIME_FORMAT = "{:.6f}"

#: Ten significant figures: enough for a 32-bit counter exactly, and short
#: enough that a tenth does not arrive as 0.10000000000000001.
VALUE_FORMAT = "{:.10g}"


def label(series: SignalSeries) -> str:
    """ "DBC Engine/Speed (rpm)", or without the brackets when there is no unit.

    The full key rather than the name alone: two databases can each have a
    signal called Speed, and a column heading that cannot say which is worse
    than a long one.
    """
    return f"{series.key} ({series.unit})" if series.unit else series.key


def header(series: Sequence[SignalSeries]) -> list[str]:
    cells: list[str] = []
    for index, one in enumerate(series):
        if index:
            cells.append("")  # the gap
        cells += [TIME_HEADER, label(one)]
    return cells


def rows(series: Sequence[SignalSeries]) -> Iterator[list[str]]:
    """One row per sample index, as long as the longest signal."""
    longest = max((len(one.times) for one in series), default=0)
    for i in range(longest):
        row: list[str] = []
        for index, one in enumerate(series):
            if index:
                row.append("")
            if i < len(one.times):
                row += [TIME_FORMAT.format(one.times[i]), VALUE_FORMAT.format(one.values[i])]
            else:
                row += ["", ""]  # finished: left empty rather than held
        yield row


def with_samples(series: Iterable[SignalSeries]) -> list[SignalSeries]:
    """The ones worth a column.

    A signal the hub has heard of but never had a value for -- a DBC message
    that was never seen -- would otherwise contribute two empty columns and a
    heading, which is noise in a file somebody is about to plot.
    """
    return [one for one in series if one.times]


def write_csv(path: str, series: Iterable[SignalSeries]) -> tuple[int, int]:
    """Write the signals to `path`.  Returns (signals written, rows written)."""
    chosen = with_samples(series)
    if not chosen:
        return (0, 0)
    written = 0
    # newline="" because csv does its own line endings; without it Windows
    # turns every one into a blank line.
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header(chosen))
        for row in rows(chosen):
            writer.writerow(row)
            written += 1
    return (len(chosen), written)
