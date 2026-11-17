# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Putting firmware into a CANopen device, by the book that has one.

CiA 302-3 defines program download over SDO and almost nothing else does, so
that is what this speaks by default:

* ``0x1F50`` program data -- the image itself, written as a domain,
* ``0x1F51`` program control -- 0 stop, 1 start, 2 reset, 3 clear,
* ``0x1F56`` program software identification -- a checksum, where a device
  keeps one,
* ``0x1F57`` flash status identification -- how the last attempt went.

Each is an array with one sub-index per program, because a device may hold
more than one: the case the manual warns about is a controller taking two
images, one per processor.

**Most devices do not do it this way.**  Firmware download over CANopen is
very often a maker's own sequence of writes to objects of their own choosing.
Each part of the sequence is a hook in ``hooks/canopen.py``, given the node's
identity, so one workspace can hold the sequence for each product it talks to.
``Firmware`` asks the hooks first and falls back to CiA 302-3.

Nothing here touches Qt or the bus directly. It is handed something that can
read and write objects, which is what makes it testable without either.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

#: CiA 302-3. One sub-index per program, counted from 1.
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

#: Written in blocks so that there is something to report between them. The
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
    bus: the pane hands it a live node, a test hands it a dictionary. It is
    also what the firmware hooks are given.
    """

    def write(self, index: int, sub: int, value) -> None:
        raise NotImplementedError

    def read(self, index: int, sub: int):
        raise NotImplementedError

    def write_domain(self, index: int, sub: int, data: bytes, progress) -> None:
        """Write a block of bytes, calling ``progress(done, total)`` as it goes."""
        raise NotImplementedError

    @contextmanager
    def patient(self, seconds: float):
        """Wait up to ``seconds`` for each answer while this lasts."""
        yield


class NodeDevice(Device):
    """A live node, wearing the methods the sequence needs.

    Everything here runs on a worker thread: an SDO download of a firmware
    image takes minutes, and doing it on the GUI thread would freeze the window
    for all of them.
    """

    def __init__(self, node, block: bool = False) -> None:
        self.node = node
        self.block = block

    @contextmanager
    def patient(self, seconds: float):
        """A longer SDO timeout for as long as this lasts, then the old one back.

        Longer only: a timeout somebody has already set higher than this in the
        CANopen pane is left as it is.
        """
        sdo = self.node.sdo
        before = sdo.RESPONSE_TIMEOUT
        sdo.RESPONSE_TIMEOUT = max(before, seconds)
        try:
            yield
        finally:
            sdo.RESPONSE_TIMEOUT = before

    def write(self, index: int, sub: int, value) -> None:
        self.node.sdo[index][sub].raw = value

    def read(self, index: int, sub: int):
        """Through the EDS where it describes the object, as bytes where not.

        A device in its loader is the usual case of a node nobody loaded an
        EDS for, and what it is running is exactly what is wanted then.
        """
        try:
            entry = self.node.sdo[index]
        except KeyError:
            return self.node.sdo.upload(index, sub)
        if sub == 0 and hasattr(entry, "raw"):
            return entry.raw  # a plain variable, which has no sub-indices
        return entry[sub].raw

    def write_domain(self, index: int, sub: int, data: bytes, progress) -> None:
        # Opened as a file and written in blocks rather than assigned in one
        # go, which is the only way to have anything to report while it runs.
        try:
            sink = self.node.sdo.open(index, sub, "wb", size=len(data), block_transfer=self.block)
        except Exception as exc:
            if self.block:
                raise RuntimeError(
                    f"{exc} -- the device may not support block transfer; try Segmented"
                ) from exc
            raise
        with sink:
            for at in range(0, len(data), BLOCK):
                sink.write(data[at : at + BLOCK])
                progress(min(at + BLOCK, len(data)), len(data))


# --- CiA 302-3 ------------------------------------------------------------------------------
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
    from here on are the expected thing rather than a fault.
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

    Reported rather than checked. What a maker puts in here is a checksum
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
    a file with holes in it has no honest single answer. Filling the gaps
    would put invented bytes into somebody's flash, so this refuses instead.
    """
    if not image.segments:
        raise ValueError(f"{image.path} has nothing in it")
    if len(image.segments) > 1:
        raise ValueError(
            f"{image.path} is in {len(image.segments)} pieces with gaps between them, "
            "and a program download is one block of bytes. Supply the image as one "
            "contiguous file, or write the sequence your device wants as the "
            "firmware_steps hook in hooks/canopen.py."
        )
    return image.segments[0].data


# --- the hooks first, then the book ---------------------------------------------------------
class Firmware:
    """One node's download sequence: its hooks where they answer, CiA 302-3 where not.

    ``hooks`` is pycangui's ``Hooks``; ``identity`` is the node's, or None for
    one that has not said who it is -- a device sitting in its loader often
    has not. Every method runs on the worker thread.
    """

    def __init__(self, hooks, identity) -> None:
        self.hooks = hooks
        self.identity = identity

    def _ask(self, name: str, *args) -> Any:
        if self.hooks is None:
            return None
        return self.hooks.call("canopen", name, *args)

    def check(self, device: Device, image, program: int) -> str | None:
        """A reason not to download this image to this node, or None."""
        refused = self._ask("firmware_check", device, self.identity, image, program)
        return str(refused) if refused else None

    def enter(self, device: Device, program: int) -> None:
        if not self._ask("enter_bootloader", device, self.identity, program):
            enter_bootloader(device, program)

    def leave(self, device: Device, program: int) -> None:
        if not self._ask("exit_bootloader", device, self.identity, program):
            exit_bootloader(device, program)

    def steps(self, device: Device, image, program: int, progress) -> Iterable[Step]:
        """The hooks' sequence for this node, or CiA 302-3's. Asked before any
        step runs, so an image CiA 302-3 cannot take -- one in pieces -- is
        refused before anything is written."""
        own = self._ask("firmware_steps", device, self.identity, image, program, progress)
        if own is not None:
            return list(own)
        return list(steps(device, program, image_bytes(image), progress))

    def version(self, device: Device, program: int) -> str:
        own = self._ask("firmware_version", device, self.identity, program)
        return str(own) if own is not None else software_version(device)

    def status(self, device: Device, program: int) -> tuple[str, str]:
        """(software identification, flash status), each "" where not kept."""
        own = self._ask("flash_status", device, self.identity, program)
        if own is not None:
            return "", str(own)
        return identification(device, program), flash_status(device, program)

    def size(self, image) -> int:
        """How much is to be written, for the progress bar: the whole image,
        whatever shape the sequence gives it."""
        return sum(len(segment.data) for segment in image.segments)
