# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""The issue templates, checked against the application they describe.

A template that sends people to a menu entry which has since been renamed
costs a round of questions on every report, and nothing about the template
would ever say so.  So the menu entries, pane names and links it quotes are
read back from the real thing.
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / ".github" / "ISSUE_TEMPLATE"
SECURITY_FORM = "https://github.com/davhodg/pycangui/security/advisories/new"


def read(name: str) -> str:
    return (TEMPLATES / name).read_text(encoding="utf-8")


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    from pycangui.ui.main_window import MainWindow

    win = MainWindow()
    yield win
    win.close()


def test_each_form_has_what_github_needs():
    for name in ("bug_report.yml", "feature_request.yml"):
        text = read(name)
        assert text.startswith("name: "), name
        assert "\ndescription: " in text and "\nbody:\n" in text, name


def test_the_bug_report_names_menu_entries_that_exist(app, window):
    entries = [a.text() for a in window.help_menu.menu.actions() if a.text()]
    assert "Diagnostics..." in entries
    assert "*Help > Diagnostics...*" in read("bug_report.yml")
    assert "Documentation" in entries
    assert "Help > Documentation" in read("config.yml")


def test_the_panes_offered_are_panes(app, window):
    titles = {dock.windowTitle() for dock in window.panes.docks.values()}
    offered = [
        "CAN Trace",
        "CAN Transmit",
        "Signals and Plot",
        "CANopen",
        "UDS",
        "J1939",
        "XCP",
        "ASCII Log",
        "Python Console",
    ]
    request = read("feature_request.yml")
    for pane in offered:
        assert f"- {pane}\n" in request
        assert pane in titles, f"no pane called {pane!r} any more"
    assert "Event Log" in titles and "label: Event Log" in read("bug_report.yml")


def test_security_reports_go_where_the_policy_sends_them():
    assert SECURITY_FORM in read("config.yml")
    assert SECURITY_FORM in (ROOT / "SECURITY.md").read_text(encoding="utf-8")


def test_the_manual_link_is_the_manual():
    assert "blob/master/pycangui/help/manual.md" in read("config.yml")
    assert (ROOT / "pycangui" / "help" / "manual.md").is_file()
