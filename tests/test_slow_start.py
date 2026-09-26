# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""What starting had to compile, and what made a slow start slow."""

import os
import time
import types

from pycangui.core import slow_start


def _module(cached):
    module = types.ModuleType("pretend")
    module.__cached__ = str(cached) if cached else None
    return module


def _compiled(tmp_path, how_many, when):
    """A set of modules whose compiled copies were written at ``when``."""
    modules = {}
    for i in range(how_many):
        pyc = tmp_path / f"module{i}.pyc"
        pyc.write_bytes(b"not really bytecode")
        os.utime(pyc, (when, when))
        modules[f"module{i}"] = _module(pyc)
    return modules


def test_an_ordinary_start_says_nothing(tmp_path):
    began = time.time()
    old = _compiled(tmp_path, 20, began - 3600)  # compiled an hour ago
    assert slow_start.compiled_this_start(began, old) == 0
    assert slow_start.compiled_note(began, old) is None
    assert slow_start.message(2.5) is None


# --- what Python compiled: said whenever it did, fast start or slow ------------------------
def test_a_start_that_compiled_files_says_how_many(tmp_path):
    began = time.time() - 5
    fresh = _compiled(tmp_path, 30, began + 1)  # written during this start
    said = slow_start.compiled_note(began, fresh)
    assert said is not None and "30" in said, "how many, so it is a fact rather than a feeling"


def test_even_one_compiled_file_is_said(tmp_path):
    """Information, not an excuse for a slow start, so no threshold."""
    began = time.time() - 5
    one = _compiled(tmp_path, 1, began + 1)
    said = slow_start.compiled_note(began, one)
    assert said is not None and "1 changed file" in said


# --- a slow start: how long, and what took over a second --------------------------------
def test_a_slow_start_with_nothing_compiled_still_says_so():
    """Reported: a start of half a minute said nothing at all, because
    nothing had been compiled -- the cold disk that caused it left no trace
    for the compiled-file count to find."""
    said = slow_start.message(29.0)
    assert said is not None and "29.0" in said
    assert "Diagnostics" in said, "and where the rest of the report is"


def test_the_report_follows_a_slow_start_only():
    assert slow_start.is_slow(slow_start.SLOW_S)
    assert not slow_start.is_slow(3.0)


STEPS = [
    ("Qt started", 0.4),
    ("libraries imported", 24.3),
    ("databases", 1.2),
    ("window on screen", 0.6),
    ("the launcher script", 3.1),
]
PACKAGES = {"pandas": 12.0, "PySide6": 5.1, "numpy": 2.2, "can": 0.4}


def test_every_step_over_a_second_is_named_longest_first():
    said = slow_start.message(29.6, STEPS, PACKAGES)
    names = ("libraries imported", "the launcher script", "databases")
    order = [said.index(name) for name in names]
    assert order == sorted(order)
    assert "Qt started" not in said and "window on screen" not in said, "under a second"


def test_the_libraries_step_names_the_packages_over_a_second():
    """ "The libraries" on its own says nothing about which one."""
    said = slow_start.message(29.6, STEPS, PACKAGES)
    libraries = said[said.index("libraries imported") : said.index("the launcher script")]
    for name in ("pandas", "PySide6", "numpy"):
        assert name in libraries
    assert "can " not in libraries, "under a second"


def test_a_module_with_no_compiled_copy_is_passed_over(tmp_path):
    began = time.time() - 5
    modules = {"builtin": _module(None), **_compiled(tmp_path, 6, began + 1)}
    assert slow_start.compiled_this_start(began, modules) == 6


def test_a_compiled_copy_that_has_gone_is_not_counted(tmp_path):
    began = time.time() - 5
    modules = _compiled(tmp_path, 3, began + 1)
    modules["deleted"] = _module(tmp_path / "never-existed.pyc")
    assert slow_start.compiled_this_start(began, modules) == 3
