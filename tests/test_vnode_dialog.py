"""Tools > Virtual nodes: the small dialog in front of the mechanism.

Its whole job is to ask the three things a node file cannot answer for
itself -- which channel, how fast, and a second channel if it is a gateway
-- so these tests are mostly about it not asking anything else, and about
what it does with a file that is broken.
"""

import pytest
from PySide6.QtCore import QSettings

from pycangui.ui.main_window import MainWindow
from pycangui.ui.vnode_dialog import VirtualNodeDialog


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    win = MainWindow()
    yield win
    win.vnodes.stop_all()
    # A test that closed it itself has already been through closeEvent, and
    # a second pass disconnects everything twice and warns about each one.
    if not win._closing:
        win.close()


@pytest.fixture
def dialog(app, window):
    made = VirtualNodeDialog(window, window.vnodes, window.channels)
    yield made
    made.close()


def rows(tree):
    return [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())]


def test_the_shipped_nodes_are_offered(app, dialog):
    listed = rows(dialog.kinds)
    assert "CANopen device" in listed and "Gateway" in listed


def test_choosing_one_offers_its_own_rate(app, dialog):
    """A file that says what rate it wants should not have to say it again,
    and a rate left over from the previous node is a wrong answer wearing a
    considered one's clothes."""
    for i in range(dialog.kinds.topLevelItemCount()):
        item = dialog.kinds.topLevelItem(i)
        if item.text(0) == "J1939 engine":
            dialog.kinds.setCurrentItem(item)
            break
    assert dialog.rate.value() == pytest.approx(20.0)


def test_a_broken_file_is_listed_with_its_error_rather_than_hidden(app, window, dialog):
    """Hiding it would send its author looking for a file they can see on
    disk.  It cannot be started, and says why."""
    (window.ctx.nodes_dir / "wrong.py").write_text("def poll(node:\n", encoding="utf-8")
    dialog.refresh()

    broken = next(
        dialog.kinds.topLevelItem(i)
        for i in range(dialog.kinds.topLevelItemCount())
        if dialog.kinds.topLevelItem(i).text(0) == "wrong"
    )
    assert broken.isDisabled()
    assert "line 1" in broken.text(1)


def test_starting_one_puts_it_in_the_running_list(app, dialog):
    dialog.channel.setCurrentText("v_dialog1")
    for i in range(dialog.kinds.topLevelItemCount()):
        item = dialog.kinds.topLevelItem(i)
        if item.text(0) == "XCP slave":
            dialog.kinds.setCurrentItem(item)
            break
    dialog._start()
    assert "XCP slave" in rows(dialog.active)

    dialog.active.setCurrentItem(dialog.active.topLevelItem(0))
    dialog._stop()
    assert rows(dialog.active) == []


def test_the_window_offers_it_in_the_tools_menu(app, window):
    tools = next(a.menu() for a in window.menuBar().actions() if a.text() == "&Tools")
    assert "Virtual nodes..." in [a.text() for a in tools.actions()]


def test_a_node_left_running_is_stopped_when_the_window_closes(app, window):
    """A node holding a bus open outlives the window, and a process that
    will not exit is a bug nobody can see."""
    window.vnodes.start("xcp_slave", "v_dialog2")
    assert window.vnodes.running()
    window.close()
    assert window.vnodes.running() == []
