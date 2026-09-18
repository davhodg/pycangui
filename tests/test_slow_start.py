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


def test_nothing_is_said_when_nothing_was_compiled(tmp_path):
    began = time.time()
    old = _compiled(tmp_path, 20, began - 3600)  # compiled an hour ago
    assert slow_start.compiled_this_start(began, old) == 0
    assert slow_start.message(began, 12.0, old) is None


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
