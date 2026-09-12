# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Every pane in the window: what kinds there are, and how many of each.

The docks used to be a fixed list built in the main window's constructor, and
nineteen places in it named one by a string constant.  That is fine while
there is exactly one of everything and impossible the moment there are two:
a second trace with a different filter, two plots watching different signals,
and -- the case this exists for -- two of a user's own panes side by side,
one per node.

So a pane has a *kind* and an *instance*.  The kind knows its title, where it
opens and how to build one; the instance is a name, which is also the dock's
``objectName``, which is how Qt's ``saveState``/``restoreState`` identify a
dock.  The first instance of a kind is named after the kind, so every layout,
detached-pane list and settings key written before any of this existed still
refers to exactly the pane it always did.  Later ones get a number.

This is deliberately a facade rather than dock plumbing.  Three callers want
the same object: a plugin adding a screen, a workspace reopening the set of
panes it was saved with, and the View menu creating one on demand.  Written
once, they cannot drift apart.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from PySide6.QtCore import QEvent, QObject, QRect, Qt, QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QDockWidget,
    QMainWindow,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from pycangui import APP_NAME
from pycangui.core.context import Context
from pycangui.ui.detached import DetachedPane
from pycangui.ui.pane_bar import PaneBar

#: Events after which Qt may have put its own window flags back.  It does that
#: whenever it moves a dock about -- at the end of a drag above all -- so
#: "always on top" cannot be set once and forgotten.
REAPPLY_AFTER = (
    QEvent.Show,
    QEvent.WindowActivate,
    QEvent.NonClientAreaMouseButtonRelease,
)

#: Qt sends these to every window of the application when a modal dialog opens
#: and when it closes.  A pinned pane has to stand down in between: it is above
#: everything, the dialog included, and a dialog nobody can see or reach --
#: while the window hiding it cannot be moved, because the dialog is holding
#: the application -- is indistinguishable from a lock-up.
BLOCKED, UNBLOCKED = QEvent.WindowBlocked, QEvent.WindowUnblocked

#: Said once a session, the first time a pane is undocked.  Qt hit-tests the
#: dock areas the whole time one is being dragged, so without this a pane
#: cannot be put in front of the main window at all.
UNDOCK_TIP = "Hold Ctrl while dragging an undocked pane to stop it docking again."

#: A pane opened now is opened floating, in front of the window.  Docking
#: it takes the room the panes already on screen were using, and somebody
#: asking for a second trace or a pane wants to look at it beside what is
#: there rather than instead of it.  Dragging it in is one gesture; finding
#: where it landed and dragging it out is two.
#: The size it opens at, unless the pane asks for more.
NEW_PANE_SIZE = (620, 460)
#: Down and right of the main window's corner, so it is obviously in front
#: of it rather than lost behind it...
NEW_PANE_OFFSET = 64
#: ...and each one after that steps again, so opening three panes gives
#: three windows rather than one window with two hidden underneath.
CASCADE = 28
CASCADE_BEFORE_WRAPPING = 6


@dataclass(frozen=True)
class PaneKind:
    """One sort of pane, and whether there can be more than one of it."""

    name: str
    title: str
    area: Qt.DockWidgetArea
    #: Given the instance name, so a pane can key its own saved state on it --
    #: two traces saving their filter under the same name would be one trace
    #: shown twice.
    build: Callable[[str], QWidget]
    #: Whether a second one means anything.  The Event Log is one log however
    #: many windows you point at it; a trace is not.
    several: bool = False
    #: Instances are named by whoever opens them rather than numbered, and so
    #: are not offered as "another one of these".  A pane is the case: there
    #: is no such thing as CustomPane 2, there is the pane called Battery limits,
    #: and it is opened by name from its own menu.
    named: bool = False
    #: Undo whatever ``build`` wired up, when an instance is removed.
    shutdown: Callable[[QWidget], None] | None = None


