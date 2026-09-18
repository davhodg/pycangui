# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Where a pane's values come from, and where a change goes.

A pane is bound to a *source*, never to a node, and that is the one decision
here worth arguing about. The same group of objects is wanted against three
different things: a live controller, a DCF somebody was sent, and the defaults
in an EDS. Bound to a node, each of those becomes its own feature with its own
screen, and comparing one against another becomes a fourth. Bound to a source,
they are one mechanism with three inputs -- and offline editing, comparison and
live configuration all fall out of it.

Retrofitting this is not a refactor of the seam, it is a rewrite of every pane
that was written before it, which is why it is here before there is a single
pane to bind.

Every source is asynchronous, including the ones that answer instantly. A live
node reads over SDO on a worker thread and answers later; a file answers now.
Making the file pretend to be slow is a great deal less trouble than making the
pane able to cope with both, and it means the pane has exactly one path
through it rather than a fast one that only ever gets exercised in tests.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

from pycangui.canopen import NodeIdentity, eds_extras, eds_identity
from pycangui.canopen.display import Display, from_variable, with_overrides


class Source(QObject):
    """One place values are read from and written to."""

    #: index, sub, raw value, error text or None. The only way an answer
    #: arrives, whichever kind of source produced it.
    value = Signal(int, int, object, object)
    #: The source itself changed -- a different node identified, a file
    #: reloaded -- so anything showing its name should ask again.
    changed = Signal()

    #: What to call this source on screen.
    label = ""
    #: Whether writing means anything. An EDS's defaults are readable and not
    #: writable, and a pane bound to one should say so rather than fail.
    writable = False
    #: Whether reading it again can tell you anything new. A controller
    #: changes while you watch it; a file on disk does not, so polling one
    #: would be work with a guaranteed answer of "the same".
    live = False

    def display(self, index: int, sub: int) -> Display:
        """Name, unit, scaling, choices and limits for one object."""
        return Display()

    def request(self, index: int, sub: int) -> None:
        """Ask for a value. The answer comes back on ``value``."""

    def write(self, index: int, sub: int, raw: Any) -> None:
        """Set a value. What the source made of it comes back on ``value``."""

    def objects(self) -> list[tuple[int, int, str, str]]:
        """Everything this source knows of, as (index, sub, name, access).

        For picking from, so it is empty where the source does not know rather
        than wrong: a node with no EDS can still be read object by object, it
        just cannot be browsed. An EDS, on the other hand, knows the whole
        dictionary without a bus being present at all -- which is what lets a
        pane be built at a desk.
        """
        return []


def _listed(od) -> list[tuple[int, int, str, str]]:
    """Flatten an object dictionary, leaving out the rows that are not objects."""
    from pycangui.canopen.manager import od_entries

    if od is None:
        return []
    out = []
    for index, sub, var, name in od_entries(od):
        if var is None:
            continue  # the record itself; its sub-indices follow
        access = getattr(var, "access_type", "") or ""
        out.append((index, sub or 0, name, access))
    return out


class NodeSource(Source):
    """A live controller, read and written over SDO."""

    writable = True
    live = True

    def __init__(self, manager, node_id: int) -> None:
        super().__init__()
        self.manager = manager
        self.node_id = node_id
        self.label = f"Node {node_id}"
        manager.sdo_result.connect(self._on_result)
        manager.identified.connect(self._on_identified)

    def _on_result(self, node_id: int, index: int, sub: int, value: Any, error: Any) -> None:
        # Every pane bound to every node hears every read, so the filter is
        # the whole of what makes two custom_panes on two nodes independent.
        if node_id == self.node_id:
            self.value.emit(index, sub, value, error)

    def _on_identified(self, identity: NodeIdentity) -> None:
        if getattr(identity, "node_id", None) == self.node_id:
            self.changed.emit()

    def display(self, index: int, sub: int) -> Display:
        return self.manager.display(self.node_id, index, sub)

    def request(self, index: int, sub: int) -> None:
        self.manager.sdo_read(self.node_id, index, sub)

    def write(self, index: int, sub: int, raw: Any) -> None:
        self.manager.sdo_write(self.node_id, index, sub, str(raw))

    def objects(self) -> list[tuple[int, int, str, str]]:
        node = self.manager.node(self.node_id)
        return _listed(getattr(node, "object_dictionary", None) if node else None)


