# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Column widths: fitted by pycangui, or fitted until somebody drags one."""

import pytest
from PySide6.QtWidgets import QHeaderView, QTableWidget, QTreeWidget, QTreeWidgetItem

from pycangui.core.settings import Settings
from pycangui.ui import column_widths
from pycangui.ui.column_widths import AUTO, MANUAL, WIDEST_FITTED, ColumnWidths

LONG = "A signal name that goes on for a very long time indeed, and then some more"


@pytest.fixture
def settings(tmp_path):
    return Settings(tmp_path / "settings.json")


def a_tree(app, settings, rows=(("short", "1", "x"),), **how):
    tree = QTreeWidget()
    tree.setHeaderLabels(["Name", "Value", "Unit"])
    tree.resize(900, 300)
    widths = ColumnWidths(tree, settings, "test", **how)
    for row in rows:
        tree.addTopLevelItem(QTreeWidgetItem(list(row)))
    tree.show()
    app.processEvents()
    return tree, widths


def test_automatic_is_what_it_always_was(app, settings):
    tree, _widths = a_tree(app, settings, stretch=1)
    header = tree.header()
    assert header.sectionResizeMode(0) == QHeaderView.ResizeToContents
    assert header.sectionResizeMode(1) == QHeaderView.Stretch


def test_manual_columns_can_be_dragged_and_a_long_name_does_not_take_the_pane(app, settings):
    column_widths.set_mode(settings, MANUAL)
    tree, _widths = a_tree(app, settings, rows=((LONG * 3, "1", "x"),))
    header = tree.header()
    assert header.sectionResizeMode(0) == QHeaderView.Interactive
    assert header.sectionSize(0) <= WIDEST_FITTED


def test_a_dragged_width_is_kept_and_remembered(app, settings):
    column_widths.set_mode(settings, MANUAL)
    tree, widths = a_tree(app, settings)
    tree.header().resizeSection(0, 333)  # as a drag does
    tree.addTopLevelItem(QTreeWidgetItem([LONG, "2", "y"]))
    app.processEvents()
    assert tree.header().sectionSize(0) == 333, "new rows do not undo a drag"

    widths._save()
    again, _ = a_tree(app, settings)
    assert again.header().sectionSize(0) == 333


def test_a_double_click_on_the_edge_gives_the_column_back(app, settings):
    column_widths.set_mode(settings, MANUAL)
    tree, widths = a_tree(app, settings)
    tree.header().resizeSection(0, 333)
    tree.header().sectionHandleDoubleClicked.emit(0)
    app.processEvents()
    assert tree.header().sectionSize(0) != 333
    widths._save()
    assert settings.get("columns.test") is None


def test_changing_the_setting_changes_the_lists_that_are_open(app, settings):
    tree, _widths = a_tree(app, settings)
    column_widths.set_mode(settings, MANUAL)
    assert tree.header().sectionResizeMode(0) == QHeaderView.Interactive
    column_widths.set_mode(settings, AUTO)
    assert tree.header().sectionResizeMode(0) == QHeaderView.ResizeToContents


def a_table(app, settings, **how):
    table = QTableWidget(1, 3)
    table.resize(900, 300)
    widths = ColumnWidths(table, settings, "test_table", **how)
    table.show()
    app.processEvents()
    return table, widths


def test_a_table_is_set_the_same_way_as_a_tree(app, settings):
    table, _widths = a_table(app, settings)
    header = table.horizontalHeader()
    assert header.sectionResizeMode(0) == QHeaderView.ResizeToContents
    column_widths.set_mode(settings, MANUAL)
    assert header.sectionResizeMode(0) == QHeaderView.Interactive
    header.resizeSection(0, 222)
    table.insertRow(1)
    app.processEvents()
    assert header.sectionSize(0) == 222


def test_a_fixed_column_is_that_wide_until_it_is_dragged(app, settings):
    table, _widths = a_table(app, settings, fixed={1: 150})
    header = table.horizontalHeader()
    assert header.sectionResizeMode(1) == QHeaderView.Fixed
    assert header.sectionSize(1) == 150

    column_widths.set_mode(settings, MANUAL)
    assert header.sectionResizeMode(1) == QHeaderView.Interactive
    assert header.sectionSize(1) >= 150
    header.resizeSection(1, 60)
    table.insertRow(1)
    app.processEvents()
    assert header.sectionSize(1) == 60


def test_hiding_a_column_and_showing_it_again_is_not_a_drag(app, settings):
    column_widths.set_mode(settings, MANUAL)
    table, widths = a_table(app, settings)
    table.setColumnHidden(0, True)
    table.setColumnHidden(0, False)
    app.processEvents()
    assert 0 not in widths._dragged


def test_the_trace_follows_the_setting(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    from pycangui.ui.main_window import MainWindow

    window = MainWindow()
    try:
        trace = window.panes.view("trace")
        for table in (trace.table, trace.latest_table):
            assert table.horizontalHeader().sectionResizeMode(1) == QHeaderView.ResizeToContents
        column_widths.set_mode(window.ctx.settings, MANUAL)
        for table in (trace.table, trace.latest_table):
            assert table.horizontalHeader().sectionResizeMode(1) == QHeaderView.Interactive
    finally:
        column_widths.set_mode(window.ctx.settings, AUTO)
        window.close()
