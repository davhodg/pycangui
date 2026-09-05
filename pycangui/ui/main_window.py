"""Main window: connect bar, dockable panes, persistent layout."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QInputDialog,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QStatusBar,
)

from pycangui import APP_NAME, __version__
from pycangui.canopen.manager import CanopenManager
from pycangui.core import workspaces
from pycangui.core.backends import BACKENDS
from pycangui.core.channels import ActiveBus, Channels
from pycangui.core.context import Context
from pycangui.core.dbc import DbcDecoder
from pycangui.core.demo import DemoDevice
from pycangui.core.detect import DEMO_CHANNEL, summarise
from pycangui.core.events import PROBLEMS, EventLog
from pycangui.core.excepthook import ExceptionLogger
from pycangui.core.export import write_csv
from pycangui.core.hooks import Hooks
from pycangui.core.logbridge import LogBridge
from pycangui.core.logging import WRITE_FILTER, Recorder
from pycangui.core.plugins import Plugins
from pycangui.core.plugins import builtin_dir as builtin_plugins_dir
from pycangui.core.signals import SignalHub
from pycangui.custom_panes import model as custom_model
from pycangui.j1939.manager import J1939Manager
from pycangui.uds.manager import UdsManager
from pycangui.ui import folders
from pycangui.ui.ascii_view import AsciiView
from pycangui.ui.canopen_view import CanopenView
from pycangui.ui.confirm import Confirmations, is_real
from pycangui.ui.connect_bar import ConnectBar
from pycangui.ui.console_view import ConsoleView
from pycangui.ui.custom_pane_view import CustomPaneView
from pycangui.ui.help_menu import HelpMenu
from pycangui.ui.j1939_view import J1939View
from pycangui.ui.panes import PaneKind, Panes
from pycangui.ui.plugin_app import PluginApp
from pycangui.ui.replay_action import ReplayAction
from pycangui.ui.scope_view import ScopeView
from pycangui.ui.trace_view import TraceView
from pycangui.ui.tx_view import TxView
from pycangui.ui.uds_view import UdsView
from pycangui.ui.workspace_menu import SWITCH_WHILE_CONNECTED, WorkspaceMenu
from pycangui.ui.xcp_view import XcpView
from pycangui.xcp.manager import XcpManager


def window_title() -> str:
    """The workspace is named only when it is not the one everybody has.

    Somebody with a single product should not be able to tell from the window
    that workspaces were built, and a title bar reading "default" would be the
    one place that gave it away.
    """
    name = workspaces.active()
    return f"{APP_NAME} {__version__}" + ("" if name == workspaces.DEFAULT else f" - {name}")


#: A pane pane is named after the pane it shows, so that reopening the
#: workspace reopens the same custom_panes and two custom_panes are two docks.
CUSTOM_PREFIX = "custom:"


def custom_instance(name: str) -> str:
    return f"{CUSTOM_PREFIX}{name}"


def custom_name(instance: str) -> str:
    return instance[len(CUSTOM_PREFIX) :] if instance.startswith(CUSTOM_PREFIX) else instance


# Bumped whenever the set of docks changes.  restoreState declines a state
# saved under a different version, so an old layout is replaced by the current
# default instead of being restored with panes missing.
LAYOUT_VERSION = 4

#: Open on a first run.  Everything else is one click away in the View menu:
#: nine panes at once is a wall, and which of the protocol panes you want
#: depends entirely on what you have plugged in.
DEFAULT_VISIBLE = ("trace", "log", "scope")


class MainWindow(QMainWindow):
    #: Open this workspace instead.  Emitted rather than acted on, because a
    #: switch is a full reload and the window that asks is the one that goes:
    #: something outside it has to close it and open the next.
    reopen_requested = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(window_title())
        self.resize(1400, 900)
        self.setDockNestingEnabled(True)  # full grid layouts, not just the four edges

        #: Every channel.  ``self.bus`` is whichever one is selected, wearing a
        #: single bus's interface, so the protocol stacks need not know about
        #: channels at all.
        self.channels = Channels()
        self.bus = ActiveBus(self.channels)

        # --- log pane first: everything else reports into it -----------------
        #: True from the moment the window starts closing, so that what is
        #: torn down on the way out is not reported as if somebody did it.
        self._closing = False
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        #: Every line said to the user arrives here with a level, and this is
        #: the only thing that writes to the pane.  Warnings and errors open it
        #: if it has been closed; notes do not.
        self.events = EventLog()
        self._surfacing = False
        self.events.posted.connect(self._on_event)

        # --- user context, hooks, protocol managers -------------------------
        self.ctx = Context(events=self.events)
        #: python-can says everything through the logging module and nothing
        #: through return values -- a wrong bitrate is reported there and
        #: nowhere else, so without this it looks like an idle bus.
        self.log_bridge = LogBridge(self.events.post)
        #: Started with pythonw, which has no console, so a traceback from a
        #: Qt slot would otherwise go nowhere at all -- see the module.
        self.exceptions = ExceptionLogger(self.events.post)
        self.exceptions.install()
        self.hooks = Hooks(self.ctx)
        #: Shared so that agreeing once covers connecting, transmitting and
        #: replaying rather than each asking again.
        self.confirm = Confirmations()
        BACKENDS.load_user_backends(
            self.ctx.backends_dir, self.events.information, self.events.warning
        )

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

        # --- panes -----------------------------------------------------------
        #: Every dock, and how many of each kind there can be.  The window
        #: names a pane to open it and then leaves it alone: a second trace
        #: costs a registration rather than a rewrite, and a plugin or a
        #: workspace opens one through the same door.
        #: What names a frame in the trace.  A list rather than four calls
        #: on each trace, so that a plugin can join in and every trace --
        #: including one opened later -- agrees about what things are called.
        self._labellers = [
            self.dbc.message_name,
            self.uds.classify,
            self.j1939.classify,
            self.xcp.classify,
        ]
        self.panes = Panes(self, self.ctx)
        self.panes.pane_shown.connect(self._on_pane_shown)
        self._register_panes()
        # The first of each kind is named after its kind, so every layout and
        # setting written before any of this existed still names its own pane.
        self.trace = self.panes.view(self.panes.add("trace"))
        self.scope = self.panes.view(self.panes.add("scope"))
        # The two halves stay reachable by name: the Python console and the
        # docs refer to window.signals_view and window.plot.
        self.signals_view = self.scope.signals_view
        self.plot = self.scope.plot
        # Opened in the order the View menu should list them, which is by what
        # they are for: what is on the bus and what you put on it, then the
        # protocol panes, then what the tool has to say for itself.
        self.tx = self.panes.view(self.panes.add("tx"))
        self.canopen_view = self.panes.view(self.panes.add("canopen"))
        self.canopen_view.add_to_custom_pane.connect(self._add_to_custom_pane)
        self.uds_view = self.panes.view(self.panes.add("uds"))
        self.j1939_view = self.panes.view(self.panes.add("j1939"))
        self.xcp_view = self.panes.view(self.panes.add("xcp"))
        self.panes.add("log")
        self.ascii = self.panes.view(self.panes.add("ascii"))
        self.console = self.panes.view(self.panes.add("console"))
        self._arrange_default()

        self.setStatusBar(QStatusBar())
        self._frame_count = 0
        self._status_timer = QTimer(self, interval=500, timeout=self._update_status)
        self._status_timer.start()

        # --- wiring ----------------------------------------------------------
        self.channels.frames.connect(self._decode_frames)
        self.recorder.state.connect(self._on_record_state)
        self.recorder.error.connect(self.events.error)
        self.recorder.note.connect(self.events.information)
        self.canopen.rpdos_read.connect(lambda _n: self._refresh_transmit_sources())
        self.canopen.pdo_update.connect(self._on_pdo_update)
        self.channels.frames.connect(self._count_frames)
        self.channels.state_changed.connect(self._on_channel_state)
        # Queued: disconnected is emitted *before* the bus is torn down, so
        # asking straight away would still see it connected.
        self.channels.state_changed.connect(lambda *_a: self._sync_demo(), Qt.QueuedConnection)
        self.channels.error.connect(self._on_error)
        self.channels.note.connect(self.events.information)
        self.channels.warning.connect(self.events.warning)

        # --- menus & layout persistence --------------------------------------
        file_menu = self.menuBar().addMenu("&File")
        file_menu.addAction("Load DBC...", self._load_dbc_dialog)
        file_menu.addAction("Unload all DBCs", self._unload_dbcs)
        file_menu.addSeparator()
        export = file_menu.addAction("Export signals...", self._export_signals)
        export.setToolTip(
            "Write every decoded signal to a CSV: DBC signals, CANopen PDO\n"
            "values and XCP measurements alike.  Recording writes raw CAN,\n"
            "which means decoding it again elsewhere to get back what is\n"
            "already on screen here."
        )
        file_menu.addSeparator()
        self.workspace_menu = WorkspaceMenu(self, self.ctx)
        self.workspace_menu.install(file_menu)
        self.workspace_menu.switch_requested.connect(self._switch_workspace)
        file_menu.addSeparator()
        quit_action = file_menu.addAction("Exit", self.close)
        quit_action.setMenuRole(QAction.QuitRole)  # the Apple menu, where there is one
        quit_action.setShortcut(QKeySequence.Quit)
        for path in self.ctx.settings.get("dbc.paths", []):
            self._load_dbc(path)
        if (a2l := self.ctx.settings.get("xcp.a2l")) and Path(a2l).exists():
            try:
                self.xcp.load_a2l(a2l)
            except Exception as exc:
                self.events.warning(f"A2L load failed: {exc}")

        self.view_menu = self.menuBar().addMenu("&View")
        self._build_view_menu()
        # Deferred by a turn of the loop: adding or removing a pane is usually
        # this menu's own action doing it, and clearing a menu while it is
        # delivering a click is not somewhere to be.
        self.panes.changed.connect(lambda: QTimer.singleShot(0, self._build_view_menu))

        self._demo: DemoDevice | None = None
        tools_menu = self.menuBar().addMenu("&Tools")
        tools_menu.addAction("Open hooks folder", self._open_hooks_folder)
        tools_menu.addAction("Open backends folder", self._open_backends_folder)
        tools_menu.addAction("Reload hooks", self._reload_hooks)
        tools_menu.addAction("Update hook stubs", self._update_hook_stubs)
        tools_menu.addSeparator()
        forget = tools_menu.addAction("Forget remembered folders", self._forget_folders)
        forget.setToolTip(
            "A file dialog opens where that sort of file was last used -- an EDS\n"
            "where the last EDS was, a firmware image where the last image was.\n"
            "This puts them all back to pycangui's own folders."
        )
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

        #: A menu of its own rather than a corner of Tools: a plugin adds
        #: screens and commands, and Tools is where the tool's own settings
        #: live.  It is also the answer to "what have I got installed", which
        #: is not a question Tools would ever be asked.
        self.plugins_menu = self.menuBar().addMenu("&Plugins")
        self.plugins_menu.setToolTipsVisible(True)

        self.help_menu = HelpMenu(self)
        # Loaded after the menus exist, because a plugin may add entries to
        # them, and before the layout is restored, because a plugin's pane has
        # to exist for restoreState to be able to put it back where it was.
        self._plugin_menus: dict[str, QMenu] = {}
        self.plugins = Plugins(
            folders=[builtin_plugins_dir(), self.ctx.workspace_dir / "plugins"],
            make_app=lambda name: PluginApp(self, name),
            log=self.events.information,
            warn=self.events.warning,
        )
        self.plugins.load_all()
        self._build_plugins_menu()
        self.panes.restore_instances()
        self._build_view_menu()
        self._default_state = self.saveState(LAYOUT_VERSION)
        self._restore_layout()
        self.panes.restore_state()

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

    # --- panes ---------------------------------------------------------------
    def _register_panes(self) -> None:
        """What sorts of pane there are, and which of them there can be several of.

        A trace and a plot earn a second instance and the rest do not, and the
        test is what a second one would show: a trace filtered differently, or
        a plot of other signals, is a different view of the same capture.  A
        second Event Log is the same log twice, a second UDS pane is two faces
        on one session, and a second transmit list is a question about which
        of them is sending.
        """
        for kind in (
            PaneKind(
                # "CAN Trace" rather than "Trace": worth saying what is being
                # traced.  Not "Raw", which would undersell a pane that names
                # the protocol and the DBC message of every frame; and not
                # "Message", because what it lists is frames -- error frames
                # included, and those are not messages at all.
                "trace",
                "CAN Trace",
                Qt.LeftDockWidgetArea,
                self._new_trace,
                several=True,
                shutdown=self._drop_trace,
            ),
            PaneKind(
                "scope",
                "Signals and Plot",
                Qt.LeftDockWidgetArea,
                lambda _name: ScopeView(self.signals, self.bus.now, self.ctx),
                several=True,
            ),
            PaneKind(
                "tx",
                "Transmit",
                Qt.BottomDockWidgetArea,
                self._new_transmit,
                several=True,
            ),
            PaneKind(
                "canopen",
                "CANopen",
                Qt.RightDockWidgetArea,
                lambda _name: CanopenView(self.canopen, self.hooks, self.ctx),
            ),
            PaneKind(
                "uds",
                "UDS",
                Qt.RightDockWidgetArea,
                lambda _name: UdsView(self.uds, self.ctx, self.confirm),
            ),
            PaneKind(
                "j1939",
                "J1939",
                Qt.RightDockWidgetArea,
                lambda _name: J1939View(self.j1939, self.ctx),
            ),
            PaneKind(
                "xcp",
                "XCP",
                Qt.RightDockWidgetArea,
                lambda _name: XcpView(self.xcp, self.ctx),
            ),
            PaneKind(
                "custom",
                "Custom pane",
                Qt.RightDockWidgetArea,
                self._new_custom_pane,
                several=True,
                named=True,
            ),
            PaneKind("log", "Event Log", Qt.BottomDockWidgetArea, lambda _name: self.log),
            PaneKind(
                # The kind keeps its old name: it is the dock's objectName, and
                # it is what the saved streams are filed under.  Only the title
                # changes, which is the part anybody reads.
                "ascii",
                "ASCII Log",
                Qt.BottomDockWidgetArea,
                lambda _name: AsciiView(self.channels, self.ctx),
            ),
            PaneKind(
                "console",
                "Python Console",
                Qt.BottomDockWidgetArea,
                lambda _name: ConsoleView(self._console_namespace(), self.ctx),
            ),
        ):
            self.panes.register(kind)

    def _new_trace(self, name: str) -> TraceView:
        """A trace, fed the capture and told which settings are its own."""
        view = TraceView(self.hooks, self.ctx, key=name)
        view.classifiers.extend(self._labellers)
        self.channels.frames.connect(view.on_frames)
        return view

    def add_trace_labeller(self, labeller) -> None:
        """Name frames in every trace, the ones open and the ones opened later.

        Kept here rather than on a trace, because a second trace showing
        different names from the first would be a puzzle rather than a feature.
        """
        if labeller in self._labellers:
            return
        self._labellers.append(labeller)
        for name in self.panes.instances("trace"):
            if (view := self.panes.view(name)) is not None:
                view.classifiers.append(labeller)

    def remove_trace_labeller(self, labeller) -> None:
        if labeller in self._labellers:
            self._labellers.remove(labeller)
        for name in self.panes.instances("trace"):
            view = self.panes.view(name)
            if view is not None and labeller in view.classifiers:
                view.classifiers.remove(labeller)

    def _new_custom_pane(self, instance: str) -> CustomPaneView:
        """One custom pane, by the name its file is kept under.

        The instance name carries it -- ``pane:Battery limits`` -- so that the
        workspace reopens the same custom_panes it was closed with, and two custom_panes
        are two docks rather than one pane with a selector in it.
        """
        name = custom_name(instance)
        pane = custom_model.load(name) or custom_model.CustomPane(title=name)
        view = CustomPaneView(
            name, pane, self.canopen, self.ctx, signals=self.signals, now=self.bus.now
        )
        view.changed.connect(lambda n=name: self._on_custom_pane_changed(n))
        return view

    def open_custom_pane(self, name: str) -> str:
        """Open a custom pane by name, making an empty one if there is no file yet."""
        pane = custom_model.load(name)
        if pane is None:
            pane = custom_model.CustomPane(title=name)
            custom_model.save(name, pane)
        return self.panes.add(
            "custom",
            name=custom_instance(name),
            title=pane.title or name,
            show=True,
            floating=True,
        )

    def _custom_pane_view(self, name: str) -> CustomPaneView | None:
        view = self.panes.view(custom_instance(name))
        return view if isinstance(view, CustomPaneView) else None

    def _on_custom_pane_changed(self, name: str) -> None:
        """A custom pane renamed itself, so its dock should say so too."""
        view = self._custom_pane_view(name)
        dock = self.panes.docks.get(custom_instance(name))
        if view is not None and dock is not None:
            dock.setWindowTitle(view.pane.title or name)

    def _new_custom_pane_dialog(self) -> None:
        name, chose = QInputDialog.getText(self, "New custom pane", "A name for it:")
        if not chose:
            return
        if (reason := custom_model.why_not(name)) != "":
            self.events.warning(f"New custom pane: {reason}")
            return
        self.open_custom_pane(name.strip())
        self.events.information(
            f"Custom pane {name.strip()} created.  Add objects to it from the CANopen "
            "pane: select them in the object dictionary and use Add to a custom pane."
        )

    @Slot(str, object)
    def _add_to_custom_pane(self, name: str, chosen) -> None:
        """Put the objects picked in the object dictionary onto a custom pane.

        Asked once for the whole selection: somebody adding six related
        parameters means one pane, and being asked six times what to call it
        would be its own argument against the feature.
        """
        items = list(chosen or [])
        if not items:
            return
        if not name:
            new, chose = QInputDialog.getText(self, "New custom pane", "A name for it:")
            if not chose:
                return
            if (reason := custom_model.why_not(new)) != "":
                self.events.warning(f"New custom pane: {reason}")
                return
            name = new.strip()
        self.open_custom_pane(name)
        view = self._custom_pane_view(name)
        if view is None:
            return
        for item in items:
            view.add_field(item)
        how_many = "object" if len(items) == 1 else f"{len(items)} objects"
        self.events.information(f"Added {how_many} to {name}")

    def _new_transmit(self, name: str) -> TxView:
        """A transmit list, keeping its own messages.

        A second one is a real thing to want: the background traffic a rig
        needs left running, and a scratch list to try something in, without
        the two being the same list.
        """
        view = TxView(self.bus, self.ctx, self.dbc, self.canopen, self.confirm, key=name)
        view.stop_all_requested.connect(self._stop_all_transmits)
        return view

    @Slot(str, bool)
    def _on_pane_shown(self, name: str, shown: bool) -> None:
        """A transmit pane that has been put away stops transmitting.

        Frames arriving on a live bus from a pane nobody can see is the hardest
        sort of fault to find, because nothing on screen accounts for them.  So
        closing one stops its cyclic messages -- and bringing it back does not
        start them again, since beginning to transmit onto a bus is not
        something to do without being asked.

        Being tabbed behind another pane is not being put away, and neither is
        being detached into a window of its own; both are still on screen, and
        stopping a rig's traffic because somebody looked at the trace would be
        its own kind of unpleasant surprise.
        """
        if shown or self._closing or self.panes.kind_of(name) != "tx":
            return
        view = self.panes.view(name)
        if not isinstance(view, TxView) or not (stopped := view.cyclic_count()):
            return
        view.stop_all()
        title = self.panes.docks[name].windowTitle() if name in self.panes.docks else name
        self.events.information(
            f"{title} was closed with {stopped} cyclic message(s) sending, so they were stopped."
        )

    def _stop_all_transmits(self) -> None:
        """What Stop all cyclic promises: every list, not just the one pressed."""
        for view in self._transmit_panes():
            view.stop_all()

    def _transmit_panes(self) -> list[TxView]:
        return [
            view
            for name in self.panes.instances("tx")
            if isinstance(view := self.panes.view(name), TxView)
        ]

    def _refresh_transmit_sources(self) -> None:
        """A database or an RPDO configuration changed, so every list is stale."""
        for view in self._transmit_panes():
            view.refresh_sources()

    def _drop_trace(self, view: TraceView) -> None:
        """Stop feeding a trace that has been closed for good.

        Qt would drop the connection when the widget is deleted, but deletion
        is deferred: without this, frames go on arriving at a pane that is no
        longer on screen for as long as it takes the event loop to come round.
        """
        self.channels.frames.disconnect(view.on_frames)

    def _build_view_menu(self) -> None:
        """Every pane, and the two things you can do to the set of them."""
        self.view_menu.clear()
        for name in self.panes.names():
            self.view_menu.addAction(self.panes.docks[name].toggleViewAction())
        self.view_menu.addSeparator()

        # They are panes -- they open as docks and they are removed under
        # Remove pane -- and the only thing worth saying about them is that
        # you made them rather than the tool shipping them.  Hence "custom",
        # and no second word for a second concept that does not exist.
        custom_menu = self.view_menu.addMenu("Custom panes")
        custom_menu.setToolTipsVisible(True)
        for name in custom_model.names():
            action = custom_menu.addAction(name, lambda n=name: self.open_custom_pane(n))
            action.setToolTip("Open this custom pane, as a dock like any other.")
        if custom_model.names():
            custom_menu.addSeparator()
        made = custom_menu.addAction("New custom pane...", self._new_custom_pane_dialog)
        made.setToolTip(
            "A named group of objects laid out as a form.  Objects are added\n"
            "from the CANopen pane: select them in the object dictionary and\n"
            "use Add to a custom pane."
        )

        # Paired with Custom panes above: the ones pycangui comes with, and the
        # ones you built.  The verb is on each entry rather than on the
        # submenu, because "Add Trace" cannot be misread and no adjective for
        # the parent could be made to work -- "New" promises a new sort of
        # thing, "Duplicate" promises a copy of the one you are looking at, and
        # these arrive with settings of their own.
        standard = self.view_menu.addMenu("Standard panes")
        standard.setToolTipsVisible(True)
        # Only the ones there can be more than one of, so that the list is
        # things you can actually do.  Every pane, one of a kind included, is
        # already named at the top of this menu.
        standard.setToolTip("The panes pycangui comes with that you can have more than one of.")
        for kind in self.panes.kinds.values():
            if not kind.several or kind.named:
                continue
            action = standard.addAction(
                f"Add {kind.title}", lambda k=kind.name: self.panes.add(k, floating=True)
            )
            action.setToolTip(
                f"Open another {kind.title} pane, with settings of its own --\n"
                "its own filter, its own list, its own signals.\n"
                "It opens in a window of its own; drag it into the main window\n"
                "to dock it, or onto another pane to tab the two together."
            )

        extras = self.panes.extras()
        remove = self.view_menu.addMenu("Remove pane")
        remove.setEnabled(bool(extras))
        remove.setToolTipsVisible(True)
        for name in extras:
            action = remove.addAction(
                self.panes.docks[name].windowTitle(), lambda n=name: self.panes.remove(n)
            )
            action.setToolTip("Close this pane for good.  Closing its window only puts it away.")

        self.view_menu.addSeparator()
        self.view_menu.addAction("Dock all panes", self.panes.dock_all)
        self.view_menu.addAction("Reset layout", self._reset_layout)

    def _arrange_default(self) -> None:
        """The layout a first run opens with: the trace, the log, and the plot.

        Everything else starts hidden rather than removed -- the View menu
        lists every pane, and showing one puts it back in the area it was
        added to, so the protocol panes still arrive on the right.
        """
        trace, log, scope = (self.panes.docks[n] for n in DEFAULT_VISIBLE)
        # The trace and the log share the top row and the plot spans below
        # them: both of those want width, and the log's lines are short.
        # Nested splits inside one area rather than the four edges, which is
        # what setDockNestingEnabled above buys.
        self.addDockWidget(Qt.LeftDockWidgetArea, trace)
        self.splitDockWidget(trace, scope, Qt.Vertical)
        self.splitDockWidget(trace, log, Qt.Horizontal)
        for name, dock in self.panes.docks.items():
            dock.setVisible(name in DEFAULT_VISIBLE)
        self.resizeDocks([trace, log], [7, 3], Qt.Horizontal)
        self.resizeDocks([trace, scope], [6, 4], Qt.Vertical)

    def _restore_layout(self) -> None:
        # Where the window sits stays in QSettings: that belongs to this desk
        # and these monitors, and switching product should rearrange the panes
        # rather than move the window.
        if (geo := QSettings().value("geometry")) is not None:
            self.restoreGeometry(geo)
        state = self.ctx.layout.get("window")
        if state is None and self.ctx.workspace == workspaces.DEFAULT:
            # Where it lived before there were workspaces.  Only for default,
            # which is what an existing setup became: a new workspace that
            # inherited the last one's arrangement would not be a new one.
            state = QSettings().value("windowState")
        # restoreState declines a layout saved under an older LAYOUT_VERSION,
        # which leaves the default in place -- the same as never having run.
        if state is None or not self.restoreState(state, LAYOUT_VERSION):
            hidden = [
                d.windowTitle() for n, d in self.panes.docks.items() if n not in DEFAULT_VISIBLE
            ]
            self.events.information(
                f"Panes for {', '.join(hidden)} are hidden to start with: "
                "turn any of them on in the View menu."
            )
            # The virtual channel is the default, and it is empty until
            # something fills it -- which is not obvious from looking at it.
            self.events.information(
                "No hardware?  Connect on the virtual channel and switch on "
                "Tools > Demo CANopen device to have something to look at."
            )
        self.panes.restore_view_states()

    @Slot(str)
    def _switch_workspace(self, name: str) -> None:
        """Open another workspace, once whoever is on a bus has agreed to it.

        A workspace holds which channels at what bitrate, so opening one means
        closing the channels this one has -- which is dropping off a live bus,
        and stopping whatever was being sent cyclically onto it.  Worth a
        question, and only when there is something to lose.
        """
        if name == self.ctx.workspace or not workspaces.exists(name):
            return
        if self.channels.any_connected:
            connected = ", ".join(
                n
                for n in self.channels.names()
                if (bus := self.channels.get(n)) is not None and bus.is_connected
            )
            agreed = self.confirm.ask(
                self,
                "workspace-switch",  # agreed once a session: it is the same loss each time
                "Close the bus and open another workspace?",
                SWITCH_WHILE_CONNECTED.format(name=connected, target=name),
            )
            if not agreed:
                return
        self.reopen_requested.emit(name)

    def _reset_layout(self) -> None:
        self.restoreState(self._default_state, LAYOUT_VERSION)

    def closeEvent(self, event) -> None:
        # Everything is about to be hidden, and reporting each pane going away
        # on the way out would be a paragraph nobody asked for.
        self._closing = True
        QSettings().setValue("geometry", self.saveGeometry())
        self.ctx.layout.set("window", bytes(self.saveState(LAYOUT_VERSION)))
        self.panes.save_view_states()
        # Saved before they are closed: closing one puts its pane away, and
        # what is saved should be how things were left, not how they were
        # tidied up.
        self.panes.save()
        # Parentless windows of their own, so they would keep the application
        # running after the main window had gone.
        self.panes.close_detached()
        # And the same for any ASCII stream popped out into a window.
        self.ascii.shutdown()
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
    @Slot(str, str)
    def _on_event(self, message: str, level: str) -> None:
        """The only thing that writes to the Event Log pane.

        Which makes it the only place that would have to change to give
        warnings and errors a colour of their own.
        """
        self.log.appendPlainText(message)
        if level in PROBLEMS:
            self._surface_log()

    def _surface_log(self) -> None:
        """Open the Event Log, because something in it needs reading.

        Deferred by a turn of the event loop for two reasons: a problem raised
        while the window is still being built would otherwise be undone by the
        saved layout, which is restored afterwards; and a burst of them -- one
        per row, when Cyclic is ticked on a selection with no bus connected --
        should cost one show rather than twenty.
        """
        if self._surfacing:
            return
        self._surfacing = True
        QTimer.singleShot(0, self._show_log)

    def _show_log(self) -> None:
        self._surfacing = False
        panes = getattr(self, "panes", None)
        dock = None if panes is None else panes.docks.get("log")
        if dock is None or "log" in panes.detached:
            return  # too early to have a pane, or it has a window of its own
        if not dock.isVisible():
            dock.show()
        dock.raise_()  # it may be docked but tabbed behind another pane

    @Slot(str)
    def _on_error(self, text: str) -> None:
        self.events.error(f"ERROR: {text}")

    def _export_signals(self) -> None:
        """Write the captured signal values where a spreadsheet can read them."""
        if not self.signals.keys():
            self.events.warning("Export signals: nothing has been decoded yet")
            return
        path = folders.save_file(
            self,
            self.ctx,
            folders.EXPORT,
            "Export signals",
            "CSV (*.csv);;All files (*)",
            self.ctx.user_dir,
            suggested="signals.csv",
        )
        if not path:
            return
        series = [s for key in self.signals.keys() if (s := self.signals.get(key)) is not None]
        try:
            count, rows = write_csv(path, series)
        except OSError as exc:
            self.events.warning(f"Export signals failed: {exc}")
            return
        if not count:
            # Signals exist but none has a value yet: an empty file with a row
            # of headings looks like a successful export of nothing.
            self.events.warning("Export signals: no samples to write yet")
            return
        self.events.information(f"Exported {count} signal(s), {rows} row(s) to {path}")

    @Slot(bool)
    def _set_strict_dbc(self, on: bool) -> None:
        self.ctx.settings.set("dbc.strict", on)
        self.events.information(
            "DBC files will be checked strictly, and you will be asked about one that fails."
            if on
            else "DBC files will be loaded without the strict checks."
        )

    @Slot(bool)
    def _set_verbose_logging(self, on: bool) -> None:
        self.log_bridge.set_verbose(on)
        self.events.information(
            f"Verbose CAN logging {'on' if on else 'off'}: the CAN libraries' "
            f"{'info messages are' if on else 'warnings and errors are still'} relayed here."
        )

    def _forget_folders(self) -> None:
        """Put every file dialog back to the folder it started life in."""
        how_many = folders.forget_all(self.ctx)
        self.events.information(
            f"Forgot {how_many} remembered folder(s): file dialogs will open in "
            "pycangui's own folders again."
            if how_many
            else "No folders were being remembered."
        )

    def _open_hooks_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.ctx.hooks_dir)))

    def _open_backends_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.ctx.backends_dir)))

    def plugin_menu(self, plugin: str) -> QMenu:
        """Where a plugin's menu entries go: Plugins > its own name.

        Grouped by plugin rather than pooled, so that a menu entry says whose
        it is -- which matters most when one of them is misbehaving.  Parented
        to the window rather than to the Plugins menu, because that menu is
        cleared and rebuilt and would take the submenus with it.
        """
        menu = self._plugin_menus.get(plugin)
        if menu is None:
            menu = self._plugin_menus[plugin] = QMenu(plugin, self)
            menu.setToolTipsVisible(True)
        return menu

    def drop_plugin_menu(self, plugin: str) -> None:
        menu = self._plugin_menus.pop(plugin, None)
        if menu is not None:
            self.plugins_menu.removeAction(menu.menuAction())
            menu.deleteLater()

    def _build_plugins_menu(self) -> None:
        """Every plugin installed, then how to reload them and where they live.

        Every one appears whether or not it added a menu entry, because this
        menu is also the answer to what is installed -- and one that failed to
        load appears too, disabled, since a plugin that is silently absent is
        the hardest kind of missing to notice.
        """
        self.plugins_menu.clear()
        for record in sorted(self.plugins.loaded.values(), key=lambda r: r.label.lower()):
            if not record.ok:
                failed = self.plugins_menu.addAction(f"{record.label} (failed to load)")
                failed.setEnabled(False)
                failed.setToolTip("Why is in the Event Log.")
                continue
            menu = self.plugin_menu(record.name)
            menu.setTitle(record.label)
            if not menu.actions():
                nothing = menu.addAction(record.description or "Adds no menu entries.")
                nothing.setEnabled(False)
            self.plugins_menu.addMenu(menu)
        if not self.plugins.loaded:
            none = self.plugins_menu.addAction("No plugins installed")
            none.setEnabled(False)
            none.setToolTip("A plugin is a folder with a plugin.py in it; see the manual.")

        self.plugins_menu.addSeparator()
        reload_action = self.plugins_menu.addAction("Reload plugins", self._reload_plugins)
        reload_action.setToolTip(
            "Load the plugin files again.  Whatever a plugin added last time is\n"
            "taken away first, so editing one and reloading is how it gets\n"
            "written -- there is no need to restart."
        )
        folder = self.plugins_menu.addAction("Open plugins folder", self._open_plugins_folder)
        folder.setToolTip("Where this workspace's plugins live.")

    def _reload_plugins(self) -> None:
        self.plugins.load_all()
        bad = self.plugins.errors()
        say = self.events.warning if bad else self.events.information
        say(
            f"Plugins reloaded ({len(self.plugins.working())} working"
            + (f", {len(bad)} failed, see above)" if bad else ")")
        )
        self._build_plugins_menu()
        self._build_view_menu()

    def _open_plugins_folder(self) -> None:
        folder = self.ctx.workspace_dir / "plugins"
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _reload_hooks(self) -> None:
        self.hooks.reload()
        bad = self.hooks.errors()
        say = self.events.warning if bad else self.events.information
        say("Hooks reloaded" + (f" ({len(bad)} file(s) failed, see above)" if bad else ""))

    def _update_hook_stubs(self) -> None:
        added = self.hooks.update_stubs()
        if added:
            for module, names in added.items():
                self.events.information(f"hooks/{module}.py: added {', '.join(names)}")
            self.hooks.reload()
        else:
            self.events.information("Hook files already up to date")

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
                self.events.warning(f"Demo device failed to start: {exc}")
                return
            self.events.information(
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
        self,
        interface: str,
        channel: str,
        bitrate: int,
        fd: bool,
        data_bitrate: int = 0,
        extra: dict | None = None,
    ) -> None:
        bus = self.channels.active_bus()
        if bus is None:
            self.events.warning("No channel selected")
            return
        if not self._may_connect(bus.channel_name, interface, channel, bitrate, fd, extra):
            self.connect_bar.set_connected(False)
            return
        bus.connect_bus(interface, channel, bitrate, fd, extra, data_bitrate)
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
            self.events.information(f"{name} connected: {bus.description}")
        else:
            self.events.information(f"{name} disconnected")

    # --- recording -----------------------------------------------------------
    @Slot(bool)
    def _toggle_record(self, on: bool) -> None:
        if not on:
            self.recorder.stop()
            return
        path = folders.save_file(
            self,
            self.ctx,
            folders.LOG,
            "Record to a log file",
            WRITE_FILTER,
            self.ctx.user_dir,
            suggested="capture.blf",
        )
        if not path or not self.recorder.start(path):
            self.record_action.setChecked(False)

    @Slot(bool, str)
    def _on_record_state(self, recording: bool, path: str) -> None:
        self.record_action.blockSignals(True)
        self.record_action.setChecked(recording)
        self.record_action.setText("Recording..." if recording else "Record")
        self.record_action.blockSignals(False)
        self.events.information(f"Recording to {path}" if recording else "Recording stopped")

    # --- DBC / signals -------------------------------------------------------
    def _load_dbc_dialog(self) -> None:
        path = folders.open_file(
            self,
            self.ctx,
            folders.DBC,
            "Load CAN database",
            "CAN databases (*.dbc *.kcd *.sym *.arxml)",
            self.ctx.user_dir,
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
            self.events.warning(f"DBC load failed: {path}: {exc}")
            if not strict or not offer_relaxing or not self._offer_relaxed_load(path, exc):
                return False
            try:
                db = self.dbc.load(path, strict=False)
            except Exception as exc2:
                self.events.warning(f"DBC load failed even unchecked: {path}: {exc2}")
                return False
            self.events.information(f"Loaded {path} without the strict checks")
        how = "" if strict else " (strict checks off)"
        self.events.information(f"Loaded {path}: {len(db.messages)} messages{how}")
        if hasattr(self, "tx"):
            self._refresh_transmit_sources()
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
        self._refresh_transmit_sources()
        self.events.information("DBC databases unloaded")

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
