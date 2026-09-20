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
    0x08 STIM, 0x10 PGM). Return the key bytes, or None to let pycangui fall
    back to the seed and key DLL chosen in the XCP pane -- and, where there
    is no DLL either, report that unlocking is not possible.

    Most ECUs ship the algorithm as a seed and key DLL, which pycangui
    loads itself: the interface is standard, so it needs nothing here. This
    is for an algorithm that is a few lines of Python, or for a resource the
    DLL does not cover -- answer for the ones you know and return None for
    the rest, and each is dealt with by whatever can.

    Examples:

        # Invert every seed byte
        # return bytes(b ^ 0xFF for b in seed)

        # Add a constant, byte-wise
        # return bytes((b + 0x11) & 0xFF for b in seed)
    """
    return None
