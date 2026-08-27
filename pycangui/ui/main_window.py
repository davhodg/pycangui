"""Main window: connect bar, dockable panes, persistent layout."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QTimer, QUrl, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDockWidget,
    QFileDialog,
    QMainWindow,
    QPlainTextEdit,
    QScrollArea,
    QStatusBar,
)

from pycangui import APP_NAME, __version__
from pycangui.canopen.manager import CanopenManager
from pycangui.core.backends import BACKENDS
from pycangui.core.channels import ActiveBus, Channels
from pycangui.core.context import Context
from pycangui.core.dbc import DbcDecoder
from pycangui.core.demo import DemoDevice
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
from pycangui.ui.help_menu import HelpMenu
from pycangui.ui.j1939_view import J1939View
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
        self.trace = TraceView(self.hooks, self.ctx)
        self.trace.classifiers.append(self.dbc.message_name)
        self.trace.classifiers.append(self.uds.classify)
        self.trace.classifiers.append(self.j1939.classify)
        self.trace.classifiers.append(self.xcp.classify)
        self._add_dock("trace", "Trace", self.trace, Qt.LeftDockWidgetArea)
        self.scope = ScopeView(self.signals, self.bus.now)
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
        view_menu.addAction("Reset layout", self._reset_layout)

        tools_menu = self.menuBar().addMenu("&Tools")
        self._demo: DemoDevice | None = None
        self._demo_action = tools_menu.addAction("Demo CANopen device (virtual bus)")
        self._demo_action.setCheckable(True)
        self._demo_action.toggled.connect(self._toggle_demo)
        tools_menu.addSeparator()
        tools_menu.addAction("Open hooks folder", self._open_hooks_folder)
        tools_menu.addAction("Open backends folder", self._open_backends_folder)
        tools_menu.addAction("Reload hooks", self._reload_hooks)
        tools_menu.addAction("Update hook stubs", self._update_hook_stubs)
        tools_menu.addSeparator()
        verbose = tools_menu.addAction("Verbose CAN logging")
        verbose.setCheckable(True)
        verbose.setToolTip("Relay the CAN libraries' info messages to the event log too")
        verbose.toggled.connect(self._set_verbose_logging)

        self.help_menu = HelpMenu(self)
        self._default_state = self.saveState(LAYOUT_VERSION)
        self._restore_layout()

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
        dock.setWidget(scroll)
        self.addDockWidget(area, dock)
        return dock

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
        self.scope.restore_state(s.value("scopeSplitter"))

    def _reset_layout(self) -> None:
        self.restoreState(self._default_state, LAYOUT_VERSION)

    def closeEvent(self, event) -> None:
        s = QSettings()
        s.setValue("geometry", self.saveGeometry())
        s.setValue("windowState", self.saveState(LAYOUT_VERSION))
        s.setValue("scopeSplitter", self.scope.save_state())
        self.replay.stop()
        self.help_menu.shutdown()
        self.connect_bar.shutdown()
        self.log_bridge.detach()
        self.exceptions.remove()
        self.recorder.stop()
        self._demo_action.setChecked(False)  # stops and shuts down the demo device
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

    @Slot(bool)
    def _toggle_demo(self, on: bool) -> None:
        if on:
            if self.connect_bar.interface.currentText() != "virtual":
                self.log.appendPlainText("The demo device only works on the virtual interface")
                self._demo_action.setChecked(False)
                return
            try:
                # current_channel(), not the widget's text: the box shows a
                # label, which for a detected adapter is not the channel.
                self._demo = DemoDevice(self.connect_bar.current_channel(), self)
            except Exception as exc:
                # A slot that raises leaves the menu ticked and the user with
                # nothing but a traceback on a console they cannot see.
                self.log.appendPlainText(f"Demo device failed to start: {exc}")
                self._demo_action.setChecked(False)
                return
            self.log.appendPlainText("Demo device started: node 5, heartbeat 500 ms, TPDO1 100 ms")
        elif self._demo is not None:
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
        # The device identity is part of the key: with two adapters attached,
        # agreeing to channel 0 on one is not agreeing to channel 0 on the other.
        identity = ":".join(f"{k}={v}" for k, v in sorted((extra or {}).items()))
        where = f"{interface}:{channel}" + (f" [{identity}]" if identity else "")
        return self.confirm.ask(
            self,
            f"connect:{name}:{where}:{bitrate}:{fd}",
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
        if path and self._load_dbc(path):
            paths = list(self.ctx.settings.get("dbc.paths", []))
            if path not in paths:
                self.ctx.settings.set("dbc.paths", [*paths, path])

    def _load_dbc(self, path: str) -> bool:
        try:
            db = self.dbc.load(path)
        except Exception as exc:  # cantools parse errors come in many types
            self.log.appendPlainText(f"DBC load failed: {path}: {exc}")
            return False
        self.log.appendPlainText(f"Loaded {path}: {len(db.messages)} messages")
        if hasattr(self, "tx"):
            self.tx.refresh_sources()
        return True

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
