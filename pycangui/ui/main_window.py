"""Main window: connect bar, dockable panes, persistent layout."""

from __future__ import annotations

from PySide6.QtCore import QSettings, Qt, QTimer, QUrl, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDockWidget, QMainWindow, QPlainTextEdit, QStatusBar

from pycangui import APP_NAME, __version__
from pycangui.canopen.manager import CanopenManager
from pycangui.core.bus import BusManager
from pycangui.core.context import Context
from pycangui.core.demo import DemoDevice
from pycangui.core.hooks import Hooks
from pycangui.ui.canopen_view import CanopenView
from pycangui.ui.connect_bar import ConnectBar
from pycangui.ui.trace_view import TraceView


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

        # --- log pane first: everything else reports into it -----------------
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)

        # --- user context, hooks, protocol managers -------------------------
        self.ctx = Context(log=self.log.appendPlainText)
        self.hooks = Hooks(self.ctx)
        self.canopen = CanopenManager(self.bus)

        # --- docks -----------------------------------------------------------
        self.trace = TraceView()
        self._add_dock("trace", "Trace", self.trace, Qt.LeftDockWidgetArea)
        self.canopen_view = CanopenView(self.canopen, self.hooks, self.ctx)
        self._add_dock("canopen", "CANopen", self.canopen_view, Qt.RightDockWidgetArea)
        self._add_dock("log", "Log", self.log, Qt.BottomDockWidgetArea)

        self.setStatusBar(QStatusBar())
        self._frame_count = 0
        self._status_timer = QTimer(self, interval=500, timeout=self._update_status)
        self._status_timer.start()

        # --- wiring ----------------------------------------------------------
        self.bus.frames.connect(self.trace.on_frames)
        self.bus.frames.connect(self._count_frames)
        self.bus.connected.connect(self._on_connected)
        self.bus.disconnected.connect(self._on_disconnected)
        self.bus.error.connect(self._on_error)

        # --- menus & layout persistence --------------------------------------
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
        tools_menu.addAction("Reload hooks", self._reload_hooks)
        tools_menu.addAction("Update hook stubs", self._update_hook_stubs)
        self._default_state = self.saveState()
        self._restore_layout()

    # --- helpers -------------------------------------------------------------
    def _add_dock(self, name: str, title: str, widget, area: Qt.DockWidgetArea) -> QDockWidget:
        dock = QDockWidget(title, self)
        dock.setObjectName(name)  # saveState/restoreState identify docks by objectName
        dock.setWidget(widget)
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
        self._demo_action.setChecked(False)  # stops and shuts down the demo device
        self.bus.disconnect_bus()
        self.canopen.shutdown()
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

    @Slot(list)
    def _count_frames(self, frames: list) -> None:
        self._frame_count += len(frames)

    def _update_status(self) -> None:
        state = "connected" if self.bus.is_connected else "disconnected"
        self.statusBar().showMessage(f"{state} | frames: {self._frame_count}")
