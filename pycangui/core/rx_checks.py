# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Checking the counter and checksum of messages a device sends.

The other side of :mod:`pycangui.core.tx_fields`: the same configuration --
a counter and a checksum, at a byte position or in a database signal -- and
the same arithmetic, applied to frames as they arrive instead of as they are
sent. A device whose counter repeats or skips, or whose checksum is not what
the configured algorithm gives, is the fault being looked for; a receiver in
the vehicle would reject those frames, and nothing else on screen says so.

The checking is plain Python with no window in it, so it is tested on its
own. The window runs it on every batch of frames before the traces see them,
so a failing frame can be marked where it is shown.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from pycangui.core import tx_fields as tx
from pycangui.core.bus import Frame
from pycangui.core.tx_fields import Checksum, Counter

#: The workspace setting the checks are kept in.
SETTING = "rx_checks"


@dataclass
class Check:
    """What one received message is checked for."""

    can_id: int
    extended: bool = False
    counter: Counter | None = None
    checksum: Checksum | None = None
    #: The database message, where the fields are its signals; also its name.
    message: str = ""

    @property
    def key(self) -> tuple[int, bool]:
        return self.can_id, self.extended

    @property
    def label(self) -> str:
        ident = f"{self.can_id:08X}" if self.extended else f"{self.can_id:03X}"
        return f"{self.message} ({ident})" if self.message else ident

    def to_dict(self) -> dict:
        return {
            "id": self.can_id,
            "ext": self.extended,
            "message": self.message,
            "counter": tx.counter_to_dict(self.counter),
            "checksum": tx.checksum_to_dict(self.checksum),
        }

    @classmethod
    def from_dict(cls, raw: dict) -> Check:
        return cls(
            can_id=int(raw["id"]),
            extended=bool(raw.get("ext", False)),
            counter=tx.counter_from_dict(raw.get("counter")),
            checksum=tx.checksum_from_dict(raw.get("checksum")),
            message=str(raw.get("message", "") or ""),
        )


@dataclass
class Tally:
    """How one message has fared since its check was set."""

    counter_faults: int = 0
    checksum_faults: int = 0
    #: Faults in a row, ended by a frame that is right.
    run: int = 0


@dataclass
class Outcome:
    """What a frame was found to be."""

    check: Check
    #: Why the counter is wrong, or "".
    counter: str = ""
    #: Why the checksum is wrong, or "".
    checksum: str = ""
    #: What was checked, as the transmit list says it: ``count b0.lo, sum8 b7``.
    said: str = ""
    #: This frame started a run of faults, or ended one (the run's length).
    started: bool = False
    ended: int = 0

    @property
    def fault(self) -> str:
        return "; ".join(part for part in (self.counter, self.checksum) if part)


