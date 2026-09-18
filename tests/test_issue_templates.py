# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""The issue templates, checked against the application they describe.

A template that sends people to a menu entry which has since been renamed
costs a round of questions on every report, and nothing about the template
would ever say so. So the links it quotes are read back from the real thing.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / ".github" / "ISSUE_TEMPLATE"
SECURITY_FORM = "https://github.com/davhodg/pycangui/security/advisories/new"


def read(name: str) -> str:
    return (TEMPLATES / name).read_text(encoding="utf-8")


def test_each_form_has_what_github_needs():
    for name in ("bug_report.yml", "feature_request.yml"):
        text = read(name)
        assert text.startswith("name: "), name
        assert "\ndescription: " in text and "\nbody:\n" in text, name


def test_security_reports_go_where_the_policy_sends_them():
    assert SECURITY_FORM in read("config.yml")
    assert SECURITY_FORM in (ROOT / "SECURITY.md").read_text(encoding="utf-8")


def test_the_manual_link_is_the_manual():
    assert "blob/master/pycangui/help/manual.md" in read("config.yml")
    assert (ROOT / "pycangui" / "help" / "manual.md").is_file()
