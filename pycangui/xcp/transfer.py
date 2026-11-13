# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""What a flash of a file over XCP or CCP will do, worked out before it starts.

A file says which bytes go where. The A2L says which stretches of the slave's
memory may be programmed, and in which order they are erased and written.
Putting the two together is this module's whole job, and it does it without
the bus, so what is about to happen can be shown -- and tested -- before
anything is sent.

The bytes of the file inside an area are written there. Bytes in no area are
left out and said to be left out, unless they are asked for as well: that is
how an area the A2L does not list is flashed anyway. An area the file has
nothing for is not erased, since erasing it would only take away what the
slave already had there.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from pycangui.xcp.a2l import Area

#: How many bytes are sent between one look at the Cancel button and the next,
#: and between one move of the progress bar and the next.
CHUNK = 0x400


@dataclass(frozen=True)
class Run:
    """Contiguous bytes of the file, all in one area."""

    area: str
    address: int
    data: bytes
    extension: int = 0

    @property
    def end(self) -> int:
        return self.address + len(self.data)


@dataclass(frozen=True)
class Plan:
    #: Erased first, in this order: the areas the file has bytes for.
    clears: tuple[Area, ...]
    #: Then written, in this order, an area's runs one after another.
    runs: tuple[Run, ...]
    #: Bytes of the file in no area, and so not written: (address, size).
    outside: tuple[tuple[int, int], ...] = ()
    #: Areas the file has nothing for, left as they are.
    untouched: tuple[Area, ...] = ()

    @property
    def size(self) -> int:
        return sum(len(r.data) for r in self.runs)


def from_segments(segments) -> list[Area]:
    """An area for each segment of a file: flashing it as it is, A2L or none."""
    return [
        Area(f"segment {n + 1}", s.address, len(s.data), n, n, source="file")
        for n, s in enumerate(segments)
    ]


def make(segments, areas: list[Area], outside_too: bool = False) -> Plan:
    """Which of the file's bytes go to which area, and in what order.

    ``segments`` are an image's segments: an address and bytes each. With no
    areas at all the file is flashed as it is, a segment to an area.
    ``outside_too`` writes the bytes that are in no area as well, each run
    of them its own area, after the rest.
    """
    if not areas:
        areas = from_segments(segments)
    runs: list[Run] = []
    used: list[Area] = []
    for area in sorted(areas, key=lambda a: a.program_order):
        mine = [
            Run(area.name, max(s.address, area.address), _cut(s, area), area.extension)
            for s in segments
            if s.address < area.end and s.address + len(s.data) > area.address
        ]
        if mine:
            runs += sorted(mine, key=lambda r: r.address)
            used.append(area)
    outside = _outside(segments, areas)
    if outside_too and outside:
        cleared = max((a.clear_order for a in areas), default=-1) + 1
        programmed = max((a.program_order for a in areas), default=-1) + 1
        for n, (address, size) in enumerate(outside):
            extra = Area(
                f"outside {n + 1}", address, size, cleared + n, programmed + n, source="file"
            )
            for s in segments:
                if s.address < extra.end and s.address + len(s.data) > extra.address:
                    runs.append(Run(extra.name, max(s.address, address), _cut(s, extra)))
            used.append(extra)
        outside = []
    clears = tuple(sorted(used, key=lambda a: a.clear_order))
    untouched = tuple(a for a in areas if a not in used)
    return Plan(clears, tuple(runs), tuple(outside), untouched)


def _cut(segment, area: Area) -> bytes:
    start = max(segment.address, area.address)
    end = min(segment.address + len(segment.data), area.end)
    return bytes(segment.data[start - segment.address : end - segment.address])


def _outside(segments, areas: list[Area]) -> list[tuple[int, int]]:
    """The stretches of the file that no area covers."""
    found = []
    for s in segments:
        pieces = [(s.address, s.address + len(s.data))]
        for area in areas:
            kept = []
            for start, end in pieces:
                if end <= area.address or start >= area.end:
                    kept.append((start, end))
                    continue
                if start < area.address:
                    kept.append((start, area.address))
                if end > area.end:
                    kept.append((area.end, end))
            pieces = kept
        found += [(start, end - start) for start, end in pieces if end > start]
    return sorted(found)


def chunks(run: Run, size: int = CHUNK):
    """A run in pieces small enough to cancel between."""
    for at in range(0, len(run.data), size):
        yield replace(run, address=run.address + at, data=run.data[at : at + size])


def bytes_at(segments, address: int, size: int) -> bytes | None:
    """The file's bytes for a stretch of memory, or None where it has a gap
    in it: a checksum of a stretch the file does not wholly fill means
    nothing to compare with."""
    for s in segments:
        if s.address <= address and address + size <= s.address + len(s.data):
            return bytes(s.data[address - s.address : address - s.address + size])
    return None
