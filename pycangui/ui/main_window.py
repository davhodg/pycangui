"""Main window: connect bar, dockable panes, persistent layout."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QSettings, Qt, QTimer, QUrl, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDockWidget,
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QScrollArea,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from pycangui import APP_NAME, __version__
from pycangui.canopen.manager import CanopenManager
from pycangui.core.backends import BACKENDS
from pycangui.core.channels import ActiveBus, Channels
from pycangui.core.context import Context
from pycangui.core.dbc import DbcDecoder
from pycangui.core.demo import DemoDevice
from pycangui.core.detect import DEMO_CHANNEL, summarise
from pycangui.core.excepthook import ExceptionLogger
from pycangui.core.hooks import Hooks
from pycangui.core.logbridge import LogBridge
from pycangui.core.logging import WRITE_FILTER, Recorder
from pycangui.core.signals import SignalHub
from pycangui.j1939.manager import J1939Manager
from pycangui.uds.manager import UdsManager
from pycangui.ui.canopen_view import CanopenView
from pycangui.ui.confirm import Confirmations, is_real
from pycangui.ui.connect_bar import ConnectBar
from pycangui.ui.console_view import ConsoleView
from pycangui.ui.detached import DetachedPane
from pycangui.ui.help_menu import HelpMenu
from pycangui.ui.j1939_view import J1939View
from pycangui.ui.pane_bar import PaneBar
from pycangui.ui.replay_action import ReplayAction
from pycangui.ui.scope_view import ScopeView
from pycangui.ui.trace_view import TraceView
from pycangui.ui.tx_view import TxView
from pycangui.ui.uds_view import UdsView
from pycangui.ui.xcp_view import XcpView
from pycangui.xcp.manager import XcpManager

# Bumped whenever the set of docks changes.  restoreState declines a state
# saved under a different version, so an old layout is replaced by the current
# default instead of being restored with panes missing.
LAYOUT_VERSION = 3

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

#: Open on a first run.  Everything else is one click away in the View menu:
#: nine panes at once is a wall, and which of the protocol panes you want
#: depends entirely on what you have plugged in.
DEFAULT_VISIBLE = ("trace", "log", "scope")


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1400, 900)
        self.setDockNestingEnabled(True)  # full grid layouts, not just the four edges

        #: Every channel.  ``self.bus`` is whichever one is selected, wearing a
        #: single bus's interface, so the protocol stacks need not know about
        #: channels at all.
        self.channels = Channels()
        self.bus = ActiveBus(self.channels)

        # --- log pane first: everything else reports into it -----------------
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)

        # --- user context, hooks, protocol managers -------------------------
        self.ctx = Context(log=self.log.appendPlainText)
        #: python-can says everything through the logging module and nothing
        #: through return values -- a wrong bitrate is reported there and
        #: nowhere else, so without this it looks like an idle bus.
        self.log_bridge = LogBridge(self.log.appendPlainText)
        #: Started with pythonw, which has no console, so a traceback from a
        #: Qt slot would otherwise go nowhere at all -- see the module.
        self.exceptions = ExceptionLogger(self.log.appendPlainText)
        self.exceptions.install()
        self.hooks = Hooks(self.ctx)
        #: Shared so that agreeing once covers connecting, transmitting and
        #: replaying rather than each asking again.
        self.confirm = Confirmations()
        BACKENDS.load_user_backends(self.ctx.backends_dir, self.log.appendPlainText)

        # --- toolbar ---------------------------------------------------------
        self.connect_bar = ConnectBar(self.channels, self.ctx)
        self.addToolBar(self.connect_bar)
        self.connect_bar.connect_requested.connect(self._connect_active)
        self.connect_bar.disconnect_requested.connect(self._disconnect_active)
        self.record_action = self.connect_bar.addAction("Record")
        self.record_action.setCheckable(True)
        self.record_action.setToolTip("Record the selected channel to a log file")
        self.record_action.toggled.connect(self._toggle_record)
        self.replay = ReplayAction(self.connect_bar, self.channels, self.ctx, self.confirm)

        self.canopen = CanopenManager(self.bus, self.hooks)
        self.uds = UdsManager(self.bus, self.hooks, self.ctx)
        self.j1939 = J1939Manager(self.bus, self.hooks)
        self.signals = SignalHub()
        self.dbc = DbcDecoder()
        self.xcp = XcpManager(self.bus, self.hooks, self.signals, self.ctx)
        self.recorder = Recorder(self.channels)  # every connected channel

        # --- docks -----------------------------------------------------------
        self._docks: dict[str, QDockWidget] = {}
        #: The button strip at the top of each pane, shown when it is out.
        self._bars: dict[str, PaneBar] = {}
        #: Panes given a window of their own, by name.
        self._detached: dict[str, DetachedPane] = {}
        #: Panes asked to stay above other windows.
        self._on_top: set[str] = set()
        #: Pinned panes standing down while a dialog is waiting for an answer.
        self._suspended: set[str] = set()
        #: Where a detached pane came from: floating or docked, and if it was
        #: floating, where it was.  Attach puts it back there rather than
        #: dropping it into the main window, which is not where it was.
        self._came_from: dict[str, tuple[bool, object]] = {}
        self._said_undock_tip = False
        self.trace = TraceView(self.hooks, self.ctx)
        self.trace.classifiers.append(self.dbc.message_name)
        self.trace.classifiers.append(self.uds.classify)
        self.trace.classifiers.append(self.j1939.classify)
        self.trace.classifiers.append(self.xcp.classify)
        self._add_dock("trace", "Trace", self.trace, Qt.LeftDockWidgetArea)
        self.scope = ScopeView(self.signals, self.bus.now, self.ctx)
        # The two halves stay reachable by name: the Python console and the
        # docs refer to window.signals_view and window.plot.
        self.signals_view = self.scope.signals_view
        self.plot = self.scope.plot
        self._add_dock("scope", "Signals and Plot", self.scope, Qt.LeftDockWidgetArea)
        self.canopen_view = CanopenView(self.canopen, self.hooks, self.ctx)
        self._add_dock("canopen", "CANopen", self.canopen_view, Qt.RightDockWidgetArea)
        self.uds_view = UdsView(self.uds, self.ctx)
        self._add_dock("uds", "UDS", self.uds_view, Qt.RightDockWidgetArea)
        self.j1939_view = J1939View(self.j1939, self.ctx)
        self._add_dock("j1939", "J1939", self.j1939_view, Qt.RightDockWidgetArea)
        self.xcp_view = XcpView(self.xcp, self.ctx)
        self._add_dock("xcp", "XCP", self.xcp_view, Qt.RightDockWidgetArea)
        self.tx = TxView(self.bus, self.ctx, self.dbc, self.canopen, self.confirm)
        self._add_dock("tx", "Transmit", self.tx, Qt.BottomDockWidgetArea)
        self._add_dock("log", "Event Log", self.log, Qt.BottomDockWidgetArea)
        self.console = ConsoleView(self._console_namespace(), self.ctx)
        self._add_dock("console", "Python Console", self.console, Qt.BottomDockWidgetArea)
        self._arrange_default()

        self.setStatusBar(QStatusBar())
        self._frame_count = 0
        self._status_timer = QTimer(self, interval=500, timeout=self._update_status)
        self._status_timer.start()

        # --- wiring ----------------------------------------------------------
        self.channels.frames.connect(self.trace.on_frames)
        self.channels.frames.connect(self._decode_frames)
        self.recorder.state.connect(self._on_record_state)
        self.recorder.error.connect(self.log.appendPlainText)
        self.recorder.note.connect(self.log.appendPlainText)
        self.canopen.rpdos_read.connect(lambda _n: self.tx.refresh_sources())
        self.canopen.pdo_update.connect(self._on_pdo_update)
        self.channels.frames.connect(self._count_frames)
        self.channels.state_changed.connect(self._on_channel_state)
        # Queued: disconnected is emitted *before* the bus is torn down, so
        # asking straight away would still see it connected.
        self.channels.state_changed.connect(lambda *_a: self._sync_demo(), Qt.QueuedConnection)
        self.channels.error.connect(self._on_error)
        self.channels.note.connect(self.log.appendPlainText)

        # --- menus & layout persistence --------------------------------------
        file_menu = self.menuBar().addMenu("&File")
        file_menu.addAction("Load DBC...", self._load_dbc_dialog)
        file_menu.addAction("Unload all DBCs", self._unload_dbcs)
        for path in self.ctx.settings.get("dbc.paths", []):
            self._load_dbc(path)
        if (a2l := self.ctx.settings.get("xcp.a2l")) and Path(a2l).exists():
            try:
                self.xcp.load_a2l(a2l)
            except Exception as exc:
                self.log.appendPlainText(f"A2L load failed: {exc}")

        view_menu = self.menuBar().addMenu("&View")
        for dock in self.findChildren(QDockWidget):
            view_menu.addAction(dock.toggleViewAction())
        view_menu.addSeparator()
        view_menu.addAction("Dock all panes", self._dock_all)
        view_menu.addAction("Reset layout", self._reset_layout)

        self._demo: DemoDevice | None = None
        tools_menu = self.menuBar().addMenu("&Tools")
        tools_menu.addAction("Open hooks folder", self._open_hooks_folder)
        tools_menu.addAction("Open backends folder", self._open_backends_folder)
        tools_menu.addAction("Reload hooks", self._reload_hooks)
        tools_menu.addAction("Update hook stubs", self._update_hook_stubs)
        tools_menu.addSeparator()
        verbose = tools_menu.addAction("Verbose CAN logging")
        verbose.setCheckable(True)
        verbose.setToolTip("Relay the CAN libraries' info messages to the event log too")
        verbose.toggled.connect(self._set_verbose_logging)
        self.strict_dbc = tools_menu.addAction("Strict DBC checks")
        self.strict_dbc.setCheckable(True)
        self.strict_dbc.setChecked(bool(self.ctx.settings.get("dbc.strict", True)))
        self.strict_dbc.setToolTip(
            "Check that a database is well formed -- no overlapping signals, none "
            "running past the end of its message -- and ask before loading one that "
            "is not.  Turn it off to load them without being asked."
        )
        self.strict_dbc.toggled.connect(self._set_strict_dbc)

        self.help_menu = HelpMenu(self)
        self._default_state = self.saveState(LAYOUT_VERSION)
        self._restore_layout()
        self._restore_pane_state()

    # --- helpers -------------------------------------------------------------
    def _console_namespace(self) -> dict:
        """What scripts and the console see.  Keep names stable: users rely on them."""

        def send(can_id: int, data, ext: bool = False, fd: bool = False) -> None:
            self.bus.send(can_id, bytes(data), extended=ext, fd=fd)

        return {
            "ctx": self.ctx,
            "bus": self.bus,  # the selected channel
            "channels": self.channels,
            "canopen": self.canopen,
            "uds": self.uds,
            "j1939": self.j1939,
            "xcp": self.xcp,
            "hooks": self.hooks,
            "window": self,
            "recorder": self.recorder,
            "send": send,
        }

    def _add_dock(self, name: str, title: str, widget, area: Qt.DockWidgetArea) -> QDockWidget:
        dock = QDockWidget(title, self)
        self._docks[name] = dock
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
        bar.pinned.connect(lambda on, n=name: self._set_pane_on_top(n, on))
        bar.detach_requested.connect(lambda n=name: self._detach_pane(n))
        bar.attach_requested.connect(lambda n=name: self._restore_pane(n))
        self._bars[name] = bar

        container = QWidget()
        stack = QVBoxLayout(container)
        stack.setContentsMargins(0, 0, 0, 0)
        stack.setSpacing(0)
        stack.addWidget(bar)
        stack.addWidget(scroll)
        dock.setWidget(container)
        dock.topLevelChanged.connect(lambda floating, d=dock: self._on_dock_floated(d, floating))
        dock.installEventFilter(self)
        self.addDockWidget(area, dock)
        return dock

    def _on_dock_floated(self, dock: QDockWidget, floating: bool) -> None:
        """An undocked pane is left as Qt makes it, and given its buttons.

        The buttons are at the top of the pane's own content, not in a title
        bar: giving a dock a custom title bar makes Qt float it frameless, and
        that costs it the native frame along with the move, resize and close
        that come with it.
        """
        name = next((n for n, d in self._docks.items() if d is dock), "")
        # Visible as well as floating.  A pane that was undocked and then
        # closed is restored floating but hidden, which said this at every
        # start-up with nothing on screen to say it about.
        if floating and dock.isVisible() and not self._said_undock_tip:
            self._said_undock_tip = True
            self.log.appendPlainText(UNDOCK_TIP)
        self._show_pane_bar(name)

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
            name = next((n for n, d in self._docks.items() if d is watched), "")
            if name:
                QTimer.singleShot(0, lambda n=name: self._show_pane_bar(n))
        return super().eventFilter(watched, event)

    # --- what an undocked pane can be asked to do ------------------------------------
    def _show_pane_bar(self, name: str) -> None:
        """Show the strip while the pane is out, and say what it can do."""
        bar = self._bars.get(name)
        dock = self._docks.get(name)
        if bar is None or dock is None:
            return
        detached = name in self._detached
        if detached and dock.isVisible():
            # There is nothing in it -- the pane is in a window of its own --
            # so an empty one must not be left on screen.  Qt shows a restored
            # floating dock *after* the layout is put back, which is after the
            # pane was taken out of it, so hiding it once is not enough.
            dock.hide()
        bar.setVisible((dock.isFloating() and dock.isVisible()) or detached)
        bar.set_detached(detached)
        bar.set_pinned(name in self._on_top)

    def _suspend_on_top(self, watched, blocked: bool) -> None:
        """Stand a pinned pane down while a dialog waits, and put it back after.

        Without this a warning can open behind a pinned window, where it
        cannot be read, and the window cannot be moved out of the way either,
        because the dialog is holding the application.  Nothing on screen says
        why, which is worse than the warning going unread.
        """
        name = next(
            (
                n
                for n in self._docks
                if watched is self._docks[n] or watched is self._detached.get(n)
            ),
            "",
        )
        if not name or name not in self._on_top:
            return
        if blocked:
            self._suspended.add(name)
        else:
            self._suspended.discard(name)
        if (window := self._detached.get(name)) is not None:
            window.set_on_top(not blocked)
        else:
            self._apply_on_top(self._docks[name])

    def _apply_on_top(self, dock: QDockWidget) -> None:
        """Keep a floating pane above other windows, if that was asked for."""
        name = next((n for n, d in self._docks.items() if d is dock), "")
        wanted = dock.isFloating() and name in self._on_top and name not in self._suspended
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

    def _save_pane_state(self) -> None:
        """Which panes are out on their own, and which are pinned.

        Settled choices like any other, so they survive a restart: a pane left
        on a second monitor came back closed, because putting it away on the
        way out was the last thing saved about it.
        """
        self.ctx.settings.set("panes.detached", sorted(self._detached))
        self.ctx.settings.set("panes.on_top", sorted(self._on_top))

    def _restore_pane_state(self) -> None:
        """Detach and pin again whatever was when pycangui last closed."""
        self._on_top = {
            name for name in self.ctx.settings.get("panes.on_top", []) if name in self._docks
        }
        for name in self.ctx.settings.get("panes.detached", []):
            if name in self._docks:
                self._detach_pane(name)
        for name in self._docks:
            self._apply_on_top(self._docks[name])
            self._show_pane_bar(name)

    def _set_pane_on_top(self, name: str, on: bool) -> None:
        self._on_top.add(name) if on else self._on_top.discard(name)
        self._save_pane_state()
        if (window := self._detached.get(name)) is not None:
            window.set_on_top(on)
        elif (dock := self._docks.get(name)) is not None:
            self._apply_on_top(dock)
        # Rebuilding a window hides what is in it, the button strip included,
        # so put it back rather than leave the pane without its buttons.
        self._show_pane_bar(name)

    def _detach_pane(self, name: str) -> None:
        """Give a pane a window of its own, with no dock behind it."""
        dock = self._docks.get(name)
        if dock is None or name in self._detached:
            return
        widget = dock.widget()
        if widget is None:
            return
        # Where to put it back.  Attaching a pane that was floating should
        # float it again: the main window is not where it was.
        self._came_from[name] = (dock.isFloating(), dock.geometry())
        dock.setWidget(None)
        dock.hide()
        window = DetachedPane(name, dock.windowTitle(), widget, on_top=name in self._on_top)
        window.closed.connect(self._reattach_pane)
        window.installEventFilter(self)  # so a dialog can get in front of it
        self._detached[name] = window
        self._show_pane_bar(name)
        window.show()
        self._save_pane_state()

    @Slot(str)
    def _reattach_pane(self, name: str, show: bool = False) -> None:
        """Put a detached pane back in its dock.

        Hidden unless asked otherwise: closing a window means closing it,
        exactly as closing a docked pane does, and a pane that reappeared in
        the main window because you had shut it would be answering a question
        nobody asked.  The widget goes home either way, so the View menu can
        show it again.
        """
        window = self._detached.pop(name, None)
        dock = self._docks.get(name)
        if window is None or dock is None:
            return
        if (widget := window.release()) is not None:
            dock.setWidget(widget)
            widget.show()  # release() reparented it, which hides it
        was_floating, geometry = self._came_from.pop(name, (False, None))
        dock.setFloating(was_floating)
        if was_floating and geometry is not None:
            dock.setGeometry(geometry)
        dock.setVisible(show)
        self._show_pane_bar(name)
        self._save_pane_state()

    def _restore_pane(self, name: str) -> None:
        """Bring a detached pane back into the window, and show it."""
        if (window := self._detached.get(name)) is not None:
            window.close()  # its closed signal hands the widget back
        self._reattach_pane(name, show=True)
        if (dock := self._docks.get(name)) is not None:
            dock.show()

    def _arrange_default(self) -> None:
        """The layout a first run opens with: the trace, the log, and the plot.

        Everything else starts hidden rather than removed -- the View menu
        lists every pane, and showing one puts it back in the area it was
        added to, so the protocol panes still arrive on the right.
        """
        trace, log, scope = (self._docks[n] for n in DEFAULT_VISIBLE)
        # The trace and the log share the top row and the plot spans below
        # them: both of those want width, and the log's lines are short.
        # Nested splits inside one area rather than the four edges, which is
        # what setDockNestingEnabled above buys.
        self.addDockWidget(Qt.LeftDockWidgetArea, trace)
        self.splitDockWidget(trace, scope, Qt.Vertical)
        self.splitDockWidget(trace, log, Qt.Horizontal)
        for name, dock in self._docks.items():
            dock.setVisible(name in DEFAULT_VISIBLE)
        self.resizeDocks([trace, log], [7, 3], Qt.Horizontal)
        self.resizeDocks([trace, scope], [6, 4], Qt.Vertical)

    def _restore_layout(self) -> None:
        s = QSettings()
        if (geo := s.value("geometry")) is not None:
            self.restoreGeometry(geo)
        state = s.value("windowState")
        # restoreState declines a layout saved under an older LAYOUT_VERSION,
        # which leaves the default in place -- the same as never having run.
        if state is None or not self.restoreState(state, LAYOUT_VERSION):
            hidden = [d.windowTitle() for n, d in self._docks.items() if n not in DEFAULT_VISIBLE]
            self.log.appendPlainText(
                f"Panes for {', '.join(hidden)} are hidden to start with: "
                "turn any of them on in the View menu."
            )
            # The virtual channel is the default, and it is empty until
            # something fills it -- which is not obvious from looking at it.
            self.log.appendPlainText(
                "No hardware?  Connect on the virtual channel and switch on "
                "Tools > Demo CANopen device to have something to look at."
            )
        self.scope.restore_state(s.value("scopeSplitter"))

    def _dock_all(self) -> None:
        """Put every undocked pane back.

        A floating pane is a window of its own, so it can end up behind the
        main one -- the taskbar will find it, but this is the way back that
        does not depend on knowing where it went.
        """
        for name in list(self._detached):
            self._restore_pane(name)
        floating = [dock for dock in self._docks.values() if dock.isFloating()]
        for dock in floating:
            dock.setFloating(False)
        self.log.appendPlainText(
            f"Docked {len(floating)} pane(s)." if floating else "No panes are undocked."
        )

    def _reset_layout(self) -> None:
        self.restoreState(self._default_state, LAYOUT_VERSION)

    def closeEvent(self, event) -> None:
        s = QSettings()
        s.setValue("geometry", self.saveGeometry())
        s.setValue("windowState", self.saveState(LAYOUT_VERSION))
        s.setValue("scopeSplitter", self.scope.save_state())
        # Saved before they are closed: closing one puts its pane away, and
        # what is saved should be how things were left, not how they were
        # tidied up.
        self._save_pane_state()
        for name in list(self._detached):
            # Parentless windows of their own, so they would keep the
            # application running after the main window had gone.
            self._detached.pop(name).close()
        self.replay.stop()
        self.help_menu.shutdown()
        self.connect_bar.shutdown()
        self.log_bridge.detach()
        self.exceptions.remove()
        self.recorder.stop()
        self._stop_demo()
        self.bus.close()  # stop the facade before its channels go away
        self.channels.shutdown()
        self.canopen.shutdown()
        self.uds.shutdown()
        self.j1939.shutdown()
        self.xcp.shutdown()
        super().closeEvent(event)

    # --- slots ---------------------------------------------------------------
    @Slot(str)
    def _on_error(self, text: str) -> None:
        self.log.appendPlainText(f"ERROR: {text}")

    @Slot(bool)
    def _set_strict_dbc(self, on: bool) -> None:
        self.ctx.settings.set("dbc.strict", on)
        self.log.appendPlainText(
            "DBC files will be checked strictly, and you will be asked about one that fails."
            if on
            else "DBC files will be loaded without the strict checks."
        )

    @Slot(bool)
    def _set_verbose_logging(self, on: bool) -> None:
        self.log_bridge.set_verbose(on)
        self.log.appendPlainText(
            f"Verbose CAN logging {'on' if on else 'off'}: the CAN libraries' "
            f"{'info messages are' if on else 'warnings and errors are still'} relayed here."
        )

    def _open_hooks_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.ctx.hooks_dir)))

    def _open_backends_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.ctx.backends_dir)))

    def _reload_hooks(self) -> None:
        self.hooks.reload()
        bad = self.hooks.errors()
        self.log.appendPlainText(
            "Hooks reloaded" + (f" ({len(bad)} file(s) failed, see above)" if bad else "")
        )

    def _update_hook_stubs(self) -> None:
        added = self.hooks.update_stubs()
        if added:
            for module, names in added.items():
                self.log.appendPlainText(f"hooks/{module}.py: added {', '.join(names)}")
            self.hooks.reload()
        else:
            self.log.appendPlainText("Hook files already up to date")

    def _sync_demo(self) -> None:
        """Run the demo device exactly while a channel is connected to its bus.

        Selecting the channel is the switch.  A separate on/off somewhere in a
        menu meant connecting to the virtual bus and finding it empty, with
        nothing on screen to say why or what to do about it.
        """
        wanted = any(
            bus.is_connected and bus.interface == "virtual" and bus.channel == DEMO_CHANNEL
            for name in self.channels.names()
            if (bus := self.channels.get(name)) is not None
        )
        if wanted and self._demo is None:
            try:
                self._demo = DemoDevice(DEMO_CHANNEL, self)
            except Exception as exc:
                self.log.appendPlainText(f"Demo device failed to start: {exc}")
                return
            self.log.appendPlainText(
                f"Demo CANopen device running on {DEMO_CHANNEL}: "
                "node 5, heartbeat 500 ms, TPDO1 100 ms"
            )
        elif not wanted:
            self._stop_demo()

    def _stop_demo(self) -> None:
        if self._demo is not None:
            self._demo.stop()
            self._demo = None

    # --- channels ------------------------------------------------------------
    def _connect_active(
        self, interface: str, channel: str, bitrate: int, fd: bool, extra: dict | None = None
    ) -> None:
        bus = self.channels.active_bus()
        if bus is None:
            self.log.appendPlainText("No channel selected")
            return
        if not self._may_connect(bus.channel_name, interface, channel, bitrate, fd, extra):
            self.connect_bar.set_connected(False)
            return
        bus.connect_bus(interface, channel, bitrate, fd, extra)
        if not bus.is_connected:
            # connect_bus reports the reason and returns; without this the
            # button stays reading "Disconnect" for a bus we never joined.
            self.connect_bar.set_connected(False)

    def _may_connect(
        self,
        name: str,
        interface: str,
        channel: str,
        bitrate: int,
        fd: bool,
        extra: dict | None = None,
    ) -> bool:
        """Ask before joining a real bus, because the bitrate has to be right.

        A CAN controller at the wrong bitrate cannot read a frame correctly, so
        it signals an error on every one it sees.  That is not a quiet failure
        on our side: those error frames go out on the wire, and the nodes that
        are working can be driven error passive and then bus off by them, which
        on a live machine means the machine stops talking to itself.

        Keyed on the bitrate, so changing it asks again -- getting it wrong is
        the whole reason for the question.
        """
        if not is_real(interface):
            return True
        # The whole configuration goes into the key, since that is what makes
        # two otherwise identical adapters different and nobody has to read it.
        # What is *shown* is the few words that tell them apart: a Vector
        # reports its entire channel configuration, and printing that put a
        # dozen lines of ctypes enums in front of the question being asked.
        identity = ":".join(f"{k}={v}" for k, v in sorted((extra or {}).items()))
        shown = summarise(extra or {})
        where = f"{interface}:{channel}" + (f" [{shown}]" if shown else "")
        return self.confirm.ask(
            self,
            f"connect:{name}:{interface}:{channel}:{identity}:{bitrate}:{fd}",
            "Connect to a real CAN bus?",
            f"{name} is about to join {where} at "
            f"{bitrate // 1000} kbit/s.\n\n"
            "If that is not the bitrate the bus is running at, this adapter cannot "
            "read the traffic, and signals an error on every frame it sees.  Those "
            "errors go out on the bus, and can stop the working nodes on it from "
            "communicating.\n\n"
            "Check the bitrate before continuing.",
        )

    @Slot()
    def _disconnect_active(self) -> None:
        bus = self.channels.active_bus()
        if bus is not None:
            bus.disconnect_bus()

    @Slot(str, bool)
    def _on_channel_state(self, name: str, connected: bool) -> None:
        bus = self.channels.get(name)
        if connected and bus is not None:
            self.log.appendPlainText(f"{name} connected: {bus.description}")
        else:
            self.log.appendPlainText(f"{name} disconnected")

    # --- recording -----------------------------------------------------------
    @Slot(bool)
    def _toggle_record(self, on: bool) -> None:
        if not on:
            self.recorder.stop()
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Record to a log file", str(self.ctx.user_dir / "capture.blf"), WRITE_FILTER
        )
        if not path or not self.recorder.start(path):
            self.record_action.setChecked(False)

    @Slot(bool, str)
    def _on_record_state(self, recording: bool, path: str) -> None:
        self.record_action.blockSignals(True)
        self.record_action.setChecked(recording)
        self.record_action.setText("Recording..." if recording else "Record")
        self.record_action.blockSignals(False)
        self.log.appendPlainText(f"Recording to {path}" if recording else "Recording stopped")

    # --- DBC / signals -------------------------------------------------------
    def _load_dbc_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Load CAN database",
            str(self.ctx.user_dir),
            "CAN databases (*.dbc *.kcd *.sym *.arxml)",
        )
        if path and self._load_dbc(path, offer_relaxing=True):
            paths = list(self.ctx.settings.get("dbc.paths", []))
            if path not in paths:
                self.ctx.settings.set("dbc.paths", [*paths, path])

    def _load_dbc(self, path: str, offer_relaxing: bool = False) -> bool:
        """Load a database, strictly unless told otherwise.

        A strict failure is offered as a question rather than treated as the
        end of it: the check is about how well formed the file is, and a
        database that fails it is usually still perfectly usable.  Only when
        the user asked for this file, though -- the databases restored at
        startup must not put a dialog in front of a window that is still
        opening.
        """
        strict = bool(self.ctx.settings.get("dbc.strict", True))
        try:
            db = self.dbc.load(path, strict=strict)
        except Exception as exc:  # cantools parse errors come in many types
            self.log.appendPlainText(f"DBC load failed: {path}: {exc}")
            if not strict or not offer_relaxing or not self._offer_relaxed_load(path, exc):
                return False
            try:
                db = self.dbc.load(path, strict=False)
            except Exception as exc2:
                self.log.appendPlainText(f"DBC load failed even unchecked: {path}: {exc2}")
                return False
            self.log.appendPlainText(f"Loaded {path} without the strict checks")
        how = "" if strict else " (strict checks off)"
        self.log.appendPlainText(f"Loaded {path}: {len(db.messages)} messages{how}")
        if hasattr(self, "tx"):
            self.tx.refresh_sources()
        return True

    def _offer_relaxed_load(self, path: str, exc: Exception) -> bool:
        """Ask whether to load a database that failed cantools' strict check."""
        return (
            QMessageBox.question(
                self,
                "Load this database anyway?",
                f"{Path(path).name} did not pass the strict check:\n\n{exc}\n\n"
                "That check is about how well formed the file is, not about whether "
                "its messages can be used, and databases that fail it are usually "
                "still fine to read and transmit.\n\n"
                "Load it anyway?  Turning off Tools > Strict DBC checks stops the "
                "asking.",
                QMessageBox.Yes | QMessageBox.Cancel,
                QMessageBox.Yes,
            )
            == QMessageBox.Yes
        )

    def _unload_dbcs(self) -> None:
        for path in list(self.dbc.databases):
            self.dbc.unload(path)
        self.ctx.settings.set("dbc.paths", [])
        self.tx.refresh_sources()
        self.log.appendPlainText("DBC databases unloaded")

    @Slot(list)
    def _decode_frames(self, frames: list) -> None:
        if not self.dbc.loaded:
            return
        for f in frames:
            decoded = self.dbc.decode(f)
            if decoded is not None:
                msg, values = decoded
                self.signals.push_many(f"DBC {msg.name}", f.timestamp, values, self.dbc.units(msg))

    @Slot(int, str, dict)
    def _on_pdo_update(self, node_id: int, pdo_name: str, values: dict) -> None:
        self.signals.push_many(f"CANopen node {node_id} {pdo_name}", self.bus.now(), values)

    @Slot(list)
    def _count_frames(self, frames: list) -> None:
        self._frame_count += len(frames)

    def _update_status(self) -> None:
        parts = []
        for name in self.channels.names():
            bus = self.channels.get(name)
            mark = "*" if name == self.channels.active else ""  # the protocol panes' channel
            if bus and bus.is_connected:
                parts.append(f"{mark}{name}: up, {bus.load_percent:.1f}% load")
            else:
                parts.append(f"{mark}{name}: down")
        if self.recorder.is_recording:
            parts.append(f"recording {self.recorder.path.name} ({self.recorder.elapsed:.0f} s)")
        parts.append(f"frames: {self._frame_count}")
        self.statusBar().showMessage("  |  ".join(parts))
