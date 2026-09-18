# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""The extension API pages still build.

A docstring pdoc cannot render, or a module in the list that no longer
imports, would otherwise be found by whoever next runs build/api_docs.py --
probably while writing a plugin, and probably not the person who broke it.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def api_docs():
    spec = importlib.util.spec_from_file_location("api_docs", ROOT / "build" / "api_docs.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def render(output: Path, *modules: str) -> None:
    pytest.importorskip("pdoc")
    import pdoc.render

    docs = api_docs()
    pdoc.render.configure(docformat=docs.DOCFORMAT)
    pdoc.pdoc(*(modules or docs.MODULES), output_directory=output)


def test_the_extension_api_renders(tmp_path):
    render(tmp_path)
    assert (tmp_path / "index.html").is_file()
    for name in api_docs().MODULES:
        page = tmp_path / (name.replace(".", "/") + ".html")
        assert page.is_file(), f"no page for {name}"


def test_documented_members_are_public_ones(tmp_path):
    """Asserted on pdoc's anchors rather than on the page text: every
    public method's source is embedded in its page, so a private helper's
    *name* appears wherever a public method calls it. What matters is that
    it gets no entry of its own."""
    render(tmp_path, "pycangui.core.hooks")
    page = (tmp_path / "pycangui/core/hooks.html").read_text(encoding="utf-8")
    assert 'id="Hooks.update_stubs"' in page, "the page is there but documents nothing"
    assert 'id="_why_uncallable"' not in page