@dataclass
class Checker:
    """Checks every received frame of the messages it has been given checks for.

    ``message_by_name`` finds a database message, for a check whose fields are
    signals; ``hook`` works out a maker's own checksum, as the transmit side's
    ``transmit.checksum`` hook does, or returns None for the configured one.
    """

    message_by_name: Callable[[str], object | None] = lambda _name: None
    hook: Callable[[str, int, bytes], int | None] | None = None
    checks: dict[tuple[int, bool], Check] = field(default_factory=dict)
    tallies: dict[tuple[int, bool], Tally] = field(default_factory=dict)
    #: The last count seen, per channel and message: a frame lost on one bus
    #: is not a count skipped on another.
    _last: dict[tuple[str, int, bool], int] = field(default_factory=dict)
    #: Each check as the transmit list says it, worked out on its first frame.
    _said: dict[tuple[int, bool], str] = field(default_factory=dict)

    # --- which messages ---------------------------------------------------------
    def set(self, check: Check) -> None:
        """Check this message from now on, starting its count afresh."""
        if check.counter is None and check.checksum is None:
            self.remove(check.key)
            return
        self.checks[check.key] = check
        self.tallies[check.key] = Tally()
        self._forget(check.key)

    def remove(self, key: tuple[int, bool]) -> None:
        self.checks.pop(key, None)
        self.tallies.pop(key, None)
        self._forget(key)

    def _forget(self, key: tuple[int, bool]) -> None:
        self._said.pop(key, None)
        for last in [k for k in self._last if k[1:] == key]:
            del self._last[last]

    def to_list(self) -> list[dict]:
        return [check.to_dict() for check in self.checks.values()]

    def load(self, raw: list[dict]) -> list[str]:
        """Take the checks a workspace kept; returns why any were left out."""
        self.checks.clear()
        self.tallies.clear()
        self._last.clear()
        self._said.clear()
        problems = []
        for entry in raw or []:
            try:
                self.set(Check.from_dict(entry))
            except (KeyError, TypeError, ValueError) as exc:
                problems.append(f"a received-message check was left out: {exc}")
        return problems

    # --- the frames ---------------------------------------------------------------
    def check(self, frame: Frame) -> Outcome | None:
        """The verdict on one frame, or None for a frame nobody asked about.

        Only frames received: what pycangui sends is checked where it is made.
        """
        if not frame.rx or frame.error:
            return None
        check = self.checks.get((frame.can_id, frame.extended))
        if check is None:
            return None
        outcome = Outcome(check, said=self.said(check))
        named = [f for f in (check.counter, check.checksum) if f is not None and f.signal]
        message = values = raw = None
        if named:
            message = self.message_by_name(check.message)
            if message is None:
                outcome.checksum = f"{check.message} is not in a loaded database"
                self._tally(check, outcome)
                return outcome
            try:
                values = message.decode(frame.data, decode_choices=False, allow_truncated=True)
                raw = message.decode(
                    frame.data, decode_choices=False, scaling=False, allow_truncated=True
                )
            except Exception as exc:  # cantools raises several kinds for a malformed frame
                outcome.checksum = f"does not decode: {exc}"
                self._tally(check, outcome)
                return outcome
        bits = {s.name: s.length for s in message.signals} if message is not None else {}
        try:
            if check.counter is not None:
                if check.counter.signal:
                    count = int(raw.get(check.counter.signal, 0))
                    modulus = check.counter.modulus(bits.get(check.counter.signal))
                else:
                    count = check.counter.at.read(frame.data)
                    modulus = check.counter.modulus()
                outcome.counter = self._count(check, frame, count, modulus)
            if check.checksum is not None:
                if check.checksum.signal:
                    outcome.checksum = self._signal_checksum(
                        check, frame, message, values, raw, bits
                    )
                else:
                    outcome.checksum = self._placed_checksum(check, frame)
        except tx.FieldError as exc:  # a field past the end of a short frame
            outcome.checksum = outcome.checksum or str(exc)
        self._tally(check, outcome)
        return outcome

    def said(self, check: Check) -> str:
        """The check in the transmit list's words, a signal by its position."""
        if (said := self._said.get(check.key)) is None:
            message = self.message_by_name(check.message) if check.message else None
            signals = getattr(message, "signals", None) or []
            places = {s.name: (s.start, s.length) for s in signals}
            said = self._said[check.key] = tx.describe(check.counter, check.checksum, places)
        return said

    def _placed_checksum(self, check: Check, frame: Frame) -> str:
        """The checksum at a byte position: worked out with its own bits at zero."""
        at = check.checksum.at
        blank = bytearray(frame.data)
        at.write(blank, 0)
        expected = self._hooked(check, frame, bytes(blank))
        if expected is None:
            expected = check.checksum.value(bytes(blank))
        expected &= at.modulus - 1
        got = at.read(frame.data)
        return "" if got == expected else _checksum_said(got, expected, at.bits)

    def _signal_checksum(self, check, frame, message, values, raw, bits) -> str:
        """The checksum in a signal: the frame encoded again as the transmit
        side would make it, and that frame's checksum compared with this one's."""
        name = check.checksum.signal
        width = bits.get(name)
        try:
            rebuilt = tx.apply_signals(
                lambda v: message.encode(v, padding=False, strict=False),
                values,
                None,
                check.checksum,
                hook=lambda blank: self._hooked(check, frame, blank),
                checksum_bits=width,
            )
            expected = int(
                message.decode(
                    rebuilt, decode_choices=False, scaling=False, allow_truncated=True
                ).get(name, 0)
            )
        except Exception as exc:  # a value out of range, a renamed signal
            return f"checksum cannot be worked out: {exc}"
        got = int(raw.get(name, 0))
        return "" if got == expected else _checksum_said(got, expected, width or 8)

    def _hooked(self, check: Check, frame: Frame, blank: bytes) -> int | None:
        if self.hook is None:
            return None
        return self.hook(check.message or check.label, frame.can_id, blank)

    def _count(self, check: Check, frame: Frame, count: int, modulus: int) -> str:
        """What is wrong with this count, after the last one; "" when nothing is."""
        key = (frame.channel, frame.can_id, frame.extended)
        last = self._last.get(key)
        self._last[key] = count
        if last is None:
            return ""  # the first one seen: the count starts here
        step = check.counter.step % modulus or modulus
        expected = (last + step) % modulus
        if count == expected:
            return ""
        if count == last:
            return f"count {count} repeated"
        moved = (count - last) % modulus
        if moved % step == 0 and moved // step > 1:
            missing = moved // step - 1
            return f"count {count} after {last}: {missing} missing"
        return f"count {count} after {last}, expected {expected}"

    def _tally(self, check: Check, outcome: Outcome) -> None:
        tally = self.tallies.setdefault(check.key, Tally())
        if outcome.counter:
            tally.counter_faults += 1
        if outcome.checksum:
            tally.checksum_faults += 1
        if outcome.fault:
            outcome.started = tally.run == 0
            tally.run += 1
        elif tally.run:
            outcome.ended = tally.run
            tally.run = 0


def _checksum_said(got: int, expected: int, bits: int) -> str:
    digits = max(1, (bits + 3) // 4)
    return f"checksum {got:0{digits}X}, expected {expected:0{digits}X}"
