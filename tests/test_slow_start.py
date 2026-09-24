# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Noticing the one start that had to compile Python again."""

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
    assert slow_start.message(began, 2.5, old) is None


def test_a_slow_start_with_nothing_compiled_still_says_so(tmp_path):
    """Reported: a start of half a minute said nothing at all, because
    nothing had been compiled -- the cold disk that caused it left no trace
    for the compiled-file count to find."""
    began = time.time()
    old = _compiled(tmp_path, 20, began - 3600)
    said = slow_start.message(began, 29.0, old)
    assert said is not None
    assert "29.0" in said


def test_without_timing_it_says_how_to_get_the_detail(tmp_path):
    began = time.time()
    plain = slow_start.message(began, 29.0, {})
    detailed = slow_start.message(began, 29.0, {}, detailed=True)
    assert "--timing" in plain
    assert "--timing" not in detailed, "already on, so nothing to suggest"


def test_the_report_follows_a_slow_start_only():
    assert slow_start.is_slow(slow_start.SLOW_S)
    assert not slow_start.is_slow(3.0)


def test_a_start_that_compiled_the_files_says_so(tmp_path):
    began = time.time() - 5
    fresh = _compiled(tmp_path, 30, began + 1)  # written during this start

    said = slow_start.message(began, 12.0, fresh)

    assert said is not None
    assert "30" in said, "how many, so it is a fact rather than a feeling"
    assert "12.0" in said
    assert "quicker" in said, "and that it does not happen again"


def test_a_couple_of_files_is_not_worth_a_message(tmp_path):
    """A hook file somebody edited compiles again, and says nothing about
    how long starting took."""
    began = time.time() - 5
    two = _compiled(tmp_path, 2, began + 1)
    assert slow_start.message(began, 2.0, two) is None


def test_a_module_with_no_compiled_copy_is_passed_over(tmp_path):
    began = time.time() - 5
    modules = {"builtin": _module(None), **_compiled(tmp_path, 6, began + 1)}
    assert slow_start.compiled_this_start(began, modules) == 6


def test_a_compiled_copy_that_has_gone_is_not_counted(tmp_path):
    began = time.time() - 5
    modules = _compiled(tmp_path, 3, began + 1)
    modules["deleted"] = _module(tmp_path / "never-existed.pyc")
    assert slow_start.compiled_this_start(began, modules) == 3
