# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Interactive Python console dock.

Built on the standard library's ``code.InteractiveConsole`` so it behaves like
the normal ``python`` prompt: multi-line blocks, ``...`` continuation, proper
tracebacks.  Output is captured by swapping ``sys.stdout`` / ``sys.stderr``
only for the duration of each statement.

Code runs on the GUI thread.  That keeps the API simple (no locking, you can
poke widgets) at the cost that a blocking call such as an SDO read freezes the
window for its duration -- fine for interactive use.
"""

from __future__ import annotations

import code
import contextlib
import io
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QKeyEvent, QTextCursor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pycangui.core.context import Context
from pycangui.ui import folders

BANNER = """pycangui console -- Python {ver}
  ctx       settings, log(), eds_dir ...      bus       connect/send/send_periodic
  canopen   CANopen manager (node(5).sdo[...]) hooks     user hooks
  uds       UDS manager (uds.client is the udsoncan Client when open)
  j1939     J1939 manager       xcp       XCP manager (xcp.a2l parameters)
  window    the main window                     send(id, data, ext=False, fd=False)
Type help(bus), help(canopen) or dir() to explore."""


class _HistoryLineEdit(QLineEdit):
    """Single-line input with up/down history like a shell."""

    def __init__(self) -> None:
        super().__init__()
        self.history: list[str] = []
        self._pos = 0
        self._draft = ""

    def remember(self, line: str) -> None:
        if line and (not self.history or self.history[-1] != line):
            self.history.append(line)
        self._pos = len(self.history)
        self._draft = ""

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key_Up, Qt.Key_Down) and self.history:
            if self._pos == len(self.history):
                self._draft = self.text()
            self._pos += -1 if event.key() == Qt.Key_Up else 1
            self._pos = max(0, min(self._pos, len(self.history)))
            self.setText(self.history[self._pos] if self._pos < len(self.history) else self._draft)
            return
        super().keyPressEvent(event)


class ConsoleView(QWidget):
    def __init__(self, namespace: dict, ctx: Context) -> None:
        super().__init__()
        self.ctx = ctx
        self.namespace = namespace
        self.console = code.InteractiveConsole(namespace)
        mono = QFont("Consolas", 9)

        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setFont(mono)
        self.output.setMaximumBlockCount(5000)
        self.prompt = QLabel(">>>")
        self.prompt.setFont(mono)
        self.input = _HistoryLineEdit()
        self.input.setFont(mono)
        self.input.returnPressed.connect(self._on_enter)
        run_btn = QPushButton("Run script...")
        run_btn.setToolTip(
            "Run a Python file here, with the same names this console has:\n"
            "ctx, bus, channels, canopen, uds, j1939, xcp, window, send."
        )
        run_btn.clicked.connect(self._run_script_dialog)
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self.output.clear)

        row = QHBoxLayout()
        row.addWidget(self.prompt)
        row.addWidget(self.input, 1)
        row.addWidget(run_btn)
        row.addWidget(clear_btn)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.addWidget(self.output)
        layout.addLayout(row)

        self._write(BANNER.format(ver=sys.version.split()[0]) + "\n")

    # --- execution -------------------------------------------------------------
    def _on_enter(self) -> None:
        line = self.input.text()
        self.input.clear()
        self.input.remember(line)
        self._write(f"{self.prompt.text()} {line}\n")
        more = self._capture(lambda: self.console.push(line))
        self.prompt.setText("..." if more else ">>>")

    def run_source(self, source: str, filename: str = "<script>") -> None:
        """Execute a whole script in the console namespace (used by Run script)."""
        self._write(f"--- running {filename}\n")

        def go():
            compiled = compile(source, filename, "exec")
            exec(compiled, self.namespace)

        self._capture(go)

    def _capture(self, fn):
        buf = io.StringIO()
        result = None
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            try:
                result = fn()
            except SystemExit:
                buf.write("(SystemExit ignored)\n")
            except Exception:  # InteractiveConsole handles its own; this is for run_source
                import traceback

                traceback.print_exc(file=buf)
        if text := buf.getvalue():
            self._write(text if text.endswith("\n") else text + "\n")
        return result

    def _run_script_dialog(self) -> None:
        path = folders.open_file(
            self, self.ctx, folders.SCRIPT, "Run Python script", "Python (*.py)", self.ctx.user_dir
        )
        if path:
            self.run_file(path)

    def run_file(self, path: str) -> None:
        p = Path(path)
        try:
            self.run_source(p.read_text(encoding="utf-8"), str(p))
        except OSError as exc:
            self._write(f"cannot read {p}: {exc}\n")

    # --- output ----------------------------------------------------------------------
    def _write(self, text: str) -> None:
        self.output.moveCursor(QTextCursor.End)
        self.output.insertPlainText(text)
        self.output.moveCursor(QTextCursor.End)
