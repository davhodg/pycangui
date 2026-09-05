"""The object a plugin is handed, and the whole of what a plugin can do.

One object with one caller each way: a plugin calls into it to add things, and
it calls into the parts of pycangui that already know how to hold them -- the
pane facade for a dock, the menu bar for an entry, the toolbar for a button.
Nothing here reimplements any of that, which is the point.  ``add_pane`` and
``View > New pane`` and a workspace reopening what it was closed with are three
callers of the same code, so they cannot drift apart.

Everything added is recorded against the plugin that added it, so that
reloading really is reloading.  Without that, a plugin edited and loaded again
leaves its old pane, its old menu entries and its old buttons on screen beside
the new ones, and the second reload leaves three.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QWidget

from pycangui.core.plugins import API_VERSION
from pycangui.ui.panes import PaneKind

#: What a plugin may say instead of importing Qt to name a dock area.  A plugin
#: should not have to know what a ``Qt.DockWidgetArea`` is in order to say
#: "put it on the right".
AREAS = {
    "left": Qt.LeftDockWidgetArea,
    "right": Qt.RightDockWidgetArea,
    "top": Qt.TopDockWidgetArea,
    "bottom": Qt.BottomDockWidgetArea,
}


class PluginApp:
    """What ``register(app)`` is given.  One of these per plugin."""

    #: So a plugin can ask rather than guess, and degrade rather than fail.
    api_version = API_VERSION

    def __init__(self, window, plugin: str) -> None:
        self.plugin = plugin
        self.window = window
        self.ctx = window.ctx
        self.hooks = window.hooks
        self.panes = window.panes
        self.channels = window.channels
        self.bus = window.bus
        self.signals = window.signals
        self.canopen = window.canopen
        self.uds = window.uds
        self.j1939 = window.j1939
        self.xcp = window.xcp
        self.dbc = window.dbc
        self.confirm = window.confirm
        #: What this plugin has added, so that it can all be taken back.
        self._kinds: list[str] = []
        self._actions: list[QAction] = []
        self._labellers: list[Callable] = []
        self._widget_kinds: list[str] = []

    # --- a screen of its own ------------------------------------------------------------
    def add_pane(
        self,
        name: str,
        title: str,
        build: Callable[[str], QWidget],
        area: str = "right",
        several: bool = False,
    ) -> str:
        """Add a dock, and open it hidden.

        Hidden because a plugin's screen is one more pane among a dozen, and
        opening every one of them on top of whatever somebody was doing is how
        a tool becomes a wall.  It is in the View menu, which is where every
        other pane is found.
        """
        kind = f"{self.plugin}:{name}"
        where = AREAS.get(area.lower(), Qt.RightDockWidgetArea) if isinstance(area, str) else area
        self.panes.register(PaneKind(kind, title, where, build, several=several))
        self._kinds.append(kind)
        opened = self.panes.add(kind, show=False)
        return opened

    def open_pane(self, name: str) -> None:
        """Show one of this plugin's panes, for a plugin that has a reason to."""
        if (dock := self.panes.docks.get(f"{self.plugin}:{name}")) is not None:
            dock.show()
            dock.raise_()

    # --- somewhere to press --------------------------------------------------------------
    def add_menu_action(
        self, text: str, callback: Callable[[], None], tooltip: str = ""
    ) -> QAction:
        """An entry under Tools > Plugins, grouped by which plugin added it."""
        action = self.window.plugin_menu(self.plugin).addAction(text, callback)
        action.setToolTip(tooltip)
        self._actions.append(action)
        return action

    def add_toolbar_button(
        self, text: str, callback: Callable[[], None], tooltip: str = "", checkable: bool = False
    ) -> QAction:
        action = self.window.connect_bar.addAction(text)
        action.setToolTip(tooltip or text)
        action.setCheckable(checkable)
        action.triggered.connect(callback)
        self._actions.append(action)
        return action

    # --- joining in with what is already there ---------------------------------------------
    def add_trace_labeller(self, labeller: Callable) -> None:
        """Name frames in the trace: ``f(frame) -> str | None``.

        Applied to every trace pane, the ones already open and the ones opened
        later, because a second trace showing different names from the first
        would be a puzzle rather than a feature.
        """
        self._labellers.append(labeller)
        self.window.add_trace_labeller(labeller)

    def add_field_widget(self, kind: str, widget_class: type) -> None:
        """An eighth way for a pane to show an object.

        The seven built in cover every configuration screen we know of, which
        is not the same as every screen there will ever be.
        """
        from pycangui.ui import field_widgets

        field_widgets.BY_KIND[kind] = widget_class
        self._widget_kinds.append(kind)

    # --- doing something slow --------------------------------------------------------------
    def run_in_background(self, job: Callable[[], Any], done: Callable[[Any, Any], None]) -> None:
        """Run ``job`` off the GUI thread and call ``done(result, error)`` on it.

        A plugin that reads five hundred objects on the GUI thread freezes the
        window until it has finished, and a frozen window is indistinguishable
        from a crashed one.
        """
        self.canopen._worker.submit(job, done)

    # --- saying something -------------------------------------------------------------------
    def log(self, message: str) -> None:
        self.ctx.events.information(f"{self.plugin}: {message}")

    def warn(self, message: str) -> None:
        self.ctx.events.warning(f"{self.plugin}: {message}")

    def error(self, message: str) -> None:
        self.ctx.events.error(f"{self.plugin}: {message}")

    # --- and taking it all back ----------------------------------------------------------------
    def remove_all(self) -> None:
        """Undo everything this plugin added.  Called before it is loaded again."""
        for kind in self._kinds:
            self.panes.unregister(kind)
        self._kinds.clear()
        for action in self._actions:
            if (parent := action.parent()) is not None and hasattr(parent, "removeAction"):
                parent.removeAction(action)
            action.deleteLater()
        self._actions.clear()
        for labeller in self._labellers:
            self.window.remove_trace_labeller(labeller)
        self._labellers.clear()
        from pycangui.ui import field_widgets

        for kind in self._widget_kinds:
            field_widgets.BY_KIND.pop(kind, None)
        self._widget_kinds.clear()
        self.window.drop_plugin_menu(self.plugin)
