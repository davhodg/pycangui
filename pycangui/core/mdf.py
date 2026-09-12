# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Reading measurement files: MDF, and the MF4 that is its current version.

A CAN log holds frames.  An MDF holds *signals* -- decoded values against
time, as a measurement tool or a logger recorded them -- and that is a
different thing wearing a similar name.  A file of one sort opened by a tool
expecting the other looks broken when it is nothing of the kind, which is most
of why this module exists at all.

What it will not do is guess.  A file that holds only frame metadata and no
payload cannot yield signals, and saying so is more use than returning nothing
and letting somebody conclude their file is empty.

**asammdf may not be installed.**  The Windows installer bundles it, but a
``pip`` installation leaves it out by default -- it brings pandas with it, some
hundred megabytes, for a format many people never meet -- and fetches it when a
file needs it.  So everything here works out whether the library is present and
says what to do when it is not, and nothing imports it at start-up.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

#: What has to be installed, and roughly what it costs.  The number is what
#: lands on disc, measured rather than guessed: pandas is most of it.
PACKAGE = "asammdf"
SIZE_MB = 100
WHY = (
    "MDF and MF4 files are read by the asammdf library, which this "
    "installation of pycangui does not include: it brings pandas with it, "
    f"about {SIZE_MB} MB, for a format many people never open."
)

#: Suffixes that mean "this is a measurement file".  ``.dat`` is MDF 3, which
#: is still what a great deal of archived data is.
SUFFIXES = (".mf4", ".mdf", ".dat")
FILTER = "Measurement files (*.mf4 *.mdf *.dat);;All files (*)"

#: The channels ASAM uses for raw bus logging.  They are frame plumbing rather
#: than measurements -- the id, the length, the flags -- and a signals list
#: with three hundred of them in it is a signals list nobody can use.  Read on
#: request, never by default.
BUS_PREFIXES = ("CAN_", "LIN_", "FLX_", "ETH_")

#: asammdf hands back one of these per channel; only the numeric ones can be
#: plotted, and the rest are text or arrays that a time series cannot hold.
_TEXT_KINDS = ("S", "U", "V", "O")


class NotAvailableError(RuntimeError):
    """asammdf is not installed.  The message says what to do about it."""


@dataclass(frozen=True)
class ChannelInfo:
    """One channel, described without reading its samples.

    Listed before anything is loaded because a real file holds thousands --
    the one this was written against has 5,838 -- and reading them all to find
    out what is in there would be minutes and gigabytes to answer a question
    the header can answer.
    """

    name: str
    unit: str
    samples: int
    group: int
    #: Frame plumbing rather than a measurement.  Kept in the list so that a
    #: person who wants the ids can have them, and out of the way otherwise.
    bus_metadata: bool = False


@dataclass
class Series:
    """One signal read out of a file: what it is called, and its values."""

    name: str
    unit: str
    times: np.ndarray
    values: np.ndarray

    def __len__(self) -> int:
        return len(self.times)


@dataclass
class Summary:
    """What a file is, before deciding what to do with it."""

    path: Path
    version: str = ""
    groups: int = 0
    channels: int = 0
    samples: int = 0
    #: True where the file holds raw frames -- payload bytes and all -- and so
    #: could be replayed as traffic.  Frame *metadata* without the payload does
    #: not count, because nothing can be replayed from it.
    has_frames: bool = False
    #: What the writing tool left behind.  Worth showing: an export that went
    #: wrong usually says so here, and it is the first place to look when a
    #: file is not what somebody expected.
    comment: str = ""
    attachments: list[str] = field(default_factory=list)


def available() -> bool:
    """Whether MDF files can be read at all in this installation."""
    try:
        import asammdf  # noqa: F401
    except ImportError:
        return False
    return True


def _mdf(path: str | Path):
    try:
        from asammdf import MDF
    except ImportError as exc:  # pragma: no cover - exercised by the UI path
        raise NotAvailableError(WHY) from exc
    return MDF(Path(path))


def looks_like_mdf(path: str | Path) -> bool:
    """Whether the first bytes say MDF, whatever the extension says.

    Checked before the library is asked for, so that picking the wrong file is
    a sentence rather than sixty megabytes and then a sentence.
    """
    try:
        with Path(path).open("rb") as f:
            return f.read(8) in (b"MDF     ", b"UnFinMF ")
    except OSError:
        return False


