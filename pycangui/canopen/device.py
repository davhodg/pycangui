# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""A CANopen node as a device: SDO for its objects, NMT for its state."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pycangui.core.device import Device


class CanopenDevice(Device):
    """One node, by its node ID, whether or not it has been heard from yet."""

    #: The two a drive is worked in; Stopped is not offered, since SDO stops too.
    states = ("PRE-OPERATIONAL", "OPERATIONAL")

    def __init__(self, manager, node_id: int) -> None:
        super().__init__()
        self.manager = manager
        self.key = node_id
        self.label = f"Node {node_id}"
        self._last_state = self.state()
        # Bound methods: the manager outlives anything holding a device, and
        # these are kept for the window's life anyway (see Devices.device).
        for signal in (manager.node_seen, manager.node_lost, manager.node_back):
            signal.connect(self._on_heard)
        manager.eds_loaded.connect(self._on_changed)
        manager.pdo_config.connect(self._on_changed)

    def _on_heard(self, node_id: int, *_rest) -> None:
        if node_id != self.key:
            return
        now = self.state()
        if now != self._last_state:  # a heartbeat a second says nothing new
            self._last_state = now
            self.state_changed.emit()

    def _on_changed(self, node_id: int, *_rest) -> None:
        if node_id == self.key:
            self.changed.emit()

    def _node(self):
        node = self.manager.node(self.key)
        if node is None:
            raise RuntimeError(f"{self.label} is not on the bus")
        return node

    def available(self) -> bool:
        return self.manager.node(self.key) is not None

    def background(self, job: Callable[[], Any], done: Callable[[Any, Any], None]) -> None:
        self.manager.background(job, done)

    def read(self, index: int, sub: int) -> bytes:
        # By index, not through the EDS: a drive nobody has a file for reads
        # the same as one that has.
        return bytes(self._node().sdo.upload(index, sub))

    def write(self, index: int, sub: int, data: bytes) -> None:
        self._node().sdo.download(index, sub, bytes(data))

    def has(self, index: int, sub: int = 0) -> bool | None:
        node = self.manager.node(self.key)
        od = getattr(node, "object_dictionary", None)
        if not od:
            return None  # no EDS: nothing to go by, not "nothing there"
        if index not in od:
            return False
        obj = od[index]
        if not hasattr(obj, "subindices"):
            return True  # a plain variable
        try:
            obj[sub]
        except KeyError:
            return False
        return True

    def state(self) -> tuple[str | None, bool]:
        return self.manager.nmt_states.get(self.key), self.key in self.manager.lost_nodes

    def set_state(self, name: str) -> None:
        self.manager.nmt(self.key, name)

    def receives(self) -> list[tuple[int, int, str]]:
        return [
            (entry.index, entry.subindex, f"RPDO{config.number}")
            for config in self.manager.pdo_configs(self.key)
            if config.direction == "RPDO" and config.enabled
            for entry in config.entries
        ]
