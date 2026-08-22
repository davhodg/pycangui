"""One offscreen QApplication for the whole test session (widgets need a
QApplication, not a QCoreApplication, and there can only be one)."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session")
def app():
    return QApplication.instance() or QApplication([])
