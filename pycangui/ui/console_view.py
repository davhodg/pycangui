# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Interactive Python console dock.

Built on the standard library's ``code.InteractiveConsole`` so it behaves like
the normal ``python`` prompt: multi-line blocks, ``...`` continuation, proper
tracebacks. The box typed into takes several lines as well -- Shift+Enter for
another, or several pasted at once -- and Enter then runs them together, as
one block. What is printed is shown as it is printed, so a script that steps
through twenty nodes says so a node at a time.

Code runs on the GUI thread. That keeps the API simple (no locking, you can
poke widgets) at the cost that a blocking call such as an SDO read freezes the
window for its duration -- fine for interactive use.

For something slower there is ``canopen.background(job, done)`` and
``uds.background(...)``, which put the work on that protocol's own worker and
hand the answer back here. Deliberately per call rather than for everything:
this namespace holds ``window``, and a widget touched from another thread
takes the process with it.

**Stop** works where the window is free to see it pressed: in ``wait(s)``, and
at a ``print``, each of which lets the window run. The script stops there.
"""

from __future__ import annotations

import ast
import code
import io
import sys
import time
import traceback
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from PySide6.QtCore import QEventLoop, Qt, QTimer, Signal
from PySide6.QtGui import QKeyEvent, QTextCursor, QTextOption
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pycangui.core.context import Context
from pycangui.ui import clear_menu, folders, fonts, icons

BANNER = """pycangui console -- Python {ver}
  ctx       settings, log(), eds_dir ... bus       connect/send/send_periodic
  canopen   CANopen manager (node(5).sdo[...]) hooks     user hooks
  uds       UDS manager (uds.client is the udsoncan Client when open)
  j1939     J1939 manager       xcp       XCP manager (xcp.a2l parameters)
  window    the main window                     send(id, data, ext=False, fd=False)
  wait(s)   let the window and buses run for s seconds (a script's sleep)
Anything slow: canopen.background(job, done) / uds.background(...), which run
off this thread so the window keeps up. done(result, error), or print.
Shift+Enter for another line, or paste several: Enter runs them together.
Type help(bus), help(canopen) or dir() to explore."""

RUN_TIP = (
    "Run a Python file here, with the same names this console has:\n"
    "ctx, bus, channels, canopen, uds, j1939, xcp, window, send."
)
STOP_TIP = (
    "Stop the script, or what was typed, at its next wait() or print.\n"
    "A call that holds the window, an SDO read say, finishes first."
)
#: The most lines the box typed into grows to before it scrolls.
TALLEST = 10
#: What Tab puts in, and what a line after a colon is indented by.
INDENT = "    "
#: How much the pane keeps, in lines. A script printing in a loop for an
#: hour must not take the memory with it.
MOST_LINES = 5000
#: How often printing lets the window run, in seconds: often enough for the
#: text to be seen arriving and Stop to be pressed, not on every line of a
#: loop printing thousands.
REDRAW_EVERY = 0.05


class ScriptStoppedError(Exception):
    """The script was stopped from the console."""


class _Console(code.InteractiveConsole):
    """An interactive console whose errors are shown in it, as Python shows them.

    The standard one hands an error to ``sys.excepthook`` whenever a program
    has replaced it, and pycangui has: its hook reports what escapes its own
    code as a bug in pycangui. So a name mistyped here was announced as one.
    A mistake in what was typed is the typist's, and belongs under the line
    that made it.
    """

    def showtraceback(self) -> None:
        kind, value, tb = sys.exc_info()
        if kind is not None and issubclass(kind, ScriptStoppedError):
            self.write("Stopped.\n")
            return
        # The first entry is this console's own call into the typed code.
        self.write("".join(traceback.format_exception(kind, value, tb.tb_next if tb else None)))

    def showsyntaxerror(self, filename=None, **_kwargs) -> None:
        kind, value, _tb = sys.exc_info()
        self.write("".join(traceback.format_exception_only(kind, value)))

    def write(self, data: str) -> None:
        sys.stderr.write(data)


class _Shown(io.TextIOBase):
    """Printed text, into the pane as it is printed.

    ``checks`` is for stdout: a print is where a busy script is stopped. Not
    for stderr, which is where the traceback saying so is written.
    """

    def __init__(self, view: ConsoleView, checks: bool) -> None:
        super().__init__()
        self._view = view
        self._checks = checks

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        self._view.write(text)
        if self._view._in_events:
            return len(text)  # a slot printing while the window ran, not the script
        self._view._let_window_run()
        if self._checks:
            self._view._check()
        return len(text)


class _Typed(QPlainTextEdit):
    """What is typed: a line, or several, with Up and Down going through what
    was typed before.

    Enter is the end of it, and says so with ``entered``; Shift+Enter
    is another line, indented as the one before and further after a colon.
    One line tall until there is more, so that it looks like the prompt it
    mostly is.
    """

    entered = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.history: list[str] = []
        self._pos = 0
        self._draft = ""
        self.setWordWrapMode(QTextOption.NoWrap)
        self.setTabChangesFocus(False)
        self.textChanged.connect(self._fit)
        self._fit()

    # Named as QLineEdit's are, which is what this was and what its callers use.
    def text(self) -> str:
        return self.toPlainText()

    def setText(self, text: str) -> None:
        self.setPlainText(text)
        self.moveCursor(QTextCursor.End)

    def _fit(self) -> None:
        lines = min(max(self.document().blockCount(), 1), TALLEST)
        margins = self.contentsMargins()
        frame = 2 * int(self.document().documentMargin()) + margins.top() + margins.bottom()
        self.setFixedHeight(self.fontMetrics().lineSpacing() * lines + frame)

    def _new_line(self) -> None:
        """Another line, indented as this one, and further after a colon."""
        line = self.textCursor().block().text()
        indent = line[: len(line) - len(line.lstrip())]
        if line.rstrip().endswith(":"):
            indent += INDENT
        self.insertPlainText("\n" + indent)

    def remember(self, line: str) -> None:
        if line and (not self.history or self.history[-1] != line):
            self.history.append(line)
        self._pos = len(self.history)
        self._draft = ""

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if event.modifiers() & Qt.ShiftModifier:
                self._new_line()
            else:
                self.entered.emit()
            return
        if event.key() == Qt.Key_Tab:
            self.insertPlainText(INDENT)
            return
        cursor = self.textCursor()
        # Within what is typed, Up and Down move between its lines; only from
        # the first line, or the last, do they go through what came before.
        on_edge = (event.key() == Qt.Key_Up and cursor.blockNumber() == 0) or (
            event.key() == Qt.Key_Down and cursor.blockNumber() == self.document().blockCount() - 1
        )
        if event.key() in (Qt.Key_Up, Qt.Key_Down) and self.history and on_edge:
            if self._pos == len(self.history):
                self._draft = self.text()
            self._pos += -1 if event.key() == Qt.Key_Up else 1
            self._pos = max(0, min(self._pos, len(self.history)))
            self.setText(self.history[self._pos] if self._pos < len(self.history) else self._draft)
            return
        super().keyPressEvent(event)


class ConsoleView(QWidget):
    #: A file was run, by the button here or from the File menu: its path.
    ran = Signal(str)

    def __init__(self, namespace: dict, ctx: Context) -> None:
        super().__init__()
        self.ctx = ctx
        self.namespace = namespace
        # This console's own wait: the same sleep, and a place Stop can end it.
        namespace["wait"] = self.wait
        self.console = _Console(namespace)
        #: True while a line or a script is running. Nothing else is taken
        #: until it has finished.
        self.running = False
        self._stopping = False
        #: The event loop being waited in, so that Stop can end it.
        self._loop: QEventLoop | None = None
        self._redrawn = 0.0
        #: True while printing lets the window run: what prints then is
        #: something else's, and is not where the script stops.
        self._in_events = False
        mono = fonts.mono()

        self.output = clear_menu.add(QPlainTextEdit(), ctx, "Console output")
        self.output.setReadOnly(True)
        self.output.setFont(mono)
        self.output.setMaximumBlockCount(MOST_LINES)
        self.output.setAccessibleName("Console output")
        self.prompt = QLabel(">>>")
        self.prompt.setFont(mono)
        self.input = _Typed()
        self.input.setFont(mono)
        self.input.setAccessibleName("Python")
        self.input.entered.connect(self._on_enter)
        self.run_button = icons.beside(QPushButton("Run script..."), icons.START)
        self.run_button.setToolTip(RUN_TIP)
        self.run_button.clicked.connect(self._run_script_dialog)
        self.stop_button = icons.beside(QPushButton("Stop"), icons.STOP)
        self.stop_button.setToolTip(STOP_TIP)
        self.stop_button.clicked.connect(self.stop)
        # No Clear button: it is rarely wanted, and Clear all is on the
        # output's right-click.

        row = QHBoxLayout()
        row.addWidget(self.prompt)
        row.addWidget(self.input, 1)
        row.addWidget(self.run_button)
        row.addWidget(self.stop_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.addWidget(self.output)
        layout.addLayout(row)

        self.write(BANNER.format(ver=sys.version.split()[0]) + "\n")
        self._enable()

    # --- running ---------------------------------------------------------------------
    def _on_enter(self) -> None:
        if self.running:
            return
        typed = self.input.text()
        self.input.clear()
        self.input.remember(typed)
        if "\n" not in typed:
            self.write(f"{self.prompt.text()} {typed}\n")
            more = self._run(lambda: self.console.push(typed))
            self.prompt.setText("..." if more else ">>>")
            return
        lines = typed.split("\n")
        first = self.prompt.text()
        self.write(
            "".join(f"{first if i == 0 else '...'} {line}\n" for i, line in enumerate(lines))
        )
        # What was begun a line at a time and finished with these.
        source = "\n".join([*self.console.buffer, *lines])
        self.console.resetbuffer()
        self._run(lambda: self._run_block(source))
        self.prompt.setText(">>>")

    def _run_block(self, source: str) -> None:
        """Several lines run as one, each value shown as the prompt shows it.

        Compiled as the prompt compiles one statement, but with all of them:
        "a = 2", "a * 3" shows 6, as typing them one at a time would.
        """
        try:
            tree = ast.parse(source, "<console>")
            compiled = compile(ast.Interactive(body=tree.body), "<console>", "single")
        except (SyntaxError, ValueError, OverflowError):
            self.console.showsyntaxerror("<console>")
            return
        self.console.runcode(compiled)

    def run_source(self, source: str, filename: str = "<script>") -> None:
        """Run a whole script with this console's names: what Run script... does."""
        self.write(f"--- running {filename}\n")

        def go() -> None:
            try:
                exec(compile(source, filename, "exec"), self.namespace)
            except SystemExit:
                print("(the script called exit, which here ends the script and nothing more)")
            except ScriptStoppedError:
                sys.stderr.write("Stopped.\n")
            except Exception:
                kind, value, tb = sys.exc_info()
                # The first entry is this function's own call into the script.
                sys.stderr.write(
                    "".join(traceback.format_exception(kind, value, tb.tb_next if tb else None))
                )

        self._run(go)
        self.write(f"--- {filename} has finished\n")

    def run_file(self, path: str) -> None:
        if self.running:
            self.ctx.warn(f"{path} was not run: the console is still running. Stop it first.")
            return
        file = Path(path)
        try:
            source = file.read_text(encoding="utf-8")
        except OSError as exc:
            self.write(f"{file} cannot be read: {exc}\n")
            return
        # Before it runs: a script that fails is still the one to run again.
        self.ran.emit(str(file))
        self.run_source(source, str(file))

    def _run(self, work):
        """Run ``work`` with what it prints shown here, one at a time."""
        if self.running:
            return None
        self.running = True
        self._stopping = False
        self._enable()
        try:
            with redirect_stdout(_Shown(self, True)), redirect_stderr(_Shown(self, False)):
                return work()
        finally:
            self.running = False
            self._enable()
            self.input.setFocus()

    # --- waiting and stopping ----------------------------------------------------------
    def wait(self, seconds: float) -> None:
        """A script's sleep: frames, answers and timers go on arriving,
        where time.sleep would stop the window, and Stop with it."""
        self._check()
        loop = QEventLoop()
        QTimer.singleShot(max(0, int(seconds * 1000)), loop.quit)
        self._loop = loop
        try:
            loop.exec()
        finally:
            self._loop = None
        self._check()

    def stop(self) -> None:
        """Stop what is running at its next wait or print."""
        if not self.running:
            return
        self._stopping = True
        if self._loop is not None:
            self._loop.quit()

    def _check(self) -> None:
        if self._stopping:
            raise ScriptStoppedError("Stopped.")

    def _let_window_run(self) -> None:
        """Let the text be drawn and Stop be pressed, now and then."""
        if not self.running:
            return
        now = time.monotonic()
        if now - self._redrawn >= REDRAW_EVERY:
            self._redrawn = now
            self._in_events = True
            try:
                QApplication.processEvents()
            finally:
                self._in_events = False

    def _run_script_dialog(self) -> None:
        path = folders.open_file(
            self, self.ctx, folders.SCRIPT, "Run Python script", "Python (*.py)", self.ctx.user_dir
        )
        if path:
            self.run_file(path)

    def _enable(self) -> None:
        self.input.setEnabled(not self.running)
        self.run_button.setEnabled(not self.running)
        self.stop_button.setEnabled(self.running)

    # --- output ----------------------------------------------------------------------
    def write(self, text: str) -> None:
        self.output.moveCursor(QTextCursor.End)
        self.output.insertPlainText(text)
        self.output.moveCursor(QTextCursor.End)
