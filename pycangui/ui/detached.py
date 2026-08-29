"""A pane in a window of its own, with no dock behind it.

Qt's floating panes are still dock widgets: the main window hit-tests dock
areas the whole time one is being dragged, so it tries to dock at every
opportunity -- holding Ctrl is the only way past it -- and it is owned by the
main window, which is why Windows keeps it out of the taskbar.

Detaching goes further and takes the pane's widget out of the dock entirely,
into a top-level window with *no parent*.  Nothing then tries to dock it, it
gets a taskbar button of its own, and it can be sent to another display and
left there.  Closing it puts the pane back where it came from, which is the
whole of the way back -- there is no dock to drag it into while it is out.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QVBoxLayout, QWidget

from pycangui import APP_NAME


class DetachedPane(QWidget):
    """One pane, living in its own window until it is closed."""

    #: The pane's name, so the main window knows which dock to fill again.
    closed = Signal(str)

    def __init__(self, name: str, title: str, widget: QWidget, on_top: bool = False) -> None:
        # No parent, deliberately: an owned window is the thing Windows keeps
        # out of the taskbar, and the taskbar entry is half the point.
        super().__init__(None)
        self.name = name
        self.setWindowTitle(f"{title} - {APP_NAME}")
        self.resize(widget.size() if widget.size().isValid() else self.size())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(widget)
        self.set_on_top(on_top)

    @property
    def pane(self) -> QWidget | None:
        item = self.layout().itemAt(0)
        return item.widget() if item is not None else None

    def set_on_top(self, on: bool) -> None:
        """Above every other window, not just above pycangui's."""
        if bool(self.windowFlags() & Qt.WindowStaysOnTopHint) == on:
            return
        self.setWindowFlag(Qt.WindowStaysOnTopHint, on)
        if not self.isHidden():
            self.show()  # changing a window flag hides the window

    def release(self) -> QWidget | None:
        """Hand the pane back, so it can go into its dock again."""
        widget = self.pane
        if widget is not None:
            self.layout().removeWidget(widget)
            widget.setParent(None)
        return widget

    def closeEvent(self, event) -> None:
        self.closed.emit(self.name)
        super().closeEvent(event)