class Panes(QObject):
    """The docks, the buttons on them, and the windows they can be let out into."""

    #: A pane was added or removed: whatever lists them needs rebuilding.
    changed = Signal()
    #: A pane appeared or was put away: name, and whether it is on screen now.
    #: Anything that should not go on happening out of sight listens to this.
    pane_shown = Signal(str, bool)

    def __init__(self, window: QMainWindow, ctx: Context) -> None:
        super().__init__(window)
        self.window = window
        self.ctx = ctx
        self.kinds: dict[str, PaneKind] = {}
        self.docks: dict[str, QDockWidget] = {}
        #: The button strip at the top of each pane, shown when it is out.
        self.bars: dict[str, PaneBar] = {}
        #: Panes given a window of their own, by name.
        self.detached: dict[str, DetachedPane] = {}
        #: Panes asked to stay above other windows.
        self.on_top: set[str] = set()
        self._views: dict[str, QWidget] = {}
        self._kind_of: dict[str, str] = {}
        #: Pinned panes standing down while a dialog is waiting for an answer.
        self._suspended: set[str] = set()
        #: Where a detached pane came from: floating or docked, and if it was
        #: floating, where it was.  Attach puts it back there rather than
        #: dropping it into the main window, which is not where it was.
        self._came_from: dict[str, tuple[bool, object]] = {}
        self._said_undock_tip = False
        #: What each pane's visibility last was, so that the signal above
        #: fires on a change rather than on every event Qt sends.
        self._shown: dict[str, bool] = {}
        #: What each pane was configured with, where it needed anything: which
        #: file this one shows, which identifier that one reads.  Kept here so
        #: that a builder can ask for it and the workspace can write it down,
        #: which is what lets a pane be more than one of a kind without the
        #: kind having to be a special sort.
        #: Read back at once rather than only for the extra panes, because the
        #: first pane of a kind can hold a configuration too -- the first ASCII
        #: pane is reading an identifier like any other.
        self._config: dict[str, dict] = self._saved_config()
        #: What each pane was called when it was made, which is what "the
        #: default name" means.  For most kinds that is the kind's title and a
        #: number, but a custom pane is called whatever its file says and no
        #: amount of looking at "custom:battery" will produce "Battery limits".
        self._made_as: dict[str, str] = {}
        #: Panes part way between a dock and a window of their own.  Qt hides
        #: the dock before the window exists, so for a moment the pane looks
        #: put away when it is only moving -- and something listening for that
        #: would act on a state that never really happened.
        self._moving: set[str] = set()
        #: True while the saved panes are being reopened, so that opening
        #: them does not write a half-built list back over the one being
        #: read from.
        self._restoring = False

    # --- kinds and instances -----------------------------------------------------------
    def register(self, kind: PaneKind) -> None:
        self.kinds[kind.name] = kind

    def names(self) -> list[str]:
        """Every pane, in the order it was opened."""
        return list(self.docks)

    def extras(self) -> list[str]:
        """The instances beyond the first of their kind -- the removable ones."""
        return [name for name in self.docks if name != self._kind_of.get(name)]

    def instances(self, kind: str) -> list[str]:
        return [name for name, k in self._kind_of.items() if k == kind]

    def kind_of(self, name: str) -> str:
        return self._kind_of.get(name, "")

    def view(self, name: str) -> QWidget | None:
        """The pane itself, not the container the dock holds."""
        return self._views.get(name)

    def add(
        self,
        kind_name: str,
        name: str = "",
        title: str = "",
        show: bool = True,
        floating: bool = False,
        config: dict | None = None,
    ) -> str:
        """Open a pane of this kind and return its instance name.

        ``floating`` is what a person asking for a pane means: it opens in
        front of the window rather than taking room from the panes already
        on screen.  The panes built at start-up and the ones restored with
        the workspace do not, because where those go is the saved layout's
        business and not this call's.
        """
        kind = self.kinds.get(kind_name)
        if kind is None:
            return ""
        if not name and kind.named:
            self.ctx.events.warning(f"A {kind.title} pane has to be opened by name.")
            return ""
        if not name:
            # Asking for a pane there can only be one of means the one there
            # is, not a refusal: a plugin or a menu saying "open the log"
            # wants the log in front of it.
            name = self._next_name(kind) if kind.name in self.docks and kind.several else kind.name
        if name in self.docks:
            if show:
                self.docks[name].show()
                self.docks[name].raise_()
            return name  # already open; asking twice is not an error
        if name != kind.name and not kind.several:
            self.ctx.events.warning(f"There can only be one {kind.title} pane.")
            return ""
        # Stored before the pane is built, because the builder is what reads
        # it: an ASCII pane cannot decide which identifier it is showing after
        # it has been made.
        if config is not None:
            self._config[name] = dict(config)
            self._save_config()
        view = kind.build(name)
        self._views[name] = view
        self._kind_of[name] = kind.name
        self._made_as[name] = title or self._title(kind, name)
        dock = self._make_dock(name, self._made_as[name], view, kind.area)
        # Whatever somebody called it, over whatever it was made as.
        if chosen := self.titles().get(name):
            dock.setWindowTitle(chosen)
        if floating:
            self._float_new(dock)
        if show:
            dock.show()
            dock.raise_()
        else:
            # Said rather than left alone: addDockWidget on a window that is
            # already on screen shows the dock, so a pane added quietly -- one
            # a plugin brought, one the saved layout is about to place -- would
            # arrive in front of whatever somebody was doing.
            dock.hide()
        if name != kind.name:
            # The fixed panes are not in the saved list -- they are opened by
            # name every time -- so opening one changes nothing to save.
            self._save_instances()
        self.changed.emit()
        return name

    def show(self, name: str, floating: bool = False) -> None:
        """Bring a pane that already exists to the front.

        ``floating`` is for one somebody has just asked into existence -- a
        plugin's pane, the moment it is installed -- and applies only to a
        pane that is not on screen anywhere: one docked where somebody put it
        should come back there rather than jumping out into a window.
        """
        dock = self.docks.get(name)
        if dock is None:
            return
        if (window := self.detached.get(name)) is not None:
            window.show()
            window.raise_()
            return
        if floating and dock.isHidden() and not dock.isFloating():
            self._float_new(dock)
        dock.show()
        dock.raise_()

    def remove(self, name: str) -> None:
        """Close a pane for good.  The first of a kind stays: it is the pane.

        Hiding and removing are different answers to different questions, and
        the dock's own close button gives the first one -- every pane can be
        put away and brought back from the View menu.  This is the other one,
        which is why it is not on the title bar.
        """
        kind = self.kinds.get(self._kind_of.get(name, ""))
        if kind is None or name == kind.name:
            return
        if (window := self.detached.pop(name, None)) is not None:
            # Closing it would otherwise hand the pane back to a dock that is
            # on its way out, and show it there.
            window.closed.disconnect(self._reattach)
            container = window.release()
            window.close()
            window.deleteLater()
            if container is not None:
                container.deleteLater()
        dock = self.docks.pop(name)
        self.bars.pop(name, None)
        self.on_top.discard(name)
        self._suspended.discard(name)
        self._came_from.pop(name, None)
        self._kind_of.pop(name, None)
        self._shown.pop(name, None)
        if self._config.pop(name, None) is not None:
            self._save_config()
        self._made_as.pop(name, None)
        if (titles := self.titles()).pop(name, None) is not None:
            self.ctx.settings.set("panes.titles", titles)
        view = self._views.pop(name, None)
        self.window.removeDockWidget(dock)
        dock.setParent(None)
        dock.deleteLater()
        if view is not None and kind.shutdown is not None:
            kind.shutdown(view)
        self.ctx.layout.remove(f"panes/{name}")  # whatever it had saved about itself
        self._save_instances()
        self._save_pane_state()
        self.changed.emit()

    def _float_new(self, dock: QDockWidget) -> None:
        """Put a newly opened pane in its own window, in front of the main one.

        Qt floats a dock wherever it happened to be docked, which for a pane
        that was never on screen is a sliver at the edge.  So it is given a
        size and a place: down and right of the window's corner, stepping for
        each one already out, and wrapping before it walks off the screen.
        """
        out = sum(1 for d in self.docks.values() if d is not dock and d.isFloating())
        step = NEW_PANE_OFFSET + CASCADE * (out % CASCADE_BEFORE_WRAPPING)
        where = self.window.geometry()
        wanted = dock.widget().sizeHint() if dock.widget() is not None else None
        width, height = NEW_PANE_SIZE
        if wanted is not None:
            width = max(width, min(wanted.width() + 24, max(width, where.width() - 2 * step)))
            height = max(height, min(wanted.height() + 48, max(height, where.height() - 2 * step)))
        dock.setFloating(True)
        dock.setGeometry(QRect(where.x() + step, where.y() + step, width, height))

    def unregister(self, kind_name: str) -> None:
        """Remove a kind and every pane of it, first instance included.

        ``remove`` refuses the first of a kind because that one *is* the pane.
        This is the other case: the kind itself is going, because the plugin
        that brought it is being unloaded, and leaving a dock behind whose
        contents belong to code that is no longer there would be worse than
        anything the rule protects against.
        """
        kind = self.kinds.get(kind_name)
        if kind is None:
            return
        # remove() protects the first instance of a kind because that one *is*
        # the pane.  The protection does not apply when the kind itself is
        # going, so the panes are pointed at a stand-in whose name no instance
        # can equal, which is what makes them ordinary removable ones.
        going = replace(kind, name=f"{kind_name} (unloading)")
        self.kinds[going.name] = going
        for name in [n for n, k in self._kind_of.items() if k == kind_name]:
            self._kind_of[name] = going.name
            self.remove(name)
        self.kinds.pop(going.name, None)
        self.kinds.pop(kind_name, None)
        self.changed.emit()

    # --- what it is called ------------------------------------------------------------
    def titles(self) -> dict[str, str]:
        """The panes somebody has renamed.  Only those: a default is not a name."""
        stored = self.ctx.settings.get("panes.titles", {})
        return {str(k): str(v) for k, v in stored.items()} if isinstance(stored, dict) else {}

    def default_title(self, name: str) -> str:
        """What this pane would be called if nobody had said otherwise."""
        if made_as := self._made_as.get(name):
            return made_as
        kind = self.kinds.get(self._kind_of.get(name, ""))
        return self._title(kind, name) if kind is not None else name

    def rename(self, name: str, title: str) -> None:
        """Call a pane something.  An empty title puts the default back.

        The *instance name* is untouched, and that is the whole reason this is
        safe: it is the dock's objectName, which is the only thing Qt's
        restoreState uses to put a pane back where it was.  A rename that
        changed it would lose the arrangement it was renaming.
        """
        dock = self.docks.get(name)
        if dock is None:
            return
        title = title.strip()
        titles = self.titles()
        if title and title != self.default_title(name):
            titles[name] = title
        else:
            titles.pop(name, None)
            title = self.default_title(name)
        self.ctx.settings.set("panes.titles", titles)
        dock.setWindowTitle(title)
        if (window := self.detached.get(name)) is not None:
            window.setWindowTitle(f"{title} - {APP_NAME}")
        self.changed.emit()

    def set_default_title(self, name: str, title: str) -> None:
        """Change what a pane is called *unless* somebody has renamed it.

        For a pane whose title comes from its contents -- a custom pane is
        called whatever its file says -- so that editing the file does not
        quietly undo a rename.  It still becomes the default, so clearing a
        rename later lands on what the file says now rather than what it said
        when the pane was opened.
        """
        if name not in self.docks:
            return
        self._made_as[name] = title
        if name not in self.titles():
            self.docks[name].setWindowTitle(title)

    # --- what it was made with ------------------------------------------------------------
    def config(self, name: str) -> dict:
        """What this pane was opened with.  Empty for one that needed nothing.

        Answers before the pane exists as well as after, so that whatever is
        about to build one can ask what it is meant to be showing.
        """
        return dict(self._config.get(name, {}))

    def set_config(self, name: str, config: dict) -> None:
        self._config[name] = dict(config)
        self._save_config()

    def _saved_config(self) -> dict[str, dict]:
        stored = self.ctx.settings.get("panes.config", {})
        if not isinstance(stored, dict):
            return {}
        return {str(k): dict(v) for k, v in stored.items() if isinstance(v, dict)}

    def _save_config(self) -> None:
        if not self._restoring:
            self.ctx.settings.set("panes.config", self._config)

    def _next_name(self, kind: PaneKind) -> str:
        number = 2
        while f"{kind.name} {number}" in self.docks:
            number += 1
        return f"{kind.name} {number}"

    def _title(self, kind: PaneKind, name: str) -> str:
        """Trace, then Trace 2: the number is the whole of how you tell them apart."""
        suffix = name[len(kind.name) :].strip()
        return f"{kind.title} {suffix}" if suffix else kind.title

    # --- the dock itself ----------------------------------------------------------------
    def _make_dock(self, name: str, title: str, widget: QWidget, area) -> QDockWidget:
        dock = QDockWidget(title, self.window)
        self.docks[name] = dock
        dock.setObjectName(name)  # saveState/restoreState identify docks by objectName
        # Wrap in a scroll area so a pane shrunk below its natural minimum gets
        # scrollbars instead of pushing its controls off screen.
        scroll = QScrollArea()
        scroll.setWidget(widget)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)

        # The buttons live at the top of the pane's own content rather than in
        # a title bar: giving a dock a custom title bar makes Qt float it
        # frameless, which would cost it the native frame and the move, resize
        # and close that come with it.  Hidden while the pane is docked, since
        # none of it applies then.
        bar = PaneBar()
        bar.hide()
        bar.pinned.connect(lambda on, n=name: self.set_on_top(n, on))
        bar.detach_requested.connect(lambda n=name: self.detach(n))
        bar.attach_requested.connect(lambda n=name: self.attach(n))
        self.bars[name] = bar

        container = QWidget()
        stack = QVBoxLayout(container)
        stack.setContentsMargins(0, 0, 0, 0)
        stack.setSpacing(0)
        stack.addWidget(bar)
        stack.addWidget(scroll)
        dock.setWidget(container)
        dock.topLevelChanged.connect(lambda floating, d=dock: self._on_dock_floated(d, floating))
        dock.visibilityChanged.connect(lambda _v, n=name: self.note_shown(n))
        dock.installEventFilter(self)
        self.window.addDockWidget(area, dock)
        return dock

    def on_screen(self, name: str) -> bool:
        """Whether this pane is somewhere a person can actually see it.

        Not simply whether its dock is visible.  A pane tabbed behind another
        is on screen as far as anybody is concerned -- it is one click away and
        nothing has been put away -- and a detached pane is on screen in a
        window of its own precisely while its dock is hidden.
        """
        if (window := self.detached.get(name)) is not None:
            return not window.isHidden()
        dock = self.docks.get(name)
        return dock is not None and not dock.isHidden()

    def note_shown(self, name: str) -> None:
        """Say so if this pane has appeared or been put away since last time."""
        if name not in self.docks or name in self._moving:
            return
        now = self.on_screen(name)
        if self._shown.get(name) != now:
            self._shown[name] = now
            self.pane_shown.emit(name, now)

    def _name_of(self, dock) -> str:
        return next((n for n, d in self.docks.items() if d is dock), "")

    def _on_dock_floated(self, dock: QDockWidget, floating: bool) -> None:
        """An undocked pane is left as Qt makes it, and given its buttons.

        The buttons are at the top of the pane's own content, not in a title
        bar: giving a dock a custom title bar makes Qt float it frameless, and
        that costs it the native frame along with the move, resize and close
        that come with it.
        """
        # Visible as well as floating.  A pane that was undocked and then
        # closed is restored floating but hidden, which said this at every
        # start-up with nothing on screen to say it about.
        if floating and dock.isVisible() and not self._said_undock_tip:
            self._said_undock_tip = True
            self.ctx.events.information(UNDOCK_TIP)
        self.show_bar(self._name_of(dock))

    def eventFilter(self, watched, event) -> bool:
        """Put "always on top" and the buttons back after Qt has moved a pane."""
        if event.type() in (BLOCKED, UNBLOCKED):
            self._suspend_on_top(watched, event.type() == BLOCKED)
        if isinstance(watched, QDockWidget) and event.type() in REAPPLY_AFTER:
            # Deferred: Qt is part way through whatever it is doing to this
            # pane, and setWindowFlags hides and re-shows the widget.
            QTimer.singleShot(0, lambda d=watched: self._apply_on_top(d))
            # A pane restored floating is shown *after* topLevelChanged says so,
            # so asking then found it invisible and left it without its buttons.
            if name := self._name_of(watched):
                QTimer.singleShot(0, lambda n=name: self.show_bar(n))
        return super().eventFilter(watched, event)

    # --- what an undocked pane can be asked to do ---------------------------------------
    def show_bar(self, name: str) -> None:
        """Show the strip while the pane is out, and say what it can do."""
        bar = self.bars.get(name)
        dock = self.docks.get(name)
        if bar is None or dock is None:
            return
        detached = name in self.detached
        if detached and dock.isVisible():
            # There is nothing in it -- the pane is in a window of its own --
            # so an empty one must not be left on screen.  Qt shows a restored
            # floating dock *after* the layout is put back, which is after the
            # pane was taken out of it, so hiding it once is not enough.
            dock.hide()
        bar.setVisible((dock.isFloating() and dock.isVisible()) or detached)
        bar.set_detached(detached)
        bar.set_pinned(name in self.on_top)

    def _suspend_on_top(self, watched, blocked: bool) -> None:
        """Stand a pinned pane down while a dialog waits, and put it back after.

        Without this a warning can open behind a pinned window, where it
        cannot be read, and the window cannot be moved out of the way either,
        because the dialog is holding the application.  Nothing on screen says
        why, which is worse than the warning going unread.
        """
        name = next(
            (n for n in self.docks if watched is self.docks[n] or watched is self.detached.get(n)),
            "",
        )
        if not name or name not in self.on_top:
            return
        if blocked:
            self._suspended.add(name)
        else:
            self._suspended.discard(name)
        if (window := self.detached.get(name)) is not None:
            window.set_on_top(not blocked)
        else:
            self._apply_on_top(self.docks[name])

    def _apply_on_top(self, dock: QDockWidget) -> None:
        """Keep a floating pane above other windows, if that was asked for."""
        name = self._name_of(dock)
        if not name:
            return  # removed while the deferred call was still in the queue
        wanted = dock.isFloating() and name in self.on_top and name not in self._suspended
        flags = dock.windowFlags()
        if flags & Qt.FramelessWindowHint:
            return  # still being dragged; Qt gives it a frame when it lands
        if bool(flags & Qt.WindowStaysOnTopHint) == wanted:
            return  # nothing to do, and setWindowFlags would hide the window
        dock.setWindowFlags(
            flags | Qt.WindowStaysOnTopHint if wanted else flags & ~Qt.WindowStaysOnTopHint
        )
        dock.show()
        if (widget := dock.widget()) is not None:
            widget.show()  # the window was rebuilt, so its contents are hidden
        if wanted:
            dock.raise_()

    def set_on_top(self, name: str, on: bool) -> None:
        self.on_top.add(name) if on else self.on_top.discard(name)
        self._save_pane_state()
        if (window := self.detached.get(name)) is not None:
            window.set_on_top(on)
        elif (dock := self.docks.get(name)) is not None:
            self._apply_on_top(dock)
        # Rebuilding a window hides what is in it, the button strip included,
        # so put it back rather than leave the pane without its buttons.
        self.show_bar(name)

    def detach(self, name: str) -> None:
        """Give a pane a window of its own, with no dock behind it."""
        dock = self.docks.get(name)
        if dock is None or name in self.detached:
            return
        widget = dock.widget()
        if widget is None:
            return
        # Where to put it back.  Attaching a pane that was floating should
        # float it again: the main window is not where it was.
        self._came_from[name] = (dock.isFloating(), dock.geometry())
        self._moving.add(name)
        try:
            dock.setWidget(None)
            dock.hide()
            window = DetachedPane(name, dock.windowTitle(), widget, on_top=name in self.on_top)
            window.closed.connect(self._reattach)
            window.installEventFilter(self)  # so a dialog can get in front of it
            self.detached[name] = window
            self.show_bar(name)
            window.show()
        finally:
            self._moving.discard(name)
        self.note_shown(name)
        self._save_pane_state()

    @Slot(str)
    def _reattach(self, name: str, show: bool = False) -> None:
        """Put a detached pane back in its dock.

        Hidden unless asked otherwise: closing a window means closing it,
        exactly as closing a docked pane does, and a pane that reappeared in
        the main window because you had shut it would be answering a question
        nobody asked.  The widget goes home either way, so the View menu can
        show it again.
        """
        window = self.detached.pop(name, None)
        dock = self.docks.get(name)
        if window is None or dock is None:
            return
        self._moving.add(name)
        try:
            if (widget := window.release()) is not None:
                dock.setWidget(widget)
                widget.show()  # release() reparented it, which hides it
            was_floating, geometry = self._came_from.pop(name, (False, None))
            dock.setFloating(was_floating)
            if was_floating and geometry is not None:
                dock.setGeometry(geometry)
            dock.setVisible(show)
            self.show_bar(name)
        finally:
            self._moving.discard(name)
        self.note_shown(name)
        self._save_pane_state()

    def attach(self, name: str) -> None:
        """Bring a detached pane back into the window, and show it."""
        if (window := self.detached.get(name)) is not None:
            window.close()  # its closed signal hands the widget back
        self._reattach(name, show=True)
        if (dock := self.docks.get(name)) is not None:
            dock.show()

    def dock_all(self) -> None:
        """Put every undocked pane back.

        A floating pane is a window of its own, so it can end up behind the
        main one -- the taskbar will find it, but this is the way back that
        does not depend on knowing where it went.
        """
        for name in list(self.detached):
            self.attach(name)
        floating = [dock for dock in self.docks.values() if dock.isFloating()]
        for dock in floating:
            dock.setFloating(False)
        self.ctx.events.information(
            f"Docked {len(floating)} pane(s)." if floating else "No panes are undocked."
        )

    def close_detached(self) -> None:
        """On the way out: they are parentless windows and would outlive us."""
        for name in list(self.detached):
            self.detached.pop(name).close()

    # --- what is remembered -------------------------------------------------------------
    def save(self) -> None:
        """Everything remembered about the panes, on the way out."""
        self._save_instances()
        self._save_pane_state()

    def _save_instances(self) -> None:
        """Which panes are open beyond the fixed set, so they open again.

        Kept apart from what is remembered *about* a pane below, because the
        two are written at different times: the instances while the window is
        still being built, the rest only when somebody moves something.  One
        call doing both wrote an empty pin list over the saved one before
        anything had had the chance to read it.
        """
        if self._restoring:
            return
        self.ctx.settings.set(
            "panes.instances",
            [
                {
                    "kind": self._kind_of[name],
                    "name": name,
                    "title": self.default_title(name),
                }
                for name in self.extras()
            ],
        )

    def _save_pane_state(self) -> None:
        """Which panes are out on their own, and which are pinned.

        Settled choices like any other, so they survive a restart: a pane left
        on a second monitor came back closed, because putting it away on the
        way out was the last thing saved about it.
        """
        self.ctx.settings.set("panes.detached", sorted(self.detached))
        self.ctx.settings.set("panes.on_top", sorted(self.on_top))

    def restore_instances(self) -> None:
        """Reopen the extra panes, before the saved layout is put back.

        Order matters: ``restoreState`` places the docks that exist when it
        runs and silently ignores the rest, so a pane created afterwards would
        arrive with no position of its own.
        """
        self._restoring = True
        try:
            for saved in self.ctx.settings.get("panes.instances", []):
                if not isinstance(saved, dict):
                    continue  # a hand-edited settings.json
                self.add(
                    str(saved.get("kind", "")),
                    name=str(saved.get("name", "")),
                    title=str(saved.get("title", "")),
                    show=False,
                )
        finally:
            self._restoring = False

    def restore_state(self) -> None:
        """Detach and pin again whatever was when pycangui last closed."""
        self.on_top = {
            name for name in self.ctx.settings.get("panes.on_top", []) if name in self.docks
        }
        for name in self.ctx.settings.get("panes.detached", []):
            if name in self.docks:
                self.detach(name)
        for name in self.docks:
            self._apply_on_top(self.docks[name])
            self.show_bar(name)

    def save_view_states(self) -> None:
        """Whatever a pane says about itself -- a splitter position, so far.

        Kept per instance rather than per kind, or the second plot's splitter
        would be the first one's -- and in the workspace rather than in
        QSettings, so it travels with the arrangement it belongs to.
        """
        for name, view in self._views.items():
            if hasattr(view, "save_state"):
                self.ctx.layout.set(f"panes/{name}", bytes(view.save_state()))

    def restore_view_states(self) -> None:
        for name, view in self._views.items():
            if hasattr(view, "restore_state"):
                view.restore_state(self.ctx.layout.get(f"panes/{name}"))
