"""The Help menu: what this is, what it is licensed under, and is it current.

Deliberately small.  Real help -- how to drive the panes, what the CANopen
workflow is -- is a job of its own; for now Documentation opens the README,
which is where that material already lives.
"""

from __future__ import annotations

import platform
import sys
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QTabWidget,
    QVBoxLayout,
)

from pycangui import APP_NAME, __version__
from pycangui.core.updates import PROJECT_PAGE, README_PAGE, RELEASES_PAGE, Release, latest_release
from pycangui.core.updates import is_newer as version_is_newer
from pycangui.core.worker import Worker

#: Shown in the Licences window, in this order.  Each is shipped beside the
#: executable by the installer and lives at the repository root in a checkout.
LICENCE_FILES = (
    ("Licence", "LICENSE", "pycangui is licensed under the Apache License 2.0."),
    ("Notice", "NOTICE", "Attributions required by the licences of the libraries used."),
    (
        "Third party",
        "THIRD-PARTY-NOTICES.txt",
        "The full licence text of every installed package, generated at build time.",
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


class LicenceDialog(QDialog):
    def __init__(self, parent: QMainWindow) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} licences")
        self.resize(760, 560)
        tabs = QTabWidget()
        for title, filename, blurb in LICENCE_FILES:
            path = _find(filename)
            try:
                text = path.read_text(encoding="utf-8") if path else ""
            except OSError as exc:
                text = f"{filename} could not be read: {exc}"
            view = QPlainTextEdit(f"{blurb}\n\n{text}" if text else f"{filename} was not found.")
            view.setReadOnly(True)
            view.setFont(QFont("Consolas", 9))
            view.setLineWrapMode(QPlainTextEdit.NoWrap)
            tabs.addTab(view, title)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        layout.addWidget(buttons)


class AboutDialog(QDialog):
    """Version and environment, selectable so it can be pasted into a report."""

    def __init__(self, parent: QMainWindow) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"About {APP_NAME}")
        heading = QLabel(f"<h3>{APP_NAME} {__version__}</h3><p>A user-friendly CAN bus tool.</p>")
        heading.setTextFormat(Qt.RichText)
        details = QPlainTextEdit(environment_report())
        details.setReadOnly(True)
        details.setFont(QFont("Consolas", 9))
        details.setFixedHeight(150)
        link = QLabel(
            f'<p>Apache License 2.0 &middot; <a href="{PROJECT_PAGE}">{PROJECT_PAGE}</a></p>'
        )
        link.setOpenExternalLinks(True)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        for widget in (heading, details, link, buttons):
            layout.addWidget(widget)


def environment_report() -> str:
    """What a bug report needs: versions of the things that actually differ."""
    width = 11  # wide enough for the longest label below
    lines = [f"{APP_NAME} {__version__}", f"{'Python':{width}}{sys.version.split()[0]}"]
    for label, module in (("PySide6", "PySide6"), ("python-can", "can"), ("canopen", "canopen")):
        try:
            imported = __import__(module)
            lines.append(f"{label:{width}}{getattr(imported, '__version__', 'unknown')}")
        except Exception:  # a missing optional package is not worth a traceback here
            lines.append(f"{label:{width}}not installed")
    lines.append(f"{'Platform':{width}}{platform.platform()}")
    return "\n".join(lines)


class HelpMenu(QObject):
    """Builds the Help menu and owns the update check behind it."""

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self.window = window
        self._worker = Worker(self)
        self._checking = False

        menu = window.menuBar().addMenu("&Help")
        menu.addAction("Documentation", self._open_docs)
        menu.addSeparator()
        self.check_action = menu.addAction("Check for updates...", self._check_for_updates)
        menu.addAction("Licences...", self._show_licences)
        menu.addAction(f"About {APP_NAME}", self._show_about)
        self.menu = menu

    def shutdown(self) -> None:
        self._worker.stop()

    def _open_docs(self) -> None:
        QDesktopServices.openUrl(QUrl(README_PAGE))

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
            QMessageBox.information(self.window, "Check for updates", error)
            return
        release, problem = result
        self._report(release, problem)

    def _report(self, release: Release | None, problem: str) -> None:
        if release is None:
            QMessageBox.information(
                self.window,
                "Check for updates",
                f"{problem}\n\nYou are running {APP_NAME} {__version__}.",
            )
        elif version_is_newer(release.version, __version__):
            answer = QMessageBox.question(
                self.window,
                "Update available",
                f"{APP_NAME} {release.version} is available.  "
                f"You are running {__version__}.\n\n"
                "Open the releases page to download it?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if answer == QMessageBox.Yes:
                QDesktopServices.openUrl(QUrl(release.url or RELEASES_PAGE))
        else:
            QMessageBox.information(
                self.window,
                "Check for updates",
                f"{APP_NAME} {__version__} is up to date.",
            )
