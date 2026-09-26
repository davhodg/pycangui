# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""The Help menu: how to drive it, what it is, what it is licensed under.

Documentation shows the manual that ships inside the package, rendered here
rather than opened in a browser. A Help menu that needs the internet is worth
nothing on a bench or a production line, which is where it is most wanted; the
web copy is the fallback for a build that somehow arrived without one.
"""

from __future__ import annotations

import platform
import sys
from pathlib import Path
from typing import NamedTuple

from PySide6.QtCore import QObject, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QFont, QGuiApplication, QImage, QTextDocument
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
)

from pycangui import APP_NAME, __version__
from pycangui import help as help_pages
from pycangui.core import checkout, known_ids, packages, timing
from pycangui.core.updates import PROJECT_PAGE, README_PAGE, RELEASES_PAGE, Release, latest_release
from pycangui.core.updates import is_newer as version_is_newer
from pycangui.core.worker import Worker
from pycangui.help import manual_text
from pycangui.ui import messages


class LicenceFile(NamedTuple):
    title: str
    filename: str
    blurb: str
    #: True for a file the build produces rather than one kept in the
    #: repository, so it is absent from a source checkout and required only
    #: of a packaged application.
    generated: bool = False


#: Shown in the Licences window, in this order. All three are shipped beside
#: the executable in a build.
LICENCE_FILES = (
    LicenceFile("Licence", "LICENSE", "pycangui is licensed under the Apache License 2.0."),
    LicenceFile("Notice", "NOTICE", "Attributions required by the licences of the libraries used."),
    LicenceFile(
        "Third party",
        "THIRD-PARTY-NOTICES.txt",
        "The full licence text of every installed package.",
        generated=True,
    ),
)


def _find(name: str) -> Path | None:
    """Locate a file shipped alongside the application.

    Three places, because the same code runs from a checkout and from an
    installed build, where PyInstaller puts data next to the executable or in
    its _internal folder depending on the version.
    """
    roots = []
    if getattr(sys, "frozen", False):
        roots.append(Path(sys.executable).parent)
        if meipass := getattr(sys, "_MEIPASS", None):
            roots.append(Path(meipass))
    roots.append(Path(__file__).resolve().parents[2])  # repository root
    for root in roots:
        candidate = root / name
        if candidate.is_file():
            return candidate
    return None


def licence_text(entry: LicenceFile) -> str:
    """The contents of one licence tab, including why it might be empty."""
    path = _find(entry.filename)
    if path is None:
        missing = f"{entry.filename} was not found."
        if entry.generated:
            # Running from a source checkout: the file is built, not committed.
            return (
                f"{missing}\n\nIt is generated when the application is built, from the "
                "packages actually installed, so a source checkout does not carry one.\n\n"
                "Run build/notices.py to produce it."
            )
        return missing
    try:
        return f"{entry.blurb}\n\n{path.read_text(encoding='utf-8')}"
    except OSError as exc:
        return f"{entry.filename} could not be read: {exc}"


def missing_licence_files(frozen: bool) -> list[str]:
    """Licence files that ought to be present but are not.

    A generated file is required only of a packaged build. A source checkout
    has no copy -- THIRD-PARTY-NOTICES.txt is built from the packages actually
    installed and is not committed -- which is what broke CI when this check
    was first added, because a stale copy from an earlier build was sitting in
    the working tree and hid it.
    """
    return [
        entry.filename
        for entry in LICENCE_FILES
        if (frozen or not entry.generated) and _find(entry.filename) is None
    ]


class _ManualView(QTextBrowser):
    """A text browser that finds a page's pictures inside the manual.

    ``setMarkdown`` gives the document no location, so a picture arrives here
    as a bare name with nothing to be relative to -- the problem the links have,
    solved the same way: it is looked up among the files the manual ships, and
    nowhere else. A picture wider than the page is scaled to fit, because a
    screenshot of the main window is wider than a help window.
    """

    #: Room left beside a picture for the scroll bar and the page margins.
    MARGIN = 80
    #: However narrow the window, a picture is not shrunk past legibility.
    SMALLEST = 320

    def loadResource(self, kind, url: QUrl):
        if QTextDocument.ResourceType(kind) == QTextDocument.ImageResource and not url.scheme():
            data = help_pages.image_bytes(url.toString())
            if data:
                image = QImage.fromData(data)
                # The dialog's width rather than the viewport's: the first page
                # is laid out before the window is shown, when the viewport has
                # not been given its size yet.
                width = max(self.window().width() - self.MARGIN, self.SMALLEST)
                if image.width() > width:
                    image = image.scaledToWidth(width, Qt.SmoothTransformation)
                return image
        return super().loadResource(kind, url)


class ManualDialog(QDialog):
    """The shipped manual, rendered, a page at a time.

    Qt reads Markdown itself, so these are the same files the repository serves
    on the web with no conversion step to go stale. The GitHub dialect is
    asked for by name because the manual is full of tables, and the CommonMark
    default does not have them.

    The pages link to one another with ordinary relative links, which is what
    lets them work unchanged in a browser. Qt will not follow one by itself --
    ``setMarkdown`` gives the document no location to be relative *to* -- so a
    click arrives here as a bare filename and is looked up in the manual's own
    list of pages. Anything not in that list is a link out of the manual, and
    goes to the browser.

    Back and Contents are buttons as well as links at the top of each page. A
    page that can only be left from its first line is a page people scroll back
    up through, and the one thing worse than a document too long to navigate is
    a short one you cannot get out of.
    """

    def __init__(
        self, parent: QMainWindow | None, text: str = "", page: str = help_pages.MANUAL
    ) -> None:
        super().__init__(parent)
        # A window, not a dialog: read beside the pane it describes, so it must
        # not freeze the rest of pycangui, and a long page wants maximising.
        self.setWindowFlags(Qt.Window | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint)
        self.setModal(False)
        self.setWindowTitle(f"{APP_NAME} manual")
        self.resize(860, 700)
        #: Where the reader has been, so that Back goes back rather than home.
        self._history: list[str] = []
        self._page = page

        self.view = _ManualView(self)
        self.view.setOpenLinks(False)  # every link is ours to resolve first
        self.view.anchorClicked.connect(self._follow)

        self.back_button = QPushButton("Back")
        self.back_button.clicked.connect(self.back)
        self.contents_button = QPushButton("Contents")
        self.contents_button.clicked.connect(lambda: self.show_page(help_pages.MANUAL))

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.addButton(self.back_button, QDialogButtonBox.ActionRole)
        buttons.addButton(self.contents_button, QDialogButtonBox.ActionRole)

        layout = QVBoxLayout(self)
        layout.addWidget(self.view)
        layout.addWidget(buttons)

        # ``text`` is still taken, so that anything holding a page already can
        # show it; without one the front page is read like any other.
        self._render(text or help_pages.page_text(page))
        self._update_buttons()

    def _render(self, text: str) -> None:
        self.view.document().setMarkdown(text, QTextDocument.MarkdownDialectGitHub)
        self.view.verticalScrollBar().setValue(0)  # a new page starts at its top

    def show_page(self, name: str, remember: bool = True) -> bool:
        """Open one page of the manual. False if there is no such page."""
        text = help_pages.page_text(name)
        if not text:
            return False
        if remember and name != self._page:
            self._history.append(self._page)
        self._page = name
        self._render(text)
        self.setWindowTitle(
            f"{APP_NAME} manual"
            if name == help_pages.MANUAL
            else f"{help_pages.title_of(name)} - {APP_NAME} manual"
        )
        self._update_buttons()
        return True

    def back(self) -> None:
        if self._history:
            self.show_page(self._history.pop(), remember=False)

    def _update_buttons(self) -> None:
        self.back_button.setEnabled(bool(self._history))
        self.contents_button.setEnabled(self._page != help_pages.MANUAL)

    def _follow(self, url: QUrl) -> None:
        """A link: another page of the manual, or something outside it."""
        if self.show_page(url.toString()):
            return
        if url.scheme() in ("http", "https"):
            QDesktopServices.openUrl(url)


class LicenceDialog(QDialog):
    def __init__(self, parent: QMainWindow) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} licences")
        self.resize(760, 560)
        tabs = QTabWidget()
        for entry in LICENCE_FILES:
            view = QPlainTextEdit(licence_text(entry))
            view.setReadOnly(True)
            view.setFont(QFont("Consolas", 9))
            view.setLineWrapMode(QPlainTextEdit.NoWrap)
            tabs.addTab(view, entry.title)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        layout.addWidget(buttons)


class KnownIdsDialog(QDialog):
    """What pycangui will put a name to, and who says so.

    The trace names nothing from an identifier alone, which leaves the
    question this answers: a frame with an empty Kind column is either one
    nothing was told about, or one whose id is not where it was expected,
    and those look identical until this list is read.
    """

    COLUMNS = ("ID", "Name", "From")

    def __init__(self, parent: QMainWindow, entries: list, note: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("Known CAN ids")
        self.resize(560, 520)
        clashing = known_ids.clashes(entries)
        heading = QLabel(
            f"{len(entries)} identifier(s) would be named. {note}"
            if entries
            else "Nothing would be named yet: no database is loaded, no CANopen node "
            "is known, and no protocol has been given its identifiers."
        )
        heading.setWordWrap(True)
        self.table = QTableWidget(len(entries), len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setFont(QFont("Consolas", 9))
        for row, entry in enumerate(entries):
            for column, text in enumerate((entry.shown, entry.name, entry.source)):
                item = QTableWidgetItem(text)
                if entry.can_id in clashing:
                    # Two sources naming one id: the first in the list wins
                    # in the trace and the other never appears, which is
                    # worth seeing rather than puzzling over.
                    item.setToolTip("More than one source names this id; the first one wins.")
                self.table.setItem(row, column, item)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.entries = entries
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        # Tab separated, which is what a spreadsheet and an email both want.
        # The columns line up on screen and would not survive being pasted
        # anywhere else, so the list is only useful outside this window if
        # something offers it in a shape that travels.
        copy = buttons.addButton("Copy", QDialogButtonBox.ActionRole)
        copy.setToolTip("Put the whole list on the clipboard, one row per line.")
        copy.clicked.connect(self.copy_to_clipboard)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        for widget in (heading, self.table, buttons):
            layout.addWidget(widget)

    def as_text(self) -> str:
        """The list as it pastes: a heading row, then one row per id."""
        rows = ["\t".join(self.COLUMNS)]
        rows += ["\t".join((e.shown, e.name, e.source)) for e in self.entries]
        return "\n".join(rows)

    def copy_to_clipboard(self) -> None:
        QGuiApplication.clipboard().setText(self.as_text())


class AboutDialog(QDialog):
    """Version and environment, selectable so it can be pasted into a report."""

    def __init__(self, parent: QMainWindow) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"About {APP_NAME}")
        heading = QLabel(f"<h3>{APP_NAME} {__version__}</h3><p>A graphical CAN bus tool.</p>")
        heading.setTextFormat(Qt.RichText)
        details = QPlainTextEdit(environment_report())
        details.setReadOnly(True)
        details.setFont(QFont("Consolas", 9))
        # Tall enough for the whole report, so nothing has to be scrolled to.
        lines = details.toPlainText().count("\n") + 1
        details.setMinimumHeight(details.fontMetrics().lineSpacing() * lines + 16)
        details.setMinimumWidth(details.fontMetrics().horizontalAdvance("M" * 60))
        link = QLabel(
            f'<p>Apache License 2.0 &middot; <a href="{PROJECT_PAGE}">{PROJECT_PAGE}</a></p>'
        )
        link.setOpenExternalLinks(True)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        for widget in (heading, details, link, buttons):
            layout.addWidget(widget)


def _label_width(names) -> int:
    return max(len(name) for name in ("Python", "Platform", *names)) + 2


def environment_report() -> str:
    """What a bug report needs: pycangui, Python, and every package it runs on."""
    direct = packages.declared()
    width = _label_width(r.name for r in direct)
    lines = [f"{APP_NAME} {__version__}", f"{'Python':{width}}{sys.version.split()[0]}"]
    for requirement in direct:
        found = packages.version(requirement.name)
        if found is None:
            found = "not installed" + (" (optional)" if requirement.extra else "")
        lines.append(f"{requirement.name:{width}}{found}")
    lines.append(f"{'Platform':{width}}{platform.platform()}")
    # Only a checkout has one, and only a checkout needs one: between two
    # releases every build calls itself the same version, which is no help
    # when the question is whether somebody had pulled.
    if (source := checkout.describe()) is not None:
        lines.append(f"{'Source':{width}}{source}")
    return "\n".join(lines)


def diagnostics(window) -> str:
    """Everything needed to work out why a bus looks silent.

    Written for copying into a bug report. "Connected, frames arriving, none
    shown" and "connected, no frames at all" are entirely different faults and
    look identical from a description; this separates them.
    """
    lines = [environment_report(), ""]

    # What the packages above brought with them. A fault is as likely to be
    # in one of these -- a pyserial too old for an adapter -- and nobody
    # would think to name them.
    if pulled := packages.pulled_in(packages.declared()):
        width = _label_width(pulled)
        lines.append("Installed with them")
        lines += [f"  {name:{width}}{packages.version(name)}" for name in pulled]
        lines.append("")

    lines.append("Channels")
    for name in window.channels.names():
        bus = window.channels.get(name)
        active = " (selected)" if name == window.channels.active else ""
        if bus is None or not bus.is_connected:
            lines.append(f"  {name}{active}: not connected")
            continue
        lines.append(
            f"  {name}{active}: {bus.description}\n"
            f"      interface={bus.interface!r} state={bus._read_state() or 'unreported'} "
            f"health={bus.health!r} load={bus.load_percent:.1f}% "
            f"error frames={bus._error_frames}"
        )

    floating = [(n, d) for n, d in window.panes.docks.items() if d.isFloating()]
    if floating:
        from PySide6.QtCore import Qt

        lines += ["", "Undocked panes"]
        for name, dock in floating:
            flags = dock.windowFlags()
            kind = {Qt.Widget: "Widget", Qt.Window: "Window", Qt.Tool: "Tool"}.get(
                flags & Qt.WindowType_Mask, "?"
            )
            hints = [
                label
                for hint, label in (
                    (Qt.WindowMaximizeButtonHint, "maximise"),
                    (Qt.WindowMinimizeButtonHint, "minimise"),
                    (Qt.WindowCloseButtonHint, "close"),
                    (Qt.CustomizeWindowHint, "customised"),
                    (Qt.FramelessWindowHint, "frameless"),
                )
                if flags & hint
            ]
            lines.append(f"  {name}: {kind}, {', '.join(hints) or 'no hints'}")

    trace = window.trace
    hidden_groups = sorted(trace.hidden_groups())
    hidden_channels = sorted(trace.hidden_channels())
    lines += [
        "",
        "Trace",
        f"  captured={trace.model.rowCount()} shown={trace.table.model().rowCount()} "
        f"counter={window._frame_count}",
        f"  paused={trace.pause.isChecked()} search={trace.search.text()!r}",
        f"  hidden groups={hidden_groups or 'none'}",
        f"  hidden channels={hidden_channels or 'none'}",
        f"  demo device={'running' if window._demo is not None else 'off'}",
    ]
    if trace.model.rowCount() and not trace.table.model().rowCount():
        lines.append("  >> frames ARE arriving and the filter is hiding all of them")
    elif not trace.model.rowCount():
        lines.append("  >> no frames have reached the trace at all")
    # Starting is measured on every run, so a report can say how long it took
    # without anybody having thought to ask beforehand -- which is the whole
    # difficulty with "it has got slow to start".
    if steps := timing.report_lines():
        lines += ["", *steps]
    return "\n".join(lines)


class HelpMenu(QObject):
    """Builds the Help menu and owns the update check behind it."""

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self.window = window
        self._worker = Worker(self)
        self._checking = False
        #: The manual, once opened: kept, so Documentation brings the same
        #: window forward -- on the page it was left at -- rather than a second.
        self.manual: ManualDialog | None = None

        menu = window.menuBar().addMenu("&Help")
        menu.addAction("Documentation", self._open_docs)
        menu.addAction("Diagnostics...", self._show_diagnostics)
        ids = menu.addAction("Known CAN ids...", self._show_known_ids)
        ids.setToolTip(
            "Every identifier pycangui would put a name to, and where the\n"
            "name comes from: the databases loaded, the CANopen nodes known,\n"
            "and the addresses in the UDS and XCP panes."
        )
        menu.addSeparator()
        self.check_action = menu.addAction("Check for updates...", self._check_for_updates)
        menu.addAction("Licences...", self._show_licences)
        menu.addAction(f"About {APP_NAME}", self._show_about)
        self.menu = menu

    def shutdown(self) -> None:
        self._worker.stop()
        self.close_manual()

    def close_manual(self) -> None:
        if self.manual is not None:
            # A window of its own, with no parent to take it down: without this
            # it would keep pycangui running after the main window had gone.
            self.manual.close()
            self.manual.deleteLater()
            self.manual = None

    def _open_docs(self) -> None:
        """The shipped manual, or the web page if this build has none.

        Opened without a parent, as a pane taken out into a window is, so it can
        sit behind or beside the main window rather than always on top of it.
        """
        text = manual_text()
        if not text:
            QDesktopServices.openUrl(QUrl(README_PAGE))
            return
        if self.manual is None:
            self.manual = ManualDialog(None, text)
            self.manual.setWindowIcon(self.window.windowIcon())
        self.manual.show()
        self.manual.raise_()
        self.manual.activateWindow()

    def _show_diagnostics(self) -> None:
        """Report the state of the window, and put it on the clipboard."""
        report = diagnostics(self.window)
        QGuiApplication.clipboard().setText(report)
        # Through the Event Log like every other line, as information. Written
        # into the pane directly it had no level of its own, and took the
        # colour of whatever came before it -- red and bold after an error.
        self.window.events.information("Diagnostics (copied to the clipboard):\n" + report)

    def _show_known_ids(self) -> None:
        window = self.window
        entries = known_ids.collect(
            dbc=getattr(window, "dbc", None),
            canopen=getattr(window, "canopen", None),
            uds=getattr(window, "uds", None),
            xcp=getattr(window, "xcp", None),
        )
        KnownIdsDialog(window, entries, known_ids.J1939_NOTE).exec()

    def _show_licences(self) -> None:
        LicenceDialog(self.window).exec()

    def _show_about(self) -> None:
        AboutDialog(self.window).exec()

    # --- updates -------------------------------------------------------------------
    def _check_for_updates(self) -> None:
        if self._checking:  # the menu stays clickable while the request is out
            return
        self._checking = True
        self.check_action.setText("Checking for updates...")
        self.check_action.setEnabled(False)
        self._worker.submit(latest_release, self._on_checked)

    def _on_checked(self, result, error: str | None) -> None:
        self._checking = False
        self.check_action.setText("Check for updates...")
        self.check_action.setEnabled(True)
        if error is not None:
            messages.information(self.window, "Check for updates", error)
            return
        release, problem = result
        self._report(release, problem)

    def _report(self, release: Release | None, problem: str) -> None:
        if release is None:
            messages.information(
                self.window,
                "Check for updates",
                f"{problem}\n\nYou are running {APP_NAME} {__version__}.",
            )
        elif version_is_newer(release.version, __version__):
            answer = messages.question(
                self.window,
                "Update available",
                f"{APP_NAME} {release.version} is available. "
                f"You are running {__version__}.\n\n"
                "Open the releases page to download it?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if answer == QMessageBox.Yes:
                QDesktopServices.openUrl(QUrl(release.url or RELEASES_PAGE))
        else:
            messages.information(
                self.window,
                "Check for updates",
                f"{APP_NAME} {__version__} is up to date.",
            )
