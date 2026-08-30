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

from PySide6.QtWidgets import QCheckBox, QComboBox, QDoubleSpinBox, QLineEdit, QSpinBox

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
