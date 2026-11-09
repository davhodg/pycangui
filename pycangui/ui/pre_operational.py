# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Before writing what a node may refuse while Operational: offer Pre-operational.

CiA 301 leaves it to the device which states it takes a change of parameters
or PDOs in, and many take them only in Pre-operational. Writing a DCF or a set
of PDOs into an Operational node then fails object by object, with nothing to
say why. Asked first, the answer is one click.

Only a node whose heartbeat says Operational is asked about. A node with no
heartbeat has no state pycangui can see, and guessing one would be worse than
saying so, which the reports of what was refused do.
"""

from __future__ import annotations

from canopen.nmt import NMT_STATES
from PySide6.QtWidgets import QMessageBox, QWidget

from pycangui.canopen.manager import PRE_OPERATIONAL, CanopenManager
from pycangui.ui import messages

OPERATIONAL = NMT_STATES[5]


def offer(parent: QWidget, manager: CanopenManager, node_id: int, what: str) -> bool:
    """Ask about Pre-operational for an Operational node. False if cancelled.

    Yes sends the NMT command and goes on; No goes on as it is. The node is
    left in Pre-operational afterwards: putting it back is a decision about
    the machine, not about the write.
    """
    if manager.nmt_states.get(node_id) != OPERATIONAL:
        return True
    answer = messages.question(
        parent,
        f"Node {node_id} is Operational",
        f"Many devices refuse {what} while Operational, and take them only in "
        f"Pre-operational.\n\nYes puts node {node_id} in Pre-operational first, and it "
        "stays there afterwards: start it again with NMT when you are done. No writes "
        "with the node as it is.",
        QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
        QMessageBox.Yes,
    )
    if answer == QMessageBox.Cancel:
        return False
    if answer == QMessageBox.Yes:
        manager.nmt(node_id, PRE_OPERATIONAL)
    return True


def state_note(manager: CanopenManager, node_id: int) -> str:
    """A line on the node's NMT state for a report of what it refused.

    Nothing when it is in Pre-operational, where the state is not the reason.
    """
    state = manager.nmt_states.get(node_id)
    if state == PRE_OPERATIONAL:
        return ""
    if state is None:
        return (
            f"\n\nNode {node_id}'s NMT state is not known: it sends no heartbeat. If it is "
            "Operational, many devices refuse these there; in Pre-operational they may go."
        )
    return f"\n\nNode {node_id} is {state}. Many devices refuse these unless in Pre-operational."
