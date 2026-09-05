"""Entry point: `python -m pycangui` or the `pycangui` console script."""

import importlib
import importlib.util
import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from pycangui import APP_NAME

#: Imported by ``--selftest``.  These are the modules a packaged build is most
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
    """Import everything a build could plausibly be missing.  Exit code only:
    a windowed build has nowhere to print to."""
    failures = []
    for name in SELFTEST_MODULES:
        try:
            importlib.import_module(name)
        except Exception as exc:
            failures.append(f"{name}: {exc}")
    # Adapter backends: ask whether the module is *present*, rather than
    # importing it.  Importing pulls in vendor libraries that are legitimately
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

    # The manual is package data, so it is exactly the kind of file a build
    # drops silently -- which is what happened to the demo EDS for months.
    try:
        from pycangui.help import manual_text

        if not manual_text().strip():
            failures.append("pycangui/help/manual.md: not shipped with this build")
    except Exception as exc:
        failures.append(f"pycangui.help: {exc}")

    # Help > Licences reads these at run time from beside the executable.  A
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


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)  # QSettings uses these two for the registry/ini path

    # Imported here rather than at the top of the file so that a library which
    # is not installed becomes a dialog.  Started from pycangui.cmd there is no
    # console -- it runs pythonw -- so an ImportError on the way up is a window
    # that never appears and not one word about why.
    try:
        from pycangui.ui.main_window import MainWindow
        from pycangui.ui.session import Session
    except ImportError as exc:
        QMessageBox.critical(
            None,
            f"{APP_NAME} cannot start",
            f"A library {APP_NAME} needs is missing:\n\n    {exc}\n\n"
            "Start it with pycangui.cmd (or pycangui.sh), which installs "
            "anything missing before starting.",
        )
        return 1

    # Held by a Session rather than a local, because switching workspace
    # replaces the window rather than reconfiguring it -- see ui/session.py.
    session = Session(MainWindow)
    session.open()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
