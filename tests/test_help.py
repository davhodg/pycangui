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
    _find,
    environment_report,
    missing_licence_files,
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


def test_the_help_menu_entries(app, window):
    texts = [a.text() for a in window.help_menu.menu.actions() if a.text()]
    assert texts == [
        "Documentation",
        "Diagnostics...",
        "Check for updates...",
        "Licences...",
        "About pycangui",
    ]


def test_about_shows_the_version(app, window):
    report = environment_report()
    assert __version__ in report
    assert "Python" in report and "python-can" in report
    dialog = AboutDialog(window)
    assert __version__ in dialog.findChild(QPlainTextEdit).toPlainText()
    dialog.deleteLater()


def test_the_licence_window_shows_every_file(app, window):
    """Committed files must load; a generated one must explain its absence."""
    dialog = LicenceDialog(window)
    tabs = dialog.findChild(QTabWidget)
    assert tabs.count() == len(LICENCE_FILES)
    for index, entry in enumerate(LICENCE_FILES):
        body = tabs.widget(index).toPlainText()
        assert tabs.tabText(index) == entry.title
        if entry.generated and _find(entry.filename) is None:
            assert "generated when the application is built" in body
        else:
            assert f"{entry.filename} was not found" not in body
            assert len(body) > 200, f"{entry.filename} looks empty"
    dialog.deleteLater()


def test_only_a_build_is_expected_to_have_the_generated_notices(app):
    """The rule that broke CI: THIRD-PARTY-NOTICES.txt is built, not committed.

    A stale copy from an earlier local build hid this -- from a fresh checkout
    the file is simply absent, and demanding it failed both the test suite and
    --selftest.
    """
    generated = [e.filename for e in LICENCE_FILES if e.generated]
    committed = [e.filename for e in LICENCE_FILES if not e.generated]
    assert generated == ["THIRD-PARTY-NOTICES.txt"]

    # Whatever is in the working tree, a source run never demands a built file.
    assert not set(missing_licence_files(frozen=False)) & set(generated)
    # ...and the committed ones are always required, and always there.
    assert not set(missing_licence_files(frozen=False)) & set(committed)

    if _find("THIRD-PARTY-NOTICES.txt") is None:
        assert missing_licence_files(frozen=True) == generated, (
            "a packaged build must still be required to carry it"
        )


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


# --- the shipped manual ----------------------------------------------------------------
def test_the_manual_ships_with_the_package():
    """Package data, which is exactly the kind of file a build drops silently."""
    from pycangui.help import all_text, manual_text, missing_pages

    assert manual_text().startswith("# pycangui manual")
    assert missing_pages() == [], "a page that did not ship is a topic that vanished"
    assert len(all_text().splitlines()) > 100, "a stub is not a manual"


def test_the_manual_is_read_through_the_package_not_a_path():
    """importlib.resources, so it is found in a wheel as well as a checkout.

    A path relative to __file__ works from the source tree and fails in the
    one place that matters, which is somebody else's installation.
    """
    import inspect

    import pycangui.help

    source = inspect.getsource(pycangui.help)
    assert "resources.files" in source
    assert "__file__" not in source


def test_every_pane_is_documented(app, window):
    """A pane added without a word about it is a pane nobody will find."""
    from pycangui.help import all_text

    text = all_text().lower()
    for dock in window.panes.docks.values():
        assert dock.windowTitle().lower() in text, f"{dock.windowTitle()} is not in the manual"


def test_the_manual_has_no_control_characters_in_it(app):
    r"""A \b in a Python string is a backspace, not a folder separator.

    One got into the manual that way, and the path a user needs in order to
    find their backends folder shipped with a character missing.  Nothing that
    renders a document would ever have complained about it.
    """
    from pycangui.help import all_text

    stray = sorted({ch for ch in all_text() if ord(ch) < 32 and ch not in "\n\t"})
    assert not stray, f"control characters in the manual: {[hex(ord(c)) for c in stray]}"


def test_the_manual_spells_out_the_folders_it_sends_people_to(app):
    """Whole and correct, because somebody has to type them."""
    from pycangui.help import all_text

    text = all_text()
    assert r"%APPDATA%\pycangui" in text
    assert r"%APPDATA%\pycangui\backends" in text


def test_documentation_shows_the_manual_rather_than_a_browser(app, window, monkeypatch):
    """On a bench with no network, a Help menu that opens a browser is nothing."""
    from PySide6.QtGui import QDesktopServices

    from pycangui.ui import help_menu as module

    monkeypatch.setattr(
        QDesktopServices, "openUrl", lambda _url: pytest.fail("it went to the internet")
    )
    shown = []
    monkeypatch.setattr(module.ManualDialog, "exec", lambda self: shown.append(self))
    window.help_menu._open_docs()

    assert shown, "no manual window"
    assert "pycangui manual" in shown[0].view.toPlainText().lower(), "rendered, not raw markdown"
    assert "#" not in shown[0].view.toPlainText()[:40], "the markdown was not parsed"


