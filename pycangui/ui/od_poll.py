# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Polling objects ticked in the CANopen object dictionary, into Signals and Plot.

An object a node does not map to a PDO can only be followed by asking for it
again and again. A custom pane does that for the objects on it; this does it
for objects ticked in the dictionary's *Poll* column, without building a pane
first -- the dictionary is where an object is found, so it is where it is
picked.

The ticks are kept per device, as bookmarks are, so the next controller of
the same kind polls the same objects. Polling itself is turned on by hand
each time and not remembered: a tool that starts sending SDO requests as it
opens is a tool that surprises somebody on a bus that was quiet.

Every node with ticked objects is polled while it is on, each by a Poller of
its own, so a slow node does not hold back a quick one. The same rules as a
custom pane's polling hold: the rate asked for is a ceiling and the one
achieved is shown, a round only starts once the last is answered, a pause of
pycangui's own traffic holds it, and *Stop all repeating traffic* stops it.
A node that is stopped or has gone quiet is left alone until it is back,
since every request to it would only fail or time out.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, Signal

from pycangui.custom_panes.polling import DEFAULT_HZ, MAX_HZ, MIN_HZ, Poller

#: Where the ticked objects are kept, per device.
SETTING = "canopen.poll"
#: Requests a node may have waiting before the oldest are forgotten: one that
#: never answers would otherwise grow its list for as long as polling ran.
MAX_WAITING = 8


class DictionaryPoll(QObject):
    """The objects ticked for polling, and the pollers that read them."""

    #: Requested rate, slowest rate achieved over the nodes being polled, and
    #: whether it is running and whether paused: for the label beside the box.
    rate = Signal(float, float, bool, bool)
    running_changed = Signal(bool)

    def __init__(
        self,
        manager,
        settings,
        device_key: Callable[[int], str],
        signals=None,
        now: Callable[[], float] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.manager = manager
        self._settings = settings
        self._device_key = device_key
        self._signals = signals
        self._now = now
        stored = settings.get(SETTING, {}) if settings is not None else {}
        self._lists: dict[str, set[tuple[int, int]]] = {
            str(key): {(int(i), int(s)) for i, s in objects}
            for key, objects in (stored.items() if isinstance(stored, dict) else [])
        }
        self._pollers: dict[int, Poller] = {}
        #: Per node and object, oldest first: True where polling asked for it.
        #: An answer belongs to the oldest request for its object, so a read
        #: by hand meanwhile does not finish a round it was not part of.
        self._asked: dict[tuple[int, int, int], list[bool]] = {}
        self._running = False
        self._paused = False
        self._hz = DEFAULT_HZ
        for changed in (manager.identified, manager.node_back, manager.node_lost):
            changed.connect(lambda *_a: self.sync())
        manager.node_seen.connect(self._on_node_seen)

    # --- what is ticked ---------------------------------------------------------------
    def polled(self, node_id: int) -> set[tuple[int, int]]:
        return set(self._lists.get(self._device_key(node_id), set()))

    def set_polled(self, node_id: int, where: tuple[int, int], on: bool) -> None:
        key = self._device_key(node_id)
        objects = self._lists.setdefault(key, set())
        objects.add(where) if on else objects.discard(where)
        if not objects:
            del self._lists[key]  # an empty list is the same as no list
        if self._settings is not None:
            self._settings.set(
                SETTING, {k: sorted([i, s] for i, s in v) for k, v in self._lists.items()}
            )
        self.sync()

    # --- on and off -------------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._running

    @property
    def hz(self) -> float:
        return self._hz

    def start(self) -> None:
        if not self._running:
            self._running = True
            self.running_changed.emit(True)
        self.sync()

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        for poller in self._pollers.values():
            poller.stop()
            poller.deleteLater()
        self._pollers.clear()
        self._asked.clear()
        self.running_changed.emit(False)
        self._announce()

    def set_rate(self, hz: float) -> None:
        self._hz = max(MIN_HZ, min(float(hz), MAX_HZ))
        for poller in self._pollers.values():
            poller.set_rate(self._hz)
        self._announce()

    def set_paused(self, on: bool) -> None:
        self._paused = on
        for poller in self._pollers.values():
            poller.set_paused(on)
        self._announce()

    def nodes(self) -> list[int]:
        """The nodes being polled now."""
        return sorted(self._pollers)

    # --- one poller per node ----------------------------------------------------------
    def _on_node_seen(self, node_id: int, _state: str) -> None:
        # Every heartbeat, so only the cheap question unless the answer changed:
        # whether this node is polled, and whether it should be.
        if self._running and (node_id in self._pollers) != self._wanted(node_id):
            self.sync()

    def _wanted(self, node_id: int) -> bool:
        return bool(self.polled(node_id)) and not self.manager.is_stopped(node_id)

    def sync(self) -> None:
        """Poll the nodes that have objects ticked and can answer; no others."""
        if not self._running:
            return
        lost = self.manager.lost_nodes
        wanted = {n for n in self.manager.nodes() if self._wanted(n) and n not in lost}
        for node_id in list(self._pollers):
            if node_id not in wanted:
                self._pollers.pop(node_id).stop()
        for node_id in wanted:
            poller = self._pollers.get(node_id)
            if poller is None:
                poller = self._pollers[node_id] = Poller(self)
                poller.read.connect(lambda i, s, n=node_id: self._read(n, i, s))
                poller.rate.connect(lambda *_a: self._announce())
                poller.set_rate(self._hz)
                poller.set_paused(self._paused)
            poller.set_objects(sorted(self.polled(node_id)))
            if not poller.running:
                poller.start(self._hz)
        self._announce()

    def _read(self, node_id: int, index: int, sub: int) -> None:
        waiting = self._asked.setdefault((node_id, index, sub), [])
        waiting.append(True)
        del waiting[:-MAX_WAITING]
        self.manager.sdo_read(node_id, index, sub)

    def note_read(self, node_id: int, index: int, sub: int) -> None:
        """A read somebody asked for by hand, so its answer is not taken for polling's."""
        if (node_id, index, sub) in self._asked:
            self._asked[(node_id, index, sub)].append(False)

    def answer(self, node_id: int, index: int, sub: int, value: Any, error: Any) -> bool:
        """An SDO answer arrived. True when polling asked for it."""
        waiting = self._asked.get((node_id, index, sub))
        if not waiting or not waiting.pop(0):
            return False
        poller = self._pollers.get(node_id)
        if poller is not None:
            poller.answered(index, sub)
        if not error:
            self._to_signals(node_id, index, sub, value)
        return True

    def _to_signals(self, node_id: int, index: int, sub: int, value: Any) -> None:
        """A polled number is a signal, so it plots and exports like one."""
        if self._signals is None or self._now is None:
            return
        if not isinstance(value, int | float) or isinstance(value, bool):
            return  # a string or a block of bytes is not a trace
        display = self.manager.display(node_id, index, sub)
        shown = display.physical(value) if display.scaled else value
        name = display.name or f"{index:04X}:{sub:02X}"
        # The group a custom pane on the same node uses, so an object polled
        # from both is one signal rather than two of the same name.
        self._signals.push(f"Node {node_id}", name, self._now(), float(shown), display.unit)
        self._signals.updated.emit()

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def achieved(self) -> float:
        """The slowest node's rounds a second: what every polled object gets at least."""
        rates = [p.achieved for p in self._pollers.values() if p.objects]
        return min(rates) if rates else 0.0

    def _announce(self) -> None:
        self.rate.emit(self._hz, self.achieved, self._running, self._paused)
