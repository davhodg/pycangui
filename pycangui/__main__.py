"""Entry point: `python -m pycangui` or the `pycangui` console script."""

import sys

from PySide6.QtWidgets import QApplication

from pycangui import APP_NAME
from pycangui.ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)  # QSettings uses these two for the registry/ini path
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
