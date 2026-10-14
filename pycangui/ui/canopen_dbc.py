# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Save as DBC, from the CANopen pane: the network, one node, or one file.

The pane is what knows which nodes and files there are and what each is
called; ``canopen.to_dbc`` makes the database. This is what is between
them: who goes in, which configuration each is taken from, the file
dialog, and saying what came of it.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pycangui.canopen import to_dbc
from pycangui.ui import folders, messages

if TYPE_CHECKING:
    from pycangui.custom_panes.source import FileSource
    from pycangui.ui.canopen_view import CanopenView

#: Said where what is in the database stops short of the node, and why.
NOT_READ = (
    "{name}: its {which} configuration has not been read from it, so its {which}s are "
    "left out. Read PDO config on the node reads both."
)
NOT_A_CONFIGURATION = (
    "{name}: an EDS, which says what a device can do, not how a node is set up: "
    "left out. Save DCF... from the node, and save that as DBC."
)


def node_device(view: CanopenView, node_id: int, taken: set[str], notes: list[str]):
    """A node, by what was read from it: the PDOs, emergency COB-ID and
    heartbeat it is set up with, and nothing its EDS merely suggests.

    Read from it means read while it was there -- it can have gone since,
    and what it said still stands.
    """
    manager = view.manager
    item = view._node_item(node_id)
    shown = item.text(1) if item is not None else ""
    name = _name(shown or f"Node{node_id}", taken)
    node = manager.node(node_id)
    device = to_dbc.Device(
        name, node_id, display=lambda index, sub: manager.display(node_id, index, sub)
    )
    tpdos_read, rpdos_read = manager.read_from_node(node_id)
    if node is not None:
        if tpdos_read:
            device.tpdos = [m for m in node.tpdo.map.values() if m.cob_id is not None]
        if rpdos_read:
            device.rpdos = [m for m in node.rpdo.map.values() if m.cob_id is not None]
    for read, which in ((tpdos_read, "TPDO"), (rpdos_read, "RPDO")):
        if not read:
            notes.append(NOT_READ.format(name=name, which=which))
    # A heartbeat is there if one has been heard, or the node says it sends one.
    produced = manager.heartbeat_times.get(node_id)
    device.heartbeat = node_id in manager.last_heartbeat or bool(produced)
    device.emcy_cob_id = to_dbc.emcy_id(manager.emcy_cob_ids.get(node_id))
    return device


def is_configuration(source: FileSource) -> bool:
    """A DCF of a node: one with a node id. An EDS has none, and describes a
    device rather than a node -- what it ships with, not how it is set up."""
    od = source.object_dictionary
    return od is not None and 1 <= int(getattr(od, "node_id", None) or 0) <= 127


def file_device(source: FileSource, taken: set[str], notes: list[str]):
    """A DCF: the values it holds, and none of the EDS defaults it falls back on."""
    name = _name(source.path.stem, taken)
    if not is_configuration(source):
        taken.discard(name)
        notes.append(NOT_A_CONFIGURATION.format(name=source.path.name))
        return None
    od = source.object_dictionary
    node_id = int(od.node_id)
    device = to_dbc.Device(name, node_id, display=source.display)
    try:
        device.tpdos, device.rpdos = to_dbc.from_dictionary(od, node_id)
    except Exception as exc:  # an odd file
        notes.append(f"{name}: its PDOs could not be read ({exc})")
    device.heartbeat = bool(to_dbc.configured_value(od, 0x1017))
    device.emcy_cob_id = to_dbc.emcy_id(to_dbc.configured_value(od, 0x1014))
    return device


def _name(text: str, taken: set[str]) -> str:
    return to_dbc._unique(to_dbc.dbc_name(text, "Node"), taken)


def save(view: CanopenView, node_ids: list[int], files: list[FileSource], network: bool) -> None:
    """Ask where, make the database, write it, and say what came of it."""
    taken: set[str] = {to_dbc.TESTER}
    notes: list[str] = []
    devices = [node_device(view, node_id, taken, notes) for node_id in node_ids]
    devices += [d for d in (file_device(source, taken, notes) for source in files) if d]
    if not devices or (not network and not any(d.tpdos or d.rpdos for d in devices)):
        # Asked for one node or file and there is nothing of it to describe:
        # a box saying why, rather than a file with nothing in it.
        why = "\n\n".join(notes) or "There is no node or DCF in the list to describe."
        messages.warning(view, "Nothing to save", why)
        return
    suggested = "canopen_network.dbc" if network else f"{devices[0].name}.dbc"
    path = folders.save_file(
        view,
        view.ctx,
        folders.DBC,
        "Save as DBC",
        "CAN database (*.dbc);;All files (*)",
        view.ctx.user_dir,
        suggested=suggested,
    )
    if not path:
        return
    made = to_dbc.build(devices, network=network)
    made.notes[:0] = notes
    try:
        differences = to_dbc.write(made, path)
    except OSError as exc:
        view.ctx.warn(f"Save as DBC: {path} could not be written: {exc}")
        messages.warning(view, "Not saved", f"{path} could not be written:\n\n{exc}")
        return
    count = len(made.database.messages)
    view.ctx.log(
        f"Saved {count} messages for {len(devices)} node(s) to {Path(path).name}. "
        "File > Load DBC... to use it."
    )
    for note in made.notes:
        view.ctx.warn(f"Save as DBC: {note}")
    if differences:
        # The file reads back as something other than what was meant: said,
        # since a database that decodes wrongly is worse than none.
        view.ctx.warn(
            f"Save as DBC: {Path(path).name} reads back differently from what was "
            "written, in " + "; ".join(differences)
        )
