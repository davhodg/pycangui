# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Entry point: `python -m pycangui` or the `pycangui` console script."""

# Deliberately out of order (hence the noqa): timing's clock starts when it is
# imported, so it has to come before the imports whose cost is being measured.
# It pulls in nothing but the standard library.
import importlib  # noqa: I001
import importlib.util
import sys

from pycangui.core import timing
from PySide6.QtWidgets import QApplication

from pycangui import APP_NAME

#: Imported by ``--selftest``. These are the modules a packaged build is most
#: likely to be missing, because they are loaded by name at run time and so no
#: static analysis can see them.
SELFTEST_MODULES = (
    "canopen",
    "cantools",
    "isotp",
    "j1939",
    "udsoncan",
    "bincopy",
    "pyqtgraph",
    "numpy",
    "pycangui.canopen.manager",
    "pycangui.uds.manager",
    "pycangui.j1939.manager",
    "pycangui.xcp.manager",
)


def selftest() -> int:
    """Import everything a build could plausibly be missing. Exit code only:
    a windowed build has nowhere to print to."""
    failures = []
    for name in SELFTEST_MODULES:
        try:
            importlib.import_module(name)
        except Exception as exc:
            failures.append(f"{name}: {exc}")
    # Adapter backends: ask whether the module is *present*, rather than
    # importing it. Importing pulls in vendor libraries that are legitimately
    # absent on a machine without that adapter, and several backends have
    # optional pip extras of their own; neither says anything about the bundle.
    try:
        import can.interfaces

        for module, _class in can.interfaces.BACKENDS.values():
            try:
                present = importlib.util.find_spec(module) is not None
            except (ImportError, ValueError):
                present = False
            if not present:
                failures.append(f"{module}: not in the build")
    except Exception as exc:
        failures.append(f"can.interfaces: {exc}")

    # asammdf is optional for a pip installation and bundled in a frozen one,
    # so its absence is a failure only here. A build that lost it would turn
    # File > Import signals into a dialog saying the library cannot be
    # installed in this build -- true, and no use to anybody.
    if getattr(sys, "frozen", False):
        try:
            importlib.import_module("asammdf")
        except Exception as exc:
            failures.append(f"asammdf: {exc}")

    # The manual is package data, so it is exactly the kind of file a build
    # drops silently -- which is what happened to the demo EDS for months.
    # Every page, by name: one missing page is a topic that vanished, and a
    # manual that still opens is the worst way for that to be found.
    try:
        from pycangui.help import missing_images, missing_pages

        for page in missing_pages():
            failures.append(f"pycangui/help/{page}: not shipped with this build")
        for image in missing_images():
            failures.append(f"pycangui/help/{image}: shown in the manual but not shipped")
    except Exception as exc:
        failures.append(f"pycangui.help: {exc}")

    # Help > Licences reads these at run time from beside the executable. A
    # build that ships them where _find cannot see them would show three empty
    # tabs, and no import check would notice.
    try:
        from pycangui.ui.help_menu import missing_licence_files

        failures += [
            f"{name}: not found beside the application"
            for name in missing_licence_files(getattr(sys, "frozen", False))
        ]
    except Exception as exc:
        failures.append(f"pycangui.ui.help_menu: {exc}")

    if failures:
        sys.stderr.write("selftest failures:" + "".join(f"\n  {f}" for f in failures) + "\n")
        return 1
    return 0


def import_can_without_mf4() -> None:
    """Import python-can without the MF4 support it loads at import.

    python-can imports asammdf on the way in, whether or not a file is ever
    written, and asammdf brings pandas: some 500 modules, a third of
    everything pycangui loads at start-up, and on a cold disk the difference
    between seconds and half a minute. pycangui records and replays only the
    formats that do not need it, and reads MDF through asammdf itself
    (core/mdf.py), which is still imported normally when that is used.

    asammdf is hidden only while python-can is imported, which python-can
    takes as asammdf not being installed.
    """
    if "can" in sys.modules or "asammdf" in sys.modules:
        return
    sys.modules["asammdf"] = None  # type: ignore[assignment]
    try:
        import can  # noqa: F401
    finally:
        if sys.modules.get("asammdf", False) is None:
            del sys.modules["asammdf"]


