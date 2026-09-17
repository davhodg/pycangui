# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Putting firmware into a CANopen device, by the book that has one.

CiA 302-3 defines program download over SDO and almost nothing else does, so
that is what this speaks:

* ``0x1F50`` program data -- the image itself, written as a domain,
* ``0x1F51`` program control -- 0 stop, 1 start, 2 reset, 3 clear,
* ``0x1F56`` program software identification -- a checksum, where a device
  keeps one,
* ``0x1F57`` flash status identification -- how the last attempt went.

Each is an array with one sub-index per program, because a device may hold
more than one: the case the manual warns about is a controller taking two
images, one per processor.

**Most devices do not do it this way.**  Firmware download over CANopen is
very often a maker's own sequence of writes to objects of their own choosing,
and no amount of standards reading will produce it.  That is exactly why this
is a plugin: installing it puts a copy in the workspace, so edit ``steps`` in
that copy to be what the device actually wants.  Nothing else has to change,
and the pane, the progress and the reporting go on working.

Nothing here touches Qt or the bus directly.  It is handed something that can
read and write objects, which is what makes it testable without either.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass

#: CiA 302-3.  One sub-index per program, counted from 1.
PROGRAM_DATA = 0x1F50
PROGRAM_CONTROL = 0x1F51
PROGRAM_IDENTIFICATION = 0x1F56
FLASH_STATUS = 0x1F57
#: CiA 301 manufacturer software version: a string, and the one thing nearly
#: every device keeps about what it is running.
SOFTWARE_VERSION = 0x100A

#: What may be written to program control, and what it means.
STOP, START, RESET, CLEAR = 0, 1, 2, 3
CONTROL_NAMES = {STOP: "stop", START: "start", RESET: "reset", CLEAR: "clear"}

#: Written in blocks so that there is something to report between them.  The
#: figure is a compromise: small enough that a slow bus still moves the bar,
#: large enough that the reporting is not most of the work.
BLOCK = 1024


@dataclass
class Step:
    """One thing to do to the device, and what to say while it is happening."""

    what: str
    run: Callable[[], None]


class Device:
    """Whatever can read and write this node's objects.

    A thin seam, and the reason the rest of this file can be tested without a
    bus: the pane hands it a live node, a test hands it a dictionary.
    """

    def write(self, index: int, sub: int, value) -> None:
        raise NotImplementedError

    def read(self, index: int, sub: int):
        raise NotImplementedError

    def write_domain(self, index: int, sub: int, data: bytes, progress) -> None:
        """Write a block of bytes, calling ``progress(done, total)`` as it goes."""
        raise NotImplementedError


def steps(device: Device, program: int, data: bytes, progress) -> Iterator[Step]:
    """The whole sequence, as separate things that can be reported one by one.

    Yielded rather than run, so that the caller can say what is happening
    before each one and stop after any of them -- a download that fails half
    way should say which half.
    """
    yield Step(
        "Stopping the program",
        lambda: device.write(PROGRAM_CONTROL, program, STOP),
    )
    yield Step(
        "Clearing the program",
        lambda: device.write(PROGRAM_CONTROL, program, CLEAR),
    )
    yield Step(
        f"Writing {len(data)} bytes",
        lambda: device.write_domain(PROGRAM_DATA, program, data, progress),
    )
    yield Step(
        "Starting the program",
        lambda: device.write(PROGRAM_CONTROL, program, START),
    )


def enter_bootloader(device: Device, program: int) -> None:
    """Stop the application, which is what puts a CiA 302-3 device in its loader.

    While it is stopped the device answers very little, and slowly: timeouts
    from here on are the expected thing rather than a fault.  A device with a
    way of its own -- a write to a maker's object, then a reset -- goes here.
    """
    device.write(PROGRAM_CONTROL, program, STOP)


def exit_bootloader(device: Device, program: int) -> None:
    """Start the application again, which is how the loader is left."""
    device.write(PROGRAM_CONTROL, program, START)


def software_version(device: Device) -> str:
    """What the device says its software is, where it says anything.

    A string on the wire, and read as bytes where the EDS does not describe
    the object, so the padding some devices leave on the end is taken off.
    """
    try:
        value = device.read(SOFTWARE_VERSION, 0)
    except Exception:
        return ""
    if isinstance(value, bytes | bytearray):
        value = bytes(value).decode("latin-1")
    return str(value).strip("\x00 \r\n")


def identification(device: Device, program: int) -> str:
    """What the device says it is running, where it says anything.

    Reported rather than checked.  What a maker puts in here is a checksum
    computed their way, and comparing it against one worked out from the file
    would be inventing agreement that was never established.
    """
    try:
        value = device.read(PROGRAM_IDENTIFICATION, program)
    except Exception:
        return ""
    return f"0x{int(value):08X}" if isinstance(value, int) else str(value)


def flash_status(device: Device, program: int) -> str:
    """How the last attempt went, where the device keeps a note of it."""
    try:
        value = device.read(FLASH_STATUS, program)
    except Exception:
        return ""
    return f"0x{int(value):08X}" if isinstance(value, int) else str(value)


def image_bytes(image) -> bytes:
    """The contiguous run to write, or a reason it cannot be written.

    A CiA 302-3 download is one domain: it is the bytes, not the addresses, so
    a file with holes in it has no honest single answer.  Filling the gaps
    would put invented bytes into somebody's flash, so this refuses instead.
    """
    if not image.segments:
        raise ValueError(f"{image.path} has nothing in it")
    if len(image.segments) > 1:
        raise ValueError(
            f"{image.path} is in {len(image.segments)} pieces with gaps between them, "
            "and a program download is one block of bytes. Supply the image as one "
            "contiguous file, or write the sequence your device wants into the plugin."
        )
    return image.segments[0].data
