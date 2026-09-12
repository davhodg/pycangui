# SPDX-License-Identifier: MIT-0
#
# A starting point, copied into your workspace for you to change.  It is
# yours to edit, keep private or give away: pycangui claims nothing in it
# and asks for no credit, so what you write here needs nobody's permission.
"""pycangui transmit hooks -- edit freely, this file is yours.

Each function below is called by pycangui at a decision point.  Return a value
to take over, or return None to let pycangui do the normal thing.  A function
that raises is reported in the Event Log pane and ignored.  Tools > Reload hooks
picks up changes without a restart.

This one exists because a checksum is the part of a message most likely to be
nobody's standard.  pycangui knows XOR, sums and a few CRCs, and a maker's own
arithmetic is none of them -- so when the list has no entry for what your
device wants, the answer is not to argue with the list, it is to write it here.
"""

from __future__ import annotations

from pycangui.core.hooks import hook


@hook
def checksum(name: str, can_id: int, data: bytes, *, ctx) -> int | None:
    """The checksum for a message about to be transmitted, or None.

    Called for every message sent from a transmit pane that has a checksum
    configured, *after* its counter has been written, with ``data`` as the
    payload at that moment.  Return an integer to use instead of the
    configured algorithm; return None -- as this default does -- to let the
    configured one stand.

    ``name`` is the row's name, or the DBC message name where it has one, so
    one function can answer for several messages and leave the rest alone.
    Returning None for anything you do not recognise is the whole of how that
    is arranged: this hook overriding every message in the list because it
    fell through to a default would be a bad afternoon.

    The value is written where the pane's configuration says it goes, in the
    byte or nibble chosen there, so this function decides the arithmetic and
    nothing else.

    Example -- a sum over the first six bytes, seeded with the identifier,
    which is the shape a great many proprietary checksums take:

        if name != "Command":
            return None
        return (sum(data[:6]) + (can_id & 0xFF)) & 0xFF
    """
    return None
