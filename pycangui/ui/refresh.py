# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""How often a live view repaints, and the one control that changes it.

Two panes redraw on a timer: the trace, which appends rows as frames arrive,
and the plot, which pulls the visible window out of the signal hub every
50 ms. Both are fast because the bus is fast, and on a busy bus both become
unreadable -- rows go past faster than they can be seen, and a curve at
20 Hz is a shimmer.

The control is a choice rather than a rate, and that is a decision rather
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

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QComboBox

#: The plot's own timer, and the trace repainting as fast as frames arrive.
FAST_MS = 50

#: Four times a second. A figure changing faster than about that cannot be
#: read at all, and this is the trace's row of numbers as much as the plot's
#: curve. Slower still would be more comfortable to read and would start to
#: feel like lag on a pane the user is driving.
SLOW_MS = 250

LABEL = "Slow refresh"

LIVE, PAUSED = "Live refresh", "Paused"
#: How a pane keeps up, one choice for what were two ticks.
CHOICES = (LIVE, LABEL, PAUSED)


def display_choice(
    pause: QCheckBox, slow: QCheckBox, tip: str, item_tips: tuple[str, str, str]
) -> QComboBox:
    """Live refresh, Slow refresh or Paused, as one menu over the two ticks.

    Pause and Slow refresh were two ticks on rows already too long to read,
    and they are two states of one thing: how the pane keeps up. The ticks
    stay underneath, never shown -- Slow refresh is remembered through its
    own, and whatever asks whether a pane is paused goes on asking the same
    way -- and the menu follows them however they are set, from the console
    or a restored setting as much as from the menu.

    Paused leaves Slow refresh as it was, so that going back is going back to
    what it was before.

    ``tip`` leads with what the choice does not touch. "Paused" beside a
    trace reads, to somebody recording, as the recording paused -- and it is
    only the screen -- so the menu says so, and each choice says so again
    when it is hovered in the list.
    """
    box = QComboBox()
    box.addItems(CHOICES)
    box.setToolTip(tip)
    for index, item_tip in enumerate(item_tips):
        box.setItemData(index, item_tip, Qt.ToolTipRole)
    for tick in (pause, slow):
        tick.hide()

    def show(_checked: bool = False) -> None:
        if pause.isChecked():
            choice = PAUSED
        else:
            choice = LABEL if slow.isChecked() else LIVE
        box.setCurrentIndex(CHOICES.index(choice))

    def chosen(index: int) -> None:
        if CHOICES[index] == PAUSED:
            pause.setChecked(True)
            return
        slow.setChecked(CHOICES[index] == LABEL)
        pause.setChecked(False)

    box.activated.connect(chosen)
    for tick in (pause, slow):
        tick.toggled.connect(show)
    show()
    return box
