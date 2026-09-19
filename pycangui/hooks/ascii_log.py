# SPDX-License-Identifier: MIT-0
#
# A starting point, copied into your workspace for you to change. It is
# yours to edit, keep private or give away: pycangui claims nothing in it
# and asks for no credit, so what you write here needs nobody's permission.
"""pycangui ASCII log hooks -- edit freely, this file is yours.

Each function below is called by pycangui at a decision point. Return a value
to take over, or return None to let pycangui do the normal thing. A function
that raises is reported in the Event Log pane and ignored. Tools > Reload hooks
picks up changes without a restart.
"""

from __future__ import annotations

from pycangui.core.hooks import hook


@hook
def enable(on: bool, can_id: int, extended: bool, *, ctx) -> bool | None:
    """Turn a device's text output on or off.

    Plenty of controllers do not print onto the bus until they are asked,
    and stop when they are asked again -- and how you ask is the maker's
    own business, so it cannot live in pycangui. The ASCII Log pane's
    **Enable** button calls this; without a hook the button says there is
    nothing to call and does nothing else.

    Return True when the request has been sent. Anything else, None
    included, is reported as "nothing was sent", because a button that
    looks as though it worked is worse than one that admits it did not.

    ``can_id`` and ``extended`` are the stream the pane is reading, which
    is usually what identifies the device to ask.

    This runs on the window's own thread, so anything that waits for the
    device holds the window still while it waits. An SDO write is the one
    to watch: it blocks until the node answers, or for the whole SDO
    timeout if it does not. A plain frame cannot wait, having nothing to
    wait for.

    Examples:

        # A command on the device's own id, and a different byte to stop.
        # Sent and forgotten, so the window never waits.
        # ctx.channels.active_bus().send(0x600, b"\\x01" if on else b"\\x00")
        # return True

        # Through CANopen: a manufacturer object that turns printing on.
        # This waits for the node to answer -- fine for a button press,
        # and not something to do in a hook that is called often.
        # node = ctx.canopen.node(5)
        # node.sdo[0x2100].raw = 1 if on else 0
        # return True

        # Over UDS: a routine that starts and stops the trace output.
        # The manager's own calls queue the work and report to the Event
        # Log rather than waiting, so this returns before the ECU has
        # answered -- True here means "asked", not "done".
        # ctx.uds.routine(1 if on else 2, 0x0210, b"")
        # return True
    """
    return None
