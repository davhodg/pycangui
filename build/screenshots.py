# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Regenerate the README screenshot: the main window on the demo device.

    python build/screenshots.py [out.png]

It opens pycangui in its default layout, connects the demo channel, loads
demo.dbc and plots engine and vehicle speed for half a minute, then saves the
window.  The window is on screen for about a minute while it does; leave it be.

A throwaway PYCANGUI_HOME and QSettings file are used, so your own workspace,
layout and remembered answers are neither read nor written.  Only the demo
device and demo.dbc are involved, so nothing that is not pycangui's own can
end up in the picture.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = PROJECT / "screenshots" / "main-window.png"
#: What gets plotted.  The demo engine sweeps between 800 and 2000 rpm about
#: every 12.6 s, so a 30 s window shows two and a half sweeps.
PLOTTED = ("DBC EEC1/EngineSpeed", "DBC CCVS1/WheelBasedVehicleSpeed")
WINDOW_S = 30
SIZE = (1400, 900)


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT
    home = tempfile.mkdtemp(prefix="pycangui-screenshot-")
    os.environ["PYCANGUI_HOME"] = home

    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(Path(home) / "qsettings"))
    app = QApplication(sys.argv)

    from pycangui import resources
    from pycangui.__main__ import set_icon
    from pycangui.core.detect import DEMO_CHANNEL
    from pycangui.ui.main_window import MainWindow

    set_icon(app)
    window = MainWindow()
    window.resize(*SIZE)
    window.move(40, 40)
    window.show()

    def pump(seconds: float, until=None) -> bool:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            if until is not None and until():
                return True
            time.sleep(0.01)
        return until() if until is not None else True

    pump(1)
    window._connect_active("virtual", DEMO_CHANNEL, 500000, False)
    if not window._load_dbc(str(resources.path("demo.dbc"))):
        print("demo.dbc did not load", file=sys.stderr)
        return 1
    if not pump(20, lambda: all(key in window.signals.keys() for key in PLOTTED)):
        print(f"the signals never arrived; the hub has {window.signals.keys()}", file=sys.stderr)
        return 1

    window.plot.window_s.setValue(WINDOW_S)
    for key in PLOTTED:
        window.signals_view.set_plotted(key, True)
        window.plot.set_plotted(key, True)
    pump(WINDOW_S + 2)

    out.parent.mkdir(parents=True, exist_ok=True)
    if not window.grab().save(str(out)):
        print(f"could not write {out}", file=sys.stderr)
        return 1
    print(f"saved {out} ({window.width()}x{window.height()})")
    window.channels.disconnect_all()
    window.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
