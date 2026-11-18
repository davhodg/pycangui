# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""A UDS ECU as a source for a custom pane: its data identifiers, by number.

The key is the DID. What one means -- its name, its description, how its bytes
read as a value and how a value goes back into bytes -- is the ``did_*`` hooks
in ``hooks/uds.py``, the same ones the UDS pane uses, so a pane and the pane
agree. Where a hook says nothing, a value is its bytes in hex, and a value
typed in is taken as bytes the same way the UDS pane takes them.

Read and written through the UDS pane's open connection: a pane over UDS is
bound to whichever ECU that pane is talking to.
"""

from __future__ import annotations

from typing import Any

from pycangui.canopen.display import Display
from pycangui.custom_panes.model import UDS
from pycangui.custom_panes.source import Key, Source


class UdsSource(Source):
    """The ECU the UDS pane has open."""

    protocol = UDS
    live = True
    writable = True

    def __init__(self, manager) -> None:
        super().__init__()
        self.manager = manager
        self.label = "UDS ECU"
        manager.did_answered.connect(self._on_answered)
        manager.opened.connect(self._on_opened)

    def _on_opened(self, _open: bool) -> None:
        self.changed.emit()

    def _on_answered(self, did: int, value: Any, error: Any) -> None:
        self.value.emit(did, value, error)

    def _name(self, did: int) -> str:
        label = self.manager.did_label(did)  # "F190 (VIN)", or "F190"
        return label.split(" (", 1)[1].rstrip(")") if " (" in label else f"DID {did:04X}"

    def display(self, key: Key) -> Display:
        did = int(key)
        return Display(name=self._name(did), description=self.manager.did_description(did))

    def request(self, key: Key) -> None:
        self.manager.read_did_value(int(key))

    def write(self, key: Key, raw: Any) -> None:
        self.manager.write_did_value(int(key), str(raw))

    def objects(self) -> list[tuple[Key, str, str]]:
        """The identifiers ``did_choices`` names -- the ones the UDS pane offers.

        Whether an ECU lets one be written is not something it says until it
        is tried, so each is offered as written, and a refusal shows against
        the field.
        """
        return [(did, name, "rw") for did, name in sorted(self.manager.did_choices().items())]