def test_a_build_without_the_manual_falls_back_to_the_web(app, window, monkeypatch):
    from PySide6.QtGui import QDesktopServices

    from pycangui.ui import help_menu as module

    monkeypatch.setattr(module, "manual_text", lambda: "")
    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toString()))
    window.help_menu._open_docs()
    assert opened and "github" in opened[0].lower()


def test_the_selftest_notices_a_missing_page(monkeypatch):
    """So a build that dropped one fails where it can still be fixed.

    By page and not merely by "is there a manual": the front page is the one
    most likely to survive, and a manual that opens and has lost its UDS page
    is the sort of thing a user finds rather than a build does.
    """
    import pycangui.__main__ as entry
    import pycangui.help as help_package

    monkeypatch.setattr(help_package, "missing_pages", lambda: ["uds.md"])
    assert entry.selftest() == 1


# --- a page per topic, and getting between them ---------------------------------------------
def links_in(text: str) -> list[str]:
    """Every markdown link target on a page."""
    import re

    return re.findall(r"\]\(([^)]+)\)", text)


def test_every_link_between_pages_goes_somewhere():
    """A manual split into pages is a manual that can have a dead link in it,
    which the one long file could not."""
    from pycangui.help import PAGES, page_text

    for name in PAGES:
        for target in links_in(page_text(name)):
            if target.startswith(("http://", "https://", "#")):
                continue
            assert target in PAGES, f"{name} links to {target}, which is not a page"


def test_every_page_is_reachable_from_the_contents():
    """An orphan page is a page nobody will ever open."""
    from pycangui.help import MANUAL, PAGES, manual_text

    listed = set(links_in(manual_text()))
    for name in PAGES:
        if name != MANUAL:
            assert name in listed, f"{name} is not linked from the contents"


def test_every_page_says_how_to_get_back():
    """Because the window is not a browser and people arrive by link."""
    from pycangui.help import MANUAL, PAGES, page_text

    for name in PAGES:
        if name != MANUAL:
            assert MANUAL in links_in(page_text(name)), f"{name} has no way back"


def test_every_page_starts_with_its_own_title():
    from pycangui.help import PAGES, page_text, title_of

    for name in PAGES:
        assert page_text(name).lstrip().startswith(("#", "[")), name
        assert title_of(name) != name, f"{name} has no heading"


def test_a_page_that_is_not_a_page_is_not_read(tmp_path):
    """The name comes from a link inside a document, and a document can be
    edited: reading whatever path it asked for would make a manual page a way
    to open files."""
    from pycangui.help import page_text

    assert page_text("../../../etc/passwd") == ""
    assert page_text("__init__.py") == ""


def test_the_manual_opens_at_the_contents(app, window):
    from pycangui.ui.help_menu import ManualDialog

    dialog = ManualDialog(window)
    assert "pycangui manual" in dialog.view.toPlainText().lower()
    assert not dialog.back_button.isEnabled(), "nowhere to go back to yet"
    assert not dialog.contents_button.isEnabled(), "already there"


def test_a_link_opens_that_page_and_back_returns(app, window):
    """Qt will not follow a relative link itself -- setMarkdown gives the
    document no location to be relative to -- so this is ours to do."""
    from PySide6.QtCore import QUrl

    from pycangui.ui.help_menu import ManualDialog

    dialog = ManualDialog(window)
    dialog._follow(QUrl("uds.md"))
    assert "ISO 14229" in dialog.view.toPlainText()
    assert dialog.back_button.isEnabled()
    assert dialog.contents_button.isEnabled()

    dialog.back()
    assert "pycangui manual" in dialog.view.toPlainText().lower()
    assert not dialog.back_button.isEnabled()


def test_the_contents_button_gets_out_of_anywhere(app, window):
    from pycangui.ui.help_menu import ManualDialog

    dialog = ManualDialog(window, page="plugins.md")
    assert dialog.show_page("hooks.md")
    dialog.contents_button.click()
    assert "pycangui manual" in dialog.view.toPlainText().lower()


def test_a_link_out_of_the_manual_goes_to_the_browser(app, window, monkeypatch):
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices

    from pycangui.ui.help_menu import ManualDialog

    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toString()))
    ManualDialog(window)._follow(QUrl("https://example.invalid/thing"))
    assert opened == ["https://example.invalid/thing"]
