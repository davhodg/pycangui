# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""The application icon: shipped, readable, and actually wired in.

An icon fails quietly in every direction.  A missing file leaves Qt's default
with no error; an exe built without one gets PyInstaller's own, which is a
perfectly good icon and not ours; an installer without SetupIconFile shows the
Inno Setup box.  So each place is checked for the one thing that says it is
pycangui's icon rather than some icon.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]


def check_build():
    spec = importlib.util.spec_from_file_location("check_build", ROOT / "build" / "check_build.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_icon_holds_every_size_windows_asks_for(app):
    from PySide6.QtGui import QIcon

    from pycangui import resources

    icon = QIcon(str(resources.path("pycangui.ico")))
    assert sorted(s.width() for s in icon.availableSizes()) == SIZES


def test_the_application_sets_it(app):
    from pycangui.__main__ import set_icon

    set_icon(app)
    assert not app.windowIcon().isNull()
    assert 256 in [s.width() for s in app.windowIcon().availableSizes()]


def test_the_exe_is_built_with_it():
    spec = (ROOT / "build" / "pycangui.spec").read_text(encoding="utf-8")
    assert 'icon=str(PROJECT / "pycangui" / "resources" / "pycangui.ico")' in spec
    assert "icon=None" not in spec


def test_the_installer_is_built_with_it():
    script = (ROOT / "build" / "installer.iss").read_text(encoding="utf-8")
    assert r"SetupIconFile=..\pycangui\resources\pycangui.ico" in script
    assert (ROOT / "build" / r"..\pycangui\resources\pycangui.ico").is_file()


def test_the_build_check_tells_our_icon_from_pyinstallers():
    """PyInstaller embeds its own icon when given none, so "the exe has an
    icon" proves nothing.  Our largest image's bytes inside the exe does."""
    checks = check_build()
    ours = checks.ICON.read_bytes()
    largest = checks.largest_image(ours)
    assert len(largest) > 1000, "the 256 px image, not a stub"
    assert checks.carries_icon(b"MZ...resources..." + largest + b"...", ours)
    assert not checks.carries_icon(b"MZ...an exe with somebody else's icon...", ours)
