"""Controls whose effect is not written on them explain themselves on hover.

Not every control: Clear, Remove and Connect say what they do, and a tooltip
repeating the label is noise.  These are the ones where the label cannot say
it -- a service number, a precondition, or a consequence worth knowing before
pressing rather than after.
"""

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QAbstractItemView, QCheckBox, QComboBox, QPushButton, QToolButton

from pycangui.ui.main_window import MainWindow

KINDS = (QPushButton, QCheckBox, QToolButton, QComboBox, QAbstractItemView)


@pytest.fixture(scope="module")
def window(tmp_path_factory):
    import os

    from PySide6.QtWidgets import QApplication

    os.environ["PYCANGUI_HOME"] = str(tmp_path_factory.mktemp("home"))
    QApplication.instance() or QApplication([])
    QSettings().clear()
    win = MainWindow()
    yield win
    win.close()


def control(window, view_name, text):
    view = getattr(window, view_name)
    for kind in KINDS:
        for widget in view.findChildren(kind):
            if getattr(widget, "text", None) and widget.text() == text:
                return widget
    raise AssertionError(f"no control labelled {text!r} in {view_name}")


@pytest.mark.parametrize(
    ("view", "label", "expected"),
    [
        # A service number is the thing the label cannot carry.
        ("uds_view", "Unlock", "0x27"),
        ("uds_view", "Read DID", "0x22"),
        ("uds_view", "Clear DTCs", "0x14"),
        ("uds_view", "Tester present", "0x3E"),
        # A consequence worth knowing before pressing, not after.
        ("uds_view", "Reset", "lost"),
        ("uds_view", "Clear DTCs", "erased"),
        ("xcp_view", "Unlock CAL", "compute_key"),
        # A precondition: pressing it without this gets you nowhere.
        ("tx", "Add CANopen RPDO...", "EDS"),
        ("uds_view", "Open", "until it is open"),
        # And what a choice actually means.
        ("connect_bar", "FD", "classic controller"),
    ],
)
def test_the_tooltip_says_what_the_label_cannot(window, view, label, expected):
    assert expected in control(window, view, label).toolTip()


def test_the_obvious_ones_are_left_alone(window):
    """A tooltip that repeats the label is noise, not help."""
    for view, label in (("trace", "Clear"), ("tx", "Remove"), ("tx", "Add raw")):
        assert not control(window, view, label).toolTip()


def test_the_hint_text_moved_off_the_window(window):
    """Standing sentences became tooltips, so the panes got their space back."""
    from PySide6.QtWidgets import QLabel

    for view_name in ("canopen_view", "tx", "xcp_view"):
        labels = [x.text() for x in getattr(window, view_name).findChildren(QLabel)]
        assert not any("double-click" in x.lower() for x in labels), labels
    assert "Double-click" in window.tx.tree.toolTip()
    assert "Double-click" in window.canopen_view.od.toolTip()
