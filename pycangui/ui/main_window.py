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
from pycangui.core.bus import BusManager
from pycangui.core.context import Context
from pycangui.core.dbc import DbcDecoder
from pycangui.core.demo import DemoDevice
from pycangui.core.hooks import Hooks
from pycangui.core.logging import WRITE_FILTER, Recorder
from pycangui.core.signals import SignalHub
from pycangui.j1939.manager import J1939Manager
from pycangui.uds.manager import UdsManager
from pycangui.ui.canopen_view import CanopenView
from pycangui.ui.connect_bar import ConnectBar
from pycangui.ui.console_view import ConsoleView
from pycangui.ui.j1939_view import J1939View
from pycangui.ui.plot_view import PlotView
from pycangui.ui.replay_view import ReplayView
from pycangui.ui.signals_view import SignalsView
from pycangui.ui.trace_view import TraceView
from pycangui.ui.tx_view import TxView
from pycangui.ui.uds_view import UdsView
from pycangui.ui.xcp_view import XcpView
from pycangui.xcp.manager import XcpManager


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1400, 900)
        self.setDockNestingEnabled(True)  # full grid layouts, not just the four edges

        self.bus = BusManager()

        # --- toolbar ---------------------------------------------------------
        self.connect_bar = ConnectBar()
        self.addToolBar(self.connect_bar)
        self.connect_bar.connect_requested.connect(self.bus.connect_bus)
        self.connect_bar.disconnect_requested.connect(self.bus.disconnect_bus)
        self.record_action = self.connect_bar.addAction("Record")
        self.record_action.setCheckable(True)
        self.record_action.setToolTip("Record everything on the bus to a log file")
        self.record_action.toggled.connect(self._toggle_record)

        # --- log pane first: everything else reports into it -----------------
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)

        # --- user context, hooks, protocol managers -------------------------
        self.ctx = Context(log=self.log.appendPlainText)
        self.hooks = Hooks(self.ctx)
        BACKENDS.load_user_backends(self.ctx.backends_dir, self.log.appendPlainText)
        self.canopen = CanopenManager(self.bus)
        self.uds = UdsManager(self.bus, self.hooks, self.ctx)
        self.j1939 = J1939Manager(self.bus, self.hooks)
        self.signals = SignalHub()
        self.dbc = DbcDecoder()
        self.xcp = XcpManager(self.bus, self.hooks, self.signals, self.ctx)
        self.recorder = Recorder(self.bus)

        # --- docks -----------------------------------------------------------
        self.trace = TraceView(self.hooks, self.ctx)
        self.trace.classifiers.append(self.dbc.message_name)
        self.trace.classifiers.append(self.uds.classify)
        self.trace.classifiers.append(self.j1939.classify)
        self.trace.classifiers.append(self.xcp.classify)
        self._add_dock("trace", "Trace", self.trace, Qt.LeftDockWidgetArea)
        self.signals_view = SignalsView(self.signals)
        self._add_dock("signals", "Signals", self.signals_view, Qt.LeftDockWidgetArea)
        self.plot = PlotView(self.signals, self.bus.now)
        self._add_dock("plot", "Plot", self.plot, Qt.LeftDockWidgetArea)
        self.signals_view.plot_toggled.connect(self.plot.set_plotted)
        self.canopen_view = CanopenView(self.canopen, self.hooks, self.ctx)
        self._add_dock("canopen", "CANopen", self.canopen_view, Qt.RightDockWidgetArea)
        self.uds_view = UdsView(self.uds, self.ctx)
        self._add_dock("uds", "UDS", self.uds_view, Qt.RightDockWidgetArea)
        self.j1939_view = J1939View(self.j1939, self.ctx)
        self._add_dock("j1939", "J1939", self.j1939_view, Qt.RightDockWidgetArea)
        self.xcp_view = XcpView(self.xcp, self.ctx)
        self._add_dock("xcp", "XCP", self.xcp_view, Qt.RightDockWidgetArea)
        self.tx = TxView(self.bus, self.ctx, self.dbc, self.canopen)
        self.replay = ReplayView(self.bus, self.ctx)
        self._add_dock("replay", "Replay", self.replay, Qt.BottomDockWidgetArea)
        self._add_dock("tx", "Transmit", self.tx, Qt.BottomDockWidgetArea)
        self._add_dock("log", "Event Log", self.log, Qt.BottomDockWidgetArea)
        self.console = ConsoleView(self._console_namespace(), self.ctx)
        self._add_dock("console", "Python Console", self.console, Qt.BottomDockWidgetArea)

        self.setStatusBar(QStatusBar())
        self._frame_count = 0
        self._status_timer = QTimer(self, interval=500, timeout=self._update_status)
        self._status_timer.start()

        # --- wiring ----------------------------------------------------------
        self.bus.frames.connect(self.trace.on_frames)
        self.bus.frames.connect(self._decode_frames)
        # An offline replay feeds the same consumers as the bus does
        self.replay.frames_replayed.connect(self.trace.on_frames)
        self.replay.frames_replayed.connect(self._decode_frames)
        self.replay.frames_replayed.connect(self._count_frames)
        self.recorder.state.connect(self._on_record_state)
        self.recorder.error.connect(self.log.appendPlainText)
        self.canopen.rpdos_read.connect(lambda _n: self.tx.refresh_sources())
        self.canopen.pdo_update.connect(self._on_pdo_update)
        self.bus.frames.connect(self._count_frames)
        self.bus.connected.connect(self._on_connected)
        self.bus.disconnected.connect(self._on_disconnected)
        self.bus.error.connect(self._on_error)

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
        self._default_state = self.saveState()
        self._restore_layout()

    # --- helpers -------------------------------------------------------------
    def _console_namespace(self) -> dict:
        """What scripts and the console see.  Keep names stable: users rely on them."""

        def send(can_id: int, data, ext: bool = False, fd: bool = False) -> None:
            self.bus.send(can_id, bytes(data), extended=ext, fd=fd)

        return {
            "ctx": self.ctx,
            "bus": self.bus,
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

    def _restore_layout(self) -> None:
        s = QSettings()
        if (geo := s.value("geometry")) is not None:
            self.restoreGeometry(geo)
        if (state := s.value("windowState")) is not None:
            self.restoreState(state)

    def _reset_layout(self) -> None:
        self.restoreState(self._default_state)

    def closeEvent(self, event) -> None:
        s = QSettings()
        s.setValue("geometry", self.saveGeometry())
        s.setValue("windowState", self.saveState())
        self.replay.stop()
        self.recorder.stop()
        self._demo_action.setChecked(False)  # stops and shuts down the demo device
        self.bus.disconnect_bus()
        self.canopen.shutdown()
        self.uds.shutdown()
        self.j1939.shutdown()
        self.xcp.shutdown()
        super().closeEvent(event)

    # --- slots ---------------------------------------------------------------
    @Slot(str)
    def _on_connected(self, desc: str) -> None:
        self.connect_bar.set_connected(True)
        self.log.appendPlainText(f"Connected: {desc}")

    @Slot()
    def _on_disconnected(self) -> None:
        self.connect_bar.set_connected(False)
        self.log.appendPlainText("Disconnected")

    @Slot(str)
    def _on_error(self, text: str) -> None:
        self.connect_bar.set_connected(self.bus.is_connected)
        self.log.appendPlainText(f"ERROR: {text}")

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
            self._demo = DemoDevice(self.connect_bar.channel.text(), self)
            self.log.appendPlainText("Demo device started: node 5, heartbeat 500 ms, TPDO1 100 ms")
        elif self._demo is not None:
            self._demo.stop()
            self._demo = None

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
        state = "connected" if self.bus.is_connected else "disconnected"
        extra = ""
        if self.recorder.is_recording:
            extra = f" | recording {self.recorder.path.name} ({self.recorder.elapsed:.0f} s)"
        self.statusBar().showMessage(f"{state} | frames: {self._frame_count}{extra}")