def unfinalised(path: str | Path) -> bool:
    """Whether the writer never closed the file.

    A logger that lost power, or one that writes this way by design.  Most
    tools refuse an unfinalised file outright; asammdf reads one, so this is
    worth saying rather than being the thing that silently differs.
    """
    try:
        with Path(path).open("rb") as f:
            return f.read(8) == b"UnFinMF "
    except OSError:
        return False


def summarise(path: str | Path) -> Summary:
    """What is in a file, without reading any samples."""
    path = Path(path)
    out = Summary(path=path)
    with _mdf(path) as mdf:
        out.version = mdf.version
        out.groups = len(mdf.groups)
        out.channels = sum(len(g.channels) for g in mdf.groups)
        out.samples = sum(g.channel_group.cycles_nr for g in mdf.groups)
        out.comment = (mdf.header.comment or "").strip()
        try:
            out.attachments = [
                str(getattr(a, "file_name", "") or getattr(a, "name", "")) for a in mdf.attachments
            ]
        except Exception:
            # MDF 3 has no attachments and raises rather than saying none, and
            # a summary is not worth failing over a field the format lacks.
            out.attachments = []
        # The payload, not the header fields.  A file with CAN_DataFrame.ID and
        # no CAN_DataFrame.DataBytes has the shape of a bus log and none of the
        # substance: there is nothing in it to replay.
        names = {c.name for g in mdf.groups for c in g.channels}
        out.has_frames = "CAN_DataFrame.DataBytes" in names or "CAN_DataFrame" in names
    return out


def channels(path: str | Path) -> list[ChannelInfo]:
    """Every channel in a file, described but not read.

    Empty channel groups are left out.  A real export is full of them -- the
    file this was written against has 382 of 882 -- because a message that did
    not occur in the exported window still gets a group, and offering somebody
    four hundred signals with nothing in them is offering them nothing.
    """
    out: list[ChannelInfo] = []
    with _mdf(path) as mdf:
        for index, group in enumerate(mdf.groups):
            cycles = group.channel_group.cycles_nr
            if not cycles:
                continue
            for channel in group.channels:
                if channel.name in ("t", "time", "timestamps"):
                    continue
                out.append(
                    ChannelInfo(
                        name=channel.name,
                        unit=getattr(channel, "unit", "") or "",
                        samples=cycles,
                        group=index,
                        bus_metadata=channel.name.startswith(BUS_PREFIXES),
                    )
                )
    return out


def read(
    path: str | Path,
    names: list[str] | None = None,
    include_bus_metadata: bool = False,
) -> list[Series]:
    """Read signals out of a measurement file.

    ``names`` reads only those, which is the usual way: a file with thousands
    of channels is not something to load whole on the chance that six of them
    were wanted.  Without it, everything numeric is read.

    A channel whose samples are text, or an array per sample, is left out.  A
    time series holds numbers, and there is no honest way to plot a string.
    """
    wanted = set(names) if names is not None else None
    out: list[Series] = []
    with _mdf(path) as mdf:
        for signal in mdf.iter_channels():
            if wanted is not None and signal.name not in wanted:
                continue
            if wanted is None and not include_bus_metadata:
                if signal.name.startswith(BUS_PREFIXES):
                    continue
            samples = np.asarray(signal.samples)
            if samples.dtype.kind in _TEXT_KINDS or samples.ndim != 1:
                # Text, or a whole record per sample.  A bus log written by
                # python-can is entirely the latter: one composed channel
                # holding id, length and payload together, which is a frame
                # rather than a measurement and has no business on a plot.
                continue
            if samples.size == 0:
                # A channel whose group claims cycles can still read back with
                # nothing in it -- real exports do this.  An empty series is
                # not a signal, and one plotted is a curve with no points and
                # a name in the legend suggesting otherwise.
                continue
            out.append(
                Series(
                    name=signal.name,
                    unit=signal.unit or "",
                    times=np.asarray(signal.timestamps, dtype=float),
                    values=samples.astype(float, copy=False),
                )
            )
    return out
