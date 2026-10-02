# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""A library that is installed and will not load, which is not one that is missing.

What a release of canmatrix did to asammdf: the import raised TypeError, not
ImportError, so nothing written for "not installed" caught it.
"""

import sys

import pytest

from pycangui import __main__ as entry
from pycangui.core import mdf
from pycangui.ui import import_signals, messages


def without_asammdf(monkeypatch):
    for name in [n for n in sys.modules if n == "asammdf" or n.startswith("asammdf.")]:
        monkeypatch.delitem(sys.modules, name)


@pytest.fixture
def broken(tmp_path, monkeypatch):
    """An asammdf that is there and raises what the real one did."""
    package = tmp_path / "asammdf"
    package.mkdir()
    (package / "__init__.py").write_text(
        "raise TypeError(\"unsupported operand type(s) for |: 'types.UnionType' and 'module'\")\n"
    )
    without_asammdf(monkeypatch)
    monkeypatch.syspath_prepend(str(tmp_path))


@pytest.fixture
def missing(monkeypatch):
    without_asammdf(monkeypatch)
    monkeypatch.setitem(sys.modules, "asammdf", None)  # what "not installed" looks like


def test_a_library_that_will_not_load_is_not_called_missing(broken):
    assert mdf.installed() and not mdf.available()
    assert mdf.why_not() not in ("", mdf.WHY)
    with pytest.raises(mdf.NotAvailableError):
        mdf._mdf("anything.mf4")


def test_a_library_that_is_not_there_is_called_missing(missing):
    assert not mdf.installed() and not mdf.available()
    assert mdf.why_not() == mdf.WHY


def test_fetching_it_again_is_not_offered_for_one_that_will_not_load(app, broken, monkeypatch):
    told, logged = [], []
    monkeypatch.setattr(messages, "question", lambda *a, **k: pytest.fail("offered to install"))
    monkeypatch.setattr(messages, "warning", lambda *a, **k: told.append(a))

    class Ctx:
        def error(self, text):
            logged.append(text)

    assert not import_signals.ensure_available(None, Ctx())
    assert len(told) == 1 and len(logged) == 1


def test_starting_tells_the_two_apart_and_names_what_failed():
    try:
        raise TypeError("unsupported operand")
    except TypeError as exc:
        will_not_load = entry.why_it_cannot_start(exc)
    not_there = entry.why_it_cannot_start(ModuleNotFoundError("No module named 'cantools'"))
    assert will_not_load != not_there
    assert "TypeError" in will_not_load and __file__ in will_not_load, "what failed, and where"
    assert "cantools" in not_there
