# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Installing a package with pip from inside pycangui. How it looks while it
runs, and cancel and failure, are tested through the MDF reader it was made
for (test_import_signals.py); what is here is the rest of it."""

import sys
import time

from pycangui.core.context import Context
from pycangui.ui import pip_install


def test_pip_is_this_interpreter_s_and_says_a_line_at_a_time():
    """Into the environment pycangui imports from, one line per step."""
    command = pip_install.pip_command("asammdf", "pandas")
    assert command[:4] == [sys.executable, "-m", "pip", "install"]
    assert "--progress-bar" in command and command[-2:] == ["asammdf", "pandas"]


def test_pip_taking_too_long_is_stopped_and_said(app, monkeypatch):
    monkeypatch.setattr(pip_install, "TIMEOUT_S", 1)
    ctx = Context(log=print)
    posted = []
    ctx.events.posted.connect(lambda text, level: posted.append((level, text)))
    began = time.monotonic()
    fake = [sys.executable, "-c", "import time; time.sleep(30)"]

    assert not pip_install.install(None, ctx, ["something"], fake)
    assert time.monotonic() - began < 10
    assert posted[-1][0] == "error"
