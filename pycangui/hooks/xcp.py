# SPDX-License-Identifier: MIT-0
#
# A starting point, copied into your workspace for you to change. It is
# yours to edit, keep private or give away: pycangui claims nothing in it
# and asks for no credit, so what you write here needs nobody's permission.
"""pycangui XCP hooks -- edit freely, this file is yours.

Each function below is called by pycangui at a decision point. Return a value
to take over, or return None to let pycangui do the normal thing. A function
that raises is reported in the Event Log pane and ignored. Tools > Reload hooks
picks up changes without a restart.
"""

from __future__ import annotations

from pycangui.core.hooks import hook


@hook
def compute_key(resource: int, seed: bytes, *, ctx) -> bytes | None:
    """Compute the XCP seed-and-key unlock key for a resource.

    ``resource`` is the resource being unlocked (0x01 CAL/PAG, 0x04 DAQ,
    0x08 STIM, 0x10 PGM). Return the key bytes, or None if you have no
    algorithm (the pane then reports that unlocking is not possible).

    Most ECUs ship the algorithm as a SeedNKey DLL; here it is plain Python.

    Examples:

        # Invert every seed byte
        # return bytes(b ^ 0xFF for b in seed)

        # Add a constant, byte-wise
        # return bytes((b + 0x11) & 0xFF for b in seed)
    """
    return None