#: Who Windows thinks the windows belong to. Without one, a pycangui started
#: from source is grouped under pythonw.exe and the taskbar shows Python's
#: icon whatever the window says -- the window icon only reaches the title bar.
APP_USER_MODEL_ID = "davhodg.pycangui"


def set_icon(app) -> None:
    """The application icon, before any window exists.

    Set on the application rather than on the main window so that the notice
    shown during start-up carries it too. The .ico rather than the PNG:
    it holds each size drawn for that size, and Qt picks the nearest one.
    """
    from PySide6.QtGui import QIcon

    from pycangui import resources

    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
        except (AttributeError, OSError):  # an unusual shell: the title bar still has it
            pass
    app.setWindowIcon(QIcon(str(resources.path("pycangui.ico"))))


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)  # QSettings uses these two for the registry/ini path
    set_icon(app)
    timing.mark("Qt started")
    # From here on, which package each imported second belongs to. Installed
    # after Qt because Qt is already in by now, and its cost is its own line.
    timing.watch_imports()

    # confirm.py is cheap -- Qt widgets and nothing else -- and has to come
    # before the expensive imports, because it is what covers them.
    from pycangui.ui.confirm import accept_notice

    loaded: dict = {}

    def load() -> None:
        """The slow half of starting up, done behind the notice.

        Seconds of libraries -- Qt's plotting, python-can, canopen -- with
        nothing on screen while they load is how a tool comes to feel heavy.
        Behind a dialog somebody is reading, it is free. Run on a thread of
        its own, so imports only: the window is made on the GUI thread after.

        Imported here rather than at the top of the file for a second reason
        as well: a library which is not installed becomes a dialog. Started
        from pycangui.cmd there is no console -- it runs pythonw -- so an
        ImportError on the way up is a window that never appears and not one
        word about why.
        """
        try:
            import_can_without_mf4()
            from pycangui.ui.main_window import MainWindow
            from pycangui.ui.session import Session

            loaded["classes"] = (Session, MainWindow)
            timing.mark("libraries imported")
        except ImportError as exc:
            loaded["error"] = exc

    # Before the window is built, and therefore before a workspace can reopen
    # its channels or a startup hook can connect one: a notice read after the
    # first connection is a notice that was too late.
    if not accept_notice(while_shown=load):
        return 0
    # Its own line, because the libraries load behind the notice and the
    # window is built after it: without this, every step between the two
    # carries however long somebody took to read the notice and press
    # Continue, and the report blames the first thing the window does.
    timing.mark(timing.NOTICE_STEP)
    if not loaded:  # nothing ran it, so do it here rather than not at all
        load()
    if (missing := loaded.get("error")) is not None:
        from pycangui.ui import messages

        messages.critical(
            None,
            f"{APP_NAME} cannot start",
            f"A library {APP_NAME} needs is missing:\n\n    {missing}\n\n"
            "Start it with pycangui.cmd (or pycangui.sh), which installs "
            "anything missing before starting.",
        )
        return 1

    session_class, window_class = loaded["classes"]
    # Held by a Session rather than a local, because switching workspace
    # replaces the window rather than reconfiguring it.
    loaded["session"] = session_class(window_class)
    window = loaded["session"].open()
    timing.mark("window on screen")
    if timing.enabled():
        report(window)
    note_a_slow_start(window)
    return app.exec()


def note_a_slow_start(window) -> None:
    """Say so when this start had to compile Python again.

    Not a judgement about the clock: the count comes from the compiled files
    themselves, so it is said exactly on the starts where it is true -- the
    first after an update -- and never on the others.
    """
    from pycangui.core import slow_start

    said = slow_start.message(timing.started_at(), timing.total_work())
    if said is not None:
        window.events.information(said)


def report(window) -> None:
    """Say where the time went, in the Event Log and in a file.

    Both, because the launcher starts pythonw and there is no console to
    print to, and because a number somebody has to read off the screen is a
    number that does not reach a bug report.
    """
    for line in timing.report_lines():
        window.events.information(line)
    if (path := timing.write_report(window.ctx.user_dir)) is not None:
        window.events.information(f"startup timing written to {path}")


if __name__ == "__main__":
    sys.exit(main())
