# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Remember a widget's value between runs.

pycangui already keeps two stores.  The window's own layout -- where the panes
are, how wide the splitter is -- goes to ``QSettings``, because that is what Qt
saves and restores for you.  Everything else goes to ``settings.json`` beside
the hooks folder: dotted keys, sorted, indented, so it can be read, edited by
hand, or checked into version control.

What was missing was a tidy way to attach a single control to a key.  Doing it
by hand means a load in one place and a save in another, and the second one
gets forgotten -- which is why the trace opened in chronological mode every
time however you left it.  ``remember`` does both ends at once:

    remember(ctx, "trace.mode", self.mode)

Only settled choices belong here.  A search box, a pause button, whichever row
is selected: those describe this minute rather than how somebody works, and
restoring them is a surprise rather than a convenience -- an application that
started up paused would be a bug report.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QLineEdit,
    QMenu,
    QSpinBox,
    QTableView,
)

from pycangui.core.context import Context


def remember(ctx: Context, key: str, widget, default=None) -> None:
    """Load this widget's saved value, then save it again whenever it changes.

    The widget's own value is the fallback, so the default lives where the
    widget is built rather than being repeated here.  Loading happens before
    the save is connected, so restoring a value does not count as changing it.
    """
    saved = ctx.settings.get(key, default)

    if isinstance(widget, QCheckBox):
        if saved is not None:
            widget.setChecked(bool(saved))
        widget.toggled.connect(lambda on: ctx.settings.set(key, bool(on)))

    elif isinstance(widget, QComboBox):
        # Stored by text, not by index: a list that gains an entry would
        # otherwise silently change what was chosen.  An editable one takes
        # any text back, because the whole point of it is that what you want
        # may not be on the list.
        if saved is not None and (widget.isEditable() or widget.findText(str(saved)) >= 0):
            widget.setCurrentText(str(saved))
        widget.currentTextChanged.connect(lambda text: ctx.settings.set(key, text))

    elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
        if saved is not None:
            try:
                widget.setValue(type(widget.value())(saved))
            except (TypeError, ValueError):  # a hand-edited settings.json
                pass
        widget.valueChanged.connect(lambda value: ctx.settings.set(key, value))

    elif isinstance(widget, QLineEdit):
        if saved is not None:
            widget.setText(str(saved))
        # On editingFinished, not textChanged: settings.json is rewritten on
        # every set, and once per keystroke is no way to treat a file.
        widget.editingFinished.connect(lambda: ctx.settings.set(key, widget.text()))

    else:
        raise TypeError(f"remember() does not handle {type(widget).__name__}")


def remember_columns(
    ctx: Context, key: str, table: QTableView, hidden: tuple[str, ...] = ()
) -> QMenu:
    """A menu of a table's columns, applied now and kept between runs.

    A table that answers more than one question grows more columns than fit
    on screen at once, and which ones matter is a settled choice about how
    somebody works -- exactly what this module is for.

    Stored by column *name* rather than by position, for the same reason a
    combo box is: a table that gains a column in the middle would otherwise
    silently hide a different one.  The menu is returned so it can be put on
    a button as well as on the header, because a right-click on a header is
    a convention rather than a thing anybody can see.
    """
    saved = ctx.settings.get(key)
    away = {str(name) for name in saved} if isinstance(saved, list) else set(hidden)
    model = table.model()
    menu = QMenu(table)
    names = [str(model.headerData(i, Qt.Horizontal) or i) for i in range(model.columnCount())]

    def show(column: int, name: str, wanted: bool) -> None:
        table.setColumnHidden(column, not wanted)
        if wanted:
            away.discard(name)
        else:
            away.add(name)
        ctx.settings.set(key, sorted(away))

    for column, name in enumerate(names):
        table.setColumnHidden(column, name in away)
        action = menu.addAction(name)
        action.setCheckable(True)
        action.setChecked(name not in away)
        action.toggled.connect(lambda on, c=column, n=name: show(c, n, on))

    header = table.horizontalHeader()
    header.setContextMenuPolicy(Qt.CustomContextMenu)
    header.customContextMenuRequested.connect(lambda where: menu.exec(header.mapToGlobal(where)))
    return menu
