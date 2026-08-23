"""The Help menu: version, licences, and the update check."""

import json
import urllib.error
import urllib.request

import pytest
from PySide6.QtWidgets import QMessageBox, QPlainTextEdit, QTabWidget

from pycangui import __version__
from pycangui.core import updates
from pycangui.ui.help_menu import (
    LICENCE_FILES,
    AboutDialog,
    LicenceDialog,
    environment_report,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [("1.2.3", (1, 2, 3)), ("v0.0.1", (0, 0, 1)), ("2.0", (2, 0)), ("nonsense", (0,))],
)
def test_parse_version(text, expected):
    assert updates.parse_version(text) == expected


@pytest.mark.parametrize(
    ("candidate", "current", "newer"),
    [
        ("0.0.2", "0.0.1", True),
        ("v1.0.0", "0.9.9", True),
        ("0.0.1", "0.0.1", False),
        ("0.0.1", "0.0.2", False),
        ("1.2", "1.2.0", False),  # the same version written two ways
        ("1.2.1", "1.2", True),
    ],
)
def test_is_newer(candidate, current, newer):
    assert updates.is_newer(candidate, current) is newer


class _Response:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode()

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def test_latest_release_reads_the_tag(monkeypatch):
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *_a, **_k: _Response({"tag_name": "v1.4.0", "html_url": "https://example/1.4.0"}),
    )
    release, problem = updates.latest_release()
    assert problem == ""
    assert release.version == "1.4.0" and release.url == "https://example/1.4.0"


def test_a_404_is_reported_as_no_releases_not_as_a_failure(monkeypatch):
    """Which is also what a private repository looks like from outside."""

    def raise_404(*_a, **_k):
        raise urllib.error.HTTPError("url", 404, "Not Found", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", raise_404)
    release, problem = updates.latest_release()
    assert release is None
    assert "No releases" in problem


def test_being_offline_is_reported_calmly(monkeypatch):
    def raise_url_error(*_a, **_k):
        raise urllib.error.URLError("getaddrinfo failed")

    monkeypatch.setattr(urllib.request, "urlopen", raise_url_error)
    release, problem = updates.latest_release()
    assert release is None
    assert "Could not reach GitHub" in problem


def test_nothing_is_fetched_unless_asked(app, tmp_path, monkeypatch):
    """Opening the window must not make a network request of its own accord."""
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))

    def forbidden(*_a, **_k):
        pytest.fail("pycangui must not call home on startup")

    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    from pycangui.ui.main_window import MainWindow

    win = MainWindow()
    app.processEvents()
    win.close()


# --- the dialogs -------------------------------------------------------------------
@pytest.fixture
def window(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    from pycangui.ui.main_window import MainWindow

    win = MainWindow()
    yield win
    win.close()


def test_the_help_menu_has_the_four_entries(app, window):
    texts = [a.text() for a in window.help_menu.menu.actions() if a.text()]
    assert texts == ["Documentation", "Check for updates...", "Licences...", "About pycangui"]


def test_about_shows_the_version(app, window):
    report = environment_report()
    assert __version__ in report
    assert "Python" in report and "python-can" in report
    dialog = AboutDialog(window)
    assert __version__ in dialog.findChild(QPlainTextEdit).toPlainText()
    dialog.deleteLater()


def test_the_licence_window_finds_every_file(app, window):
    """The files are shipped beside the executable; a missing one says so."""
    dialog = LicenceDialog(window)
    tabs = dialog.findChild(QTabWidget)
    assert tabs.count() == len(LICENCE_FILES)
    for index, (title, filename, _blurb) in enumerate(LICENCE_FILES):
        body = tabs.widget(index).toPlainText()
        assert tabs.tabText(index) == title
        assert f"{filename} was not found" not in body, f"{filename} was not located"
        assert len(body) > 200, f"{filename} looks empty"
    dialog.deleteLater()


def test_an_up_to_date_check_says_so(app, window, monkeypatch):
    shown = []
    monkeypatch.setattr(
        QMessageBox, "information", lambda _p, _t, text, *a, **k: shown.append(text)
    )
    window.help_menu._report(updates.Release(version=__version__, url=""), "")
    assert "up to date" in shown[0]


def test_a_newer_release_offers_the_download_page(app, window, monkeypatch):
    asked = []
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda _p, _t, text, *a, **k: (asked.append(text), QMessageBox.No)[1],
    )
    window.help_menu._report(updates.Release(version="99.0.0", url="https://example/99"), "")
    assert asked and "99.0.0" in asked[0]
