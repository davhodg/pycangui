"""Firmware images: Intel HEX, Motorola S-record and raw binary.

Reading and writing is bincopy's job.  What is here is the part bincopy
cannot decide: which format a file is, what its address is, and what to do
when it does not have one.

A hex or S-record file carries the address of every byte in it.  A raw binary
does not -- it is bytes and nothing else -- so the address has to be supplied
before it can be sent anywhere, and an image loaded without one says so
rather than guessing at zero.

Gaps are left as they are found.  A file with holes in it is transferred a
segment at a time, each with its own RequestDownload; padding the holes would
mean writing bytes the file never contained, over whatever the ECU had there.
A bootloader that insists on one contiguous block should be given a
contiguous file.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import bincopy

#: Suffixes that mean "this file has no addresses in it".
BINARY_SUFFIXES = {".bin", ".raw", ".img", ".rom", ".dat"}

#: S-record address widths, by the suffix that names them.  S19 is 16-bit
#: addresses, S28 24-bit, S37 32-bit; the suffix is the usual way of saying so.
SREC_SUFFIXES = {".s19": 16, ".s28": 24, ".s37": 32, ".srec": 32, ".mot": 32, ".sre": 32}
IHEX_SUFFIXES = {".hex", ".ihex", ".ihx", ".i32", ".a90"}

#: For the file dialogs.  One "any of them" entry first, because guessing the
#: format from the contents is what the reader does anyway.
READ_FILTER = (
    "Firmware images (*.hex *.ihex *.ihx *.s19 *.s28 *.s37 *.srec *.mot *.bin *.raw *.img);;"
    "Intel HEX (*.hex *.ihex *.ihx);;"
    "Motorola S-record (*.s19 *.s28 *.s37 *.srec *.mot);;"
    "Raw binary (*.bin *.raw *.img);;"
    "All files (*)"
)
WRITE_FILTER = (
    "Intel HEX (*.hex);;Motorola S-record (*.s19 *.s28 *.s37);;Raw binary (*.bin);;All files (*)"
)


class ImageError(Exception):
    """The file could not be read, or could be read but not placed."""


@dataclass(frozen=True)
class Segment:
    """One run of contiguous bytes at a known address."""

    address: int
    data: bytes

    def __len__(self) -> int:
        return len(self.data)


@dataclass(frozen=True)
class Image:
    """What a firmware file turned out to contain."""

    path: str
    format: str  # "Intel HEX", "Motorola S-record", "raw binary", ...
    segments: tuple[Segment, ...]

    @property
    def size(self) -> int:
        return sum(len(s) for s in self.segments)

    @property
    def address(self) -> int:
        return self.segments[0].address if self.segments else 0

    def summary(self) -> str:
        """One line: what was read, how much of it, and where it goes."""
        if not self.segments:
            return f"{Path(self.path).name}: {self.format}, empty"
        where = ", ".join(f"{s.address:08X}+{len(s)}" for s in self.segments[:4])
        if len(self.segments) > 4:
            where += f", and {len(self.segments) - 4} more"
        segments = "1 segment" if len(self.segments) == 1 else f"{len(self.segments)} segments"
        return f"{Path(self.path).name}: {self.format}, {self.size} bytes, {segments} [{where}]"


def looks_binary(path: str) -> bool:
    """Whether choosing this file will need an address typed in.

    Answered from the name, because the pane has to enable or disable the
    address box the moment a file is picked; reading settles it properly.
    """
    return Path(path).suffix.lower() in BINARY_SUFFIXES


def _sniff(raw: bytes) -> str | None:
    for line in raw.splitlines():
        text = line.strip()
        if not text:
            continue
        if text.startswith(b":"):
            return "Intel HEX"
        if text[:1] in (b"S", b"s") and text[1:2].isdigit():
            return "Motorola S-record"
        return None
    return None


def read(path: str, address: int | None = None) -> Image:
    """Read a firmware file, whatever format it is in.

    `address` is where a raw binary starts.  It is required for one, ignored
    for a file that carries its own addresses -- there the file is right and a
    typed-in number would only be a way to get it wrong.
    """
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise ImageError(str(exc)) from exc
    if not raw:
        raise ImageError("the file is empty")

    binfile = bincopy.BinFile()
    fmt = _sniff(raw)
    if fmt is not None:
        try:
            binfile.add(raw.decode("ascii", "strict"))
        except (UnicodeDecodeError, bincopy.Error, ValueError) as exc:
            raise ImageError(f"not readable as {fmt}: {exc}") from exc
    else:
        if address is None:
            raise ImageError(
                "this file is raw binary -- it carries no addresses, so the "
                "start address has to be supplied"
            )
        fmt = "raw binary"
        binfile.add_binary(raw, address=address)

    segments = tuple(Segment(s.address, bytes(s.data)) for s in binfile.segments)
    return Image(path=path, format=fmt, segments=segments)


def _srec_width(suffix: str, address: int, size: int) -> int:
    if (width := SREC_SUFFIXES.get(suffix)) is not None and width != 32:
        return width
    top = address + size
    return 16 if top <= 0x1_0000 else 24 if top <= 0x100_0000 else 32


def write(path: str, address: int, data: bytes) -> str:
    """Write bytes read back from an ECU, in the format the name asks for.

    Exactly what came off the bus, at the address it was asked for: nothing
    padded, nothing cropped, nothing filled in.  Returns the format used, for
    the log.
    """
    binfile = bincopy.BinFile()
    binfile.add_binary(data, address=address)
    suffix = Path(path).suffix.lower()
    try:
        if suffix in IHEX_SUFFIXES:
            fmt, text = "Intel HEX", binfile.as_ihex()
        elif suffix in SREC_SUFFIXES:
            width = _srec_width(suffix, address, len(data))
            fmt, text = "Motorola S-record", binfile.as_srec(address_length_bits=width)
        else:
            Path(path).write_bytes(data)
            return "raw binary"
        Path(path).write_text(text, encoding="ascii")
    except (OSError, bincopy.Error, ValueError) as exc:
        raise ImageError(str(exc)) from exc
    return fmt
