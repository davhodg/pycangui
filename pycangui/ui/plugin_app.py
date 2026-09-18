# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""The object a plugin is handed, and the whole of what a plugin can do.

One object with one caller each way: a plugin calls into it to add things, and
it calls into the parts of pycangui that already know how to hold them -- the
pane facade for a dock, the menu bar for an entry, the toolbar for a button.
Nothing here reimplements any of that, which is the point. ``add_pane`` and
``View > New pane`` and a workspace reopening what it was closed with are three
callers of the same code, so they cannot drift apart.

Everything added is recorded against the plugin that added it, so that
reloading really is reloading. Without that, a plugin edited and loaded again
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

#: What a plugin may say instead of importing Qt to name a dock area. A plugin
#: should not have to know what a ``Qt.DockWidgetArea`` is in order to say
#: "put it on the right".
AREAS = {
    "left": Qt.LeftDockWidgetArea,
    "right": Qt.RightDockWidgetArea,
    "top": Qt.TopDockWidgetArea,
    "bottom": Qt.BottomDockWidgetArea,
}


class PluginApp:
    """What ``register(app)`` is given. One of these per plugin."""

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
        #: The instances those kinds opened, in the order they were added, so
        #: that a plugin just installed can be shown rather than merely being
        #: somewhere in the View menu.
        self._panes: list[str] = []
        self._actions: list[QAction] = []
        #: Connected to the pane facade's signals on this plugin's behalf,
        #: and disconnected when it is unloaded -- a relay left connected to
        #: code that is no longer there would run on the next pane change.
        self._watchers: list[Callable] = []
        #: Called as the window goes, and disconnected if the plugin goes
        #: first -- a closer left connected would run against code that had
        #: already been unloaded.
        self._closers: list[Callable] = []
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
        shutdown: Callable[[QWidget], None] | None = None,
    ) -> str:
        """Add a dock, and open it hidden.

        Hidden because a plugin's screen is one more pane among a dozen, and
        opening every one of them on top of whatever somebody was doing is how
        a tool becomes a wall. It is in the View menu, which is where every
        other pane is found.

        ``shutdown`` is called with the pane when it goes -- closed for good, or
        taken away because the plugin was unloaded. A plugin that has only put
        things on screen needs nothing here. One that has put a piece of
        equipment into a state does: the equipment does not stop because the
        window showing it did.
        """
        kind = f"{self.plugin}:{name}"
        where = AREAS.get(area.lower(), Qt.RightDockWidgetArea) if isinstance(area, str) else area
        self.panes.register(PaneKind(kind, title, where, build, several=several, shutdown=shutdown))
        self._kinds.append(kind)
        opened = self.panes.add(kind, show=False)
        if opened:
            self._panes.append(opened)
        return opened

    def open_pane(self, name: str) -> None:
        """Show one of this plugin's panes, for a plugin that has a reason to."""
        self.panes.show(f"{self.plugin}:{name}")

    def on_pane_shown(self, callback: Callable[[str, bool], None]) -> None:
        """Be told when one of this plugin's panes appears or is put away.

        Added because the second plugin written through this API needed it and
        the first did not, which is the test the API was built to be put to.
        A pane that polls a bus should stop while nobody can see it -- every
        read is a round trip on somebody's equipment, and a pane put away is a
        pane with no reader.

        Only this plugin's own panes are reported, so that a plugin need not
        filter out the ones it has never heard of.
        """

        def relay(name: str, on: bool) -> None:
            if name in self._panes:
                callback(name, on)

        self.panes.pane_shown.connect(relay)
        self._watchers.append(relay)

    def on_closing(self, callback: Callable[[], None]) -> None:
        """Be told once, as the window goes, before anything is torn down.

        The other half of ``shutdown``: a pane is not removed when the tool is
        closed, it goes with the window, so a plugin that has left equipment
        running would otherwise never hear about the one moment it most needs
        to. Called on the GUI thread while the buses are still open, which is
        the only time a last write can still be sent.
        """
        self.window.closing.connect(callback)
        self._closers.append(callback)

    def show_panes(self) -> None:
        """Bring this plugin's panes out.

        Not called at load time -- a plugin's screen is one more pane among a
        dozen, and opening every one of them at start-up is how a tool becomes
        a wall. Called the moment a plugin is *installed*, because somebody
        who has just asked for it should be shown what they got rather than
        having to go and look for it in a menu.
        """
        for name in self._panes:
            self.panes.show(name, floating=True)

    # --- somewhere to press --------------------------------------------------------------
    def add_menu_action(
        self, text: str, callback: Callable[[], None], tooltip: str = ""
    ) -> QAction:
        """An entry under Plugins > <the plugin's name>, so each plugin's own are together."""
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
        """Undo everything this plugin added. Called before it is loaded again."""
        for kind in self._kinds:
            self.panes.unregister(kind)
        self._kinds.clear()
        self._panes.clear()
        for watcher in self._watchers:
            self.panes.pane_shown.disconnect(watcher)
        self._watchers.clear()
        for closer in self._closers:
            self.window.closing.disconnect(closer)
        self._closers.clear()
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
