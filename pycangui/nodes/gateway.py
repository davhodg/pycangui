# SPDX-License-Identifier: MIT-0
#
# A starting point, copied into your workspace for you to change. It is
# yours to edit, keep private or give away: pycangui claims nothing in it
# and asks for no credit, so what you write here needs nobody's permission.
"""A gateway: one node standing on two channels.

There is nothing new here. A gateway is a simulated node bound to more than
one bus, and that is the whole of the difference -- same file, same four
functions. Start it with a second channel and ``node.channels`` has two
entries; ``frame.channel`` says which one a frame came from, and
``node.send(..., channel=...)`` picks where it goes.

What this one does is the simplest useful thing: pass frames both ways, drop
the ones on ``BLOCKED``, and shift the ids in ``REMAP`` as they cross. That
covers most of what a real gateway is asked for -- keeping two segments apart
while letting some traffic through, or making a device answer at the id
another expects.

Whether the traffic should be filtered, rate limited, rewritten or counted is
your business rather than pycangui's, which is why none of that ships: this
file is where you put it.
"""

from __future__ import annotations

NAME = "Gateway"
DESCRIPTION = "Relays frames between two channels, with an id map and a block list."

#: No poll: a gateway has nothing to say on its own. Left out entirely
#: rather than defined and empty, so no timer is started for it.

#: Ids never passed on, whichever side they arrive from. A gateway that
#: forwards everything is a piece of wire.
BLOCKED: set[int] = set()

#: Ids that change as they cross, ``{arriving: leaving}``. Applied in the
#: direction it is written, and in reverse coming back, so a conversation
#: still works both ways.
REMAP: dict[int, int] = {}


def start(node, *, ctx):
    if len(node.channels) < 2:
        raise ValueError(
            "A gateway needs at least two channels. Add a second one when you start it."
        )
    node.state.reverse = {out: into for into, out in REMAP.items()}
    node.state.forwarded = 0
    node.state.dropped = 0
    node.log("relaying between " + " and ".join(node.channels))


def on_frame(node, frame, *, ctx):
    """Send this frame out of every channel it did not arrive on.

    Every channel rather than "the other one": a gateway across three
    segments is the same idea, and writing it for exactly two would be a
    limit nobody asked for.
    """
    if frame.arbitration_id in BLOCKED:
        node.state.dropped += 1
        return

    outgoing = REMAP.get(frame.arbitration_id)
    if outgoing is None:
        outgoing = node.state.reverse.get(frame.arbitration_id, frame.arbitration_id)

    for channel in node.channels:
        if channel == frame.channel:
            continue  # where it came from; sending it back is a loop
        node.send(
            outgoing,
            bytes(frame.data),
            channel=channel,
            extended=frame.is_extended_id,
        )
    node.state.forwarded += 1


def stop(node, *, ctx):
    node.log(f"forwarded {node.state.forwarded}, dropped {node.state.dropped}")
