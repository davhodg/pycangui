"""Entry point: `python -m pycangui` or the `pycangui` console script."""

import importlib
import importlib.util
import sys

from PySide6.QtWidgets import QApplication

from pycangui import APP_NAME
from pycangui.ui.main_window import MainWindow

#: Imported by ``--selftest``.  These are the modules a packaged build is most
#: likely to be missing, because they are loaded by name at run time and so no
#: static analysis can see them.
SELFTEST_MODULES = (
    "canopen",
    "cantools",
    "isotp",
    "j1939",
    "udsoncan",
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

    if failures:
        sys.stderr.write("selftest failures:" + "".join(f"\n  {f}" for f in failures) + "\n")
        return 1
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)  # QSettings uses these two for the registry/ini path
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
