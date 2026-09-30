# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Every SDO transfer pycangui makes, for the CANopen log.

Not only the ones somebody asked for in the OD tree: identifying a node,
checking an EDS against it, reading its PDO mapping, a DCF, a custom pane
polling, a plugin -- all of it is SDO, and when a node misbehaves the
question is usually which of those asked it what, and what it said back.

Caught by wrapping ``upload`` and ``download`` on each node's own SDO client,
which every read and write goes through, expedited or segmented. On the
client rather than the library's class, so nothing else in the process using
``canopen`` is logged with it. A transfer opened as a file -- a firmware
download's block transfer -- is not seen here; it has its own progress.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from pycangui.canopen import abort_reason

#: The most data bytes written out in one line; a DOMAIN can be kilobytes.
SHOWN_BYTES = 16


@dataclass(frozen=True)
class SdoRecord:
    """One transfer: who, which object, which way, what, and how it went."""

    at: float  # the bus clock, as the trace's Time column
    node_id: int
    write: bool
    index: int
    sub: int
    name: str
    data: bytes | None  # what was read or written; None when it failed
    error: str | None
    seconds: float

    def text(self) -> str:
        """What happened, for the log to put after the time and the node."""
        way = "write" if self.write else "read "
        where = f"{self.index:04X}:{self.sub:02X}" + (f" {self.name}" if self.name else "")
        took = f"{self.seconds * 1000:.0f} ms"
        if self.error is not None:
            return f"{way} {where}  FAILED: {self.error}  {took}"
        arrow = "<-" if self.write else "->"
        return f"{way} {where} {arrow} {shown(self.data)}  {took}"


def shown(data: bytes) -> str:
    """Bytes as hex, with the number they make when they are one: 37 02 = 0x0237."""
    if not data:
        return "(nothing)"
    text = data[:SHOWN_BYTES].hex(" ").upper()
    if len(data) > SHOWN_BYTES:
        return f"{text} ... ({len(data)} bytes)"
    if len(data) <= 4:
        value = int.from_bytes(data, "little")
        return f"{text} = 0x{value:0{2 * len(data)}X} ({value})"
    return f"{text} ({len(data)} bytes)"


def reason(exc: Exception) -> str:
    """An abort as its code and meaning; anything else, a timeout say, as it is."""
    import canopen

    if isinstance(exc, canopen.SdoAbortedError):
        meaning = abort_reason(exc.code)
        return f"abort 0x{exc.code:08X}" + (f", {meaning}" if meaning else "")
    return f"{type(exc).__name__}: {exc}"


def watch(
    sdo,
    node_id: int,
    report: Callable[[SdoRecord], None],
    now: Callable[[], float],
    name_of: Callable[[int, int], str],
) -> None:
    """Report every upload and download this SDO client makes. Once per client.

    ``report`` is called on whichever thread made the transfer, and has to be
    safe to call from any -- a Qt signal's emit is.
    """
    if getattr(sdo, "_pycangui_logged", False):
        return
    upload, download = sdo.upload, sdo.download

    def logged(write: bool, index: int, sub: int, call, data: bytes | None = None):
        at, started = now(), time.perf_counter()

        def record(said: bytes | None, error: str | None) -> SdoRecord:
            took = time.perf_counter() - started
            return SdoRecord(at, node_id, write, index, sub, name_of(index, sub), said, error, took)

        try:
            answer = call()
        except Exception as exc:
            report(record(None, reason(exc)))
            raise
        report(record(bytes(data if write else answer or b""), None))
        return answer

    def logged_upload(index, subindex, *args, **kwargs):
        return logged(False, index, subindex, lambda: upload(index, subindex, *args, **kwargs))

    def logged_download(index, subindex, data, *args, **kwargs):
        return logged(
            True, index, subindex, lambda: download(index, subindex, data, *args, **kwargs), data
        )

    sdo.upload = logged_upload
    sdo.download = logged_download
    sdo._pycangui_logged = True
