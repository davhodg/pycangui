# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""How often a live view repaints, and the one control that changes it.

Two panes redraw on a timer: the trace, which appends rows as frames arrive,
and the plot, which pulls the visible window out of the signal hub every
50 ms. Both are fast because the bus is fast, and on a busy bus both become
unreadable -- rows go past faster than they can be seen, and a curve at
20 Hz is a shimmer.

The control is a tick box rather than a rate, and that is a decision rather
than an omission. A number here is a number somebody has to choose without
knowing what it means, and the useful range has two ends: as fast as the
data, or slow enough to read. The second is about human eyes rather than
about this bus, so it does not vary from one bus to the next and there is
nothing to tune.

Nothing here touches capture. Frames still arrive, are classified, decoded,
recorded and exported at full rate whatever this is set to; what changes is
how often the screen is brought up to date with them. Slowed, the trace
holds arriving rows and adds them in one go -- which is also why it costs
less: one insertion of two hundred rows is far cheaper than two hundred
insertions of one.
"""

from __future__ import annotations

#: The plot's own timer, and the trace repainting as fast as frames arrive.
FAST_MS = 50

#: Four times a second. A figure changing faster than about that cannot be
#: read at all, and this is the trace's row of numbers as much as the plot's
#: curve. Slower still would be more comfortable to read and would start to
#: feel like lag on a pane the user is driving.
SLOW_MS = 250

LABEL = "Slow refresh"

TIP = (
    "Repaint four times a second instead of twenty, for a bus busy enough\n"
    "that the screen is a blur.\n"
    "\n"
    "It changes the screen and nothing else: every frame is still received,\n"
    "classified, decoded, recorded and exported exactly as before. Nothing\n"
    "is dropped and nothing is skipped -- the rows all arrive, in one go\n"
    "rather than a dribble, which is also why it costs less to draw."
)