class FileSource(Source):
    """A DCF or an EDS: the values in the file, or the defaults where it has none.

    Writing changes the file in memory and nothing else until it is saved,
    which is the difference between editing a configuration at a desk and
    configuring a machine. ``save`` writes it back through the original text
    so the comments -- which for a real EDS are where the units, the scaling
    and the descriptions live -- survive the round trip.
    """

    #: True when there are edits the file on disk does not have yet, False
    #: once there are none. Sent on every write and every save.
    modified = Signal(bool)

    def __init__(self, path: str | Path, hooks=None) -> None:
        super().__init__()
        self.path = Path(path)
        self.label = self.path.name
        self.hooks = hooks
        self._extras = eds_extras(self.path)
        self._identity = self._identity_of(self.path)
        self._values: dict[tuple[int, int], Any] = {}
        #: What ``_values`` was when it last went to disk.
        self._saved: dict[tuple[int, int], Any] = {}
        self._od = None
        self.writable = True
        self.reload()

    @staticmethod
    def _identity_of(path: Path) -> NodeIdentity | None:
        vendor, product, revision = eds_identity(path)
        if vendor is None and product is None:
            return None
        return NodeIdentity(0, vendor_id=vendor, product_code=product, revision=revision)

    def reload(self) -> None:
        import canopen

        try:
            self._od = canopen.import_od(str(self.path))
        except Exception:
            self._od = None  # not a file this reader understands
        self._values.clear()
        self._saved.clear()
        self.changed.emit()

    # --- what the file says ------------------------------------------------------------
    def _variable(self, index: int, sub: int):
        if self._od is None:
            return None
        try:
            return self._od.get_variable(index, sub)
        except Exception:
            return None

    def display(self, index: int, sub: int) -> Display:
        base = from_variable(self._variable(index, sub))
        overrides = None
        if self.hooks is not None:
            overrides = self.hooks.call(
                "canopen",
                "object_display",
                index,
                sub,
                self._extras.get((index, sub), {}),
                self._identity,
            )
        return with_overrides(base, overrides if isinstance(overrides, dict) else None)

    def request(self, index: int, sub: int) -> None:
        if (index, sub) in self._values:
            self._answer(index, sub, self._values[(index, sub)], None)
            return
        var = self._variable(index, sub)
        if var is None:
            self._answer(index, sub, None, "not in this file")
            return
        # A DCF's ParameterValue where it has one, the EDS default otherwise.
        # ``canopen`` puts both on the variable, the value winning.
        raw = getattr(var, "value", None)
        if raw is None:
            raw = getattr(var, "default", None)
        self._answer(index, sub, raw, None if raw is not None else "no value in this file")

    def write(self, index: int, sub: int, raw: Any) -> None:
        if self._variable(index, sub) is None:
            self._answer(index, sub, None, "not in this file")
            return
        self._values[(index, sub)] = raw
        self._answer(index, sub, raw, None)
        self.modified.emit(self.unsaved)

    def objects(self) -> list[tuple[int, int, str, str]]:
        return _listed(self._od)

    def _answer(self, index: int, sub: int, raw: Any, error: str | None) -> None:
        # Through the event loop, so a file behaves the way a node does and the
        # pane has one path through it rather than two.
        QTimer.singleShot(0, lambda: self.value.emit(index, sub, raw, error))

    # --- and back out again -------------------------------------------------------------
    @property
    def edited(self) -> dict[tuple[int, int], Any]:
        """Every value changed since the file was opened."""
        return dict(self._values)

    @property
    def unsaved(self) -> bool:
        """Whether there are edits the file on disk does not have."""
        return self._values != self._saved

    @property
    def needs_new_name(self) -> bool:
        """Saving over anything but a DCF would turn it into one.

        An EDS is what a device ships with, and the next person to open it
        expects the defaults rather than this afternoon's values.
        """
        return self.path.suffix.lower() != ".dcf"

    def save(self, path: str | Path | None = None, node_id: int | None = None) -> Path:
        """Write a DCF, through the original text so nothing in it is lost.

        Saved under another name, this source becomes that file: the edits
        that follow belong to the copy, and saving again goes there too.
        """
        from pycangui.canopen.dcf import values_from, write_dcf

        target = Path(path) if path is not None else self.path
        source = self.path.read_text(encoding="utf-8-sig", errors="replace")
        target.write_text(
            write_dcf(source, values_from(self._values), node_id), encoding="utf-8", newline=""
        )
        if target.resolve() != self.path.resolve():
            self.path = target
            self.label = target.name
            self.changed.emit()
        self._saved = dict(self._values)
        self.modified.emit(False)
        return target
