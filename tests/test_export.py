"""Decoded signals out to a spreadsheet.

The shape is the whole design: a time column *per signal*, with a blank column
between them.  A single shared time column would have to be built by
interpolating, or holding the last value, or inventing a grid -- and all three
write numbers into the file that were never on the bus.
"""

import csv

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QFileDialog

from pycangui.core import export
from pycangui.core.signals import SignalSeries
from pycangui.ui.main_window import MainWindow


def series(group, name, unit, times, values):
    return SignalSeries(group, name, unit, list(times), list(values))


@pytest.fixture
def three():
    return [
        series("DBC Engine", "Speed", "rpm", [0.01, 0.02, 0.03], [1500, 1520, 1490]),
        series("XCP", "current", "A", [0.012, 0.022], [12.4, 12.6]),
        series("CANopen node 5 TPDO1", "Counter", "", [0.05], [42]),
    ]


# --- the header ------------------------------------------------------------------------
def test_each_signal_gets_a_time_column_and_a_gap_before_the_next(three):
    assert export.header(three) == [
        "Time (s)",
        "DBC Engine/Speed (rpm)",
        "",
        "Time (s)",
        "XCP/current (A)",
        "",
        "Time (s)",
        "CANopen node 5 TPDO1/Counter",
    ]


def test_the_first_signal_has_no_gap_before_it(three):
    assert export.header(three[:1]) == ["Time (s)", "DBC Engine/Speed (rpm)"]


def test_a_signal_with_no_unit_gets_no_empty_brackets():
    assert export.label(series("g", "Counter", "", [0], [1])) == "g/Counter"
    assert export.label(series("g", "Speed", "rpm", [0], [1])) == "g/Speed (rpm)"


def test_the_heading_says_which_signal_not_just_its_name():
    """Two databases can each have a Speed, and a column that cannot say which
    is worse than a long one."""
    a = series("DBC Engine", "Speed", "rpm", [0], [1])
    b = series("DBC Gearbox", "Speed", "rpm", [0], [1])
    assert export.header([a, b])[1] != export.header([a, b])[4]


# --- the rows --------------------------------------------------------------------------
def test_every_number_written_was_measured(three):
    rows = list(export.rows(three))
    assert rows[0] == ["0.010000", "1500", "", "0.012000", "12.4", "", "0.050000", "42"]
    assert rows[1] == ["0.020000", "1520", "", "0.022000", "12.6", "", "", ""]


def test_a_signal_that_has_finished_leaves_its_cells_empty(three):
    """Not its last value repeated: that would be a reading nobody took."""
    rows = list(export.rows(three))
    assert len(rows) == 3, "as long as the longest signal"
    assert rows[2] == ["0.030000", "1490", "", "", "", "", "", ""]


def test_the_time_column_stays_a_column():
    """Six decimals rather than exponents, so the first samples read like the rest."""
    rows = list(export.rows([series("g", "n", "", [0.0, 1e-05, 12.5], [1, 2, 3])]))
    assert [r[0] for r in rows] == ["0.000000", "0.000010", "12.500000"]


def test_values_come_out_as_they_went_in():
    rows = list(export.rows([series("g", "n", "", [0, 1, 2], [0.1, 4294967295, -0.5])]))
    assert [r[1] for r in rows] == ["0.1", "4294967295", "-0.5"], "no float noise, no rounding"


def test_a_signal_nothing_was_ever_heard_from_is_left_out():
    """A DBC message that never arrived would otherwise be two empty columns."""
    heard = series("g", "heard", "", [0.0], [1.0])
    silent = series("g", "silent", "", [], [])
    assert export.with_samples([heard, silent]) == [heard]


# --- the file --------------------------------------------------------------------------
def test_the_file_reads_back_as_the_grid_it_looks_like(tmp_path, three):
    path = tmp_path / "signals.csv"
    count, rows = export.write_csv(str(path), three)
    assert (count, rows) == (3, 3)

    with open(path, newline="", encoding="utf-8") as handle:
        table = list(csv.reader(handle))
    assert table[0] == export.header(three)
    assert len(table) == 4, "a heading and three rows, with no blank lines between"
    assert {len(row) for row in table} == {8}, "every row the same width"


def test_a_comma_in_a_unit_does_not_split_the_column(tmp_path):
    path = tmp_path / "odd.csv"
    export.write_csv(str(path), [series("g", "n", "a, b", [0.0], [1.0])])
    with open(path, newline="", encoding="utf-8") as handle:
        assert next(iter(csv.reader(handle))) == ["Time (s)", "g/n (a, b)"]


def test_nothing_to_write_writes_nothing(tmp_path):
    """Not a file with a row of headings, which reads as a successful export."""
    path = tmp_path / "empty.csv"
    assert export.write_csv(str(path), [series("g", "n", "", [], [])]) == (0, 0)
    assert not path.exists()


# --- the menu --------------------------------------------------------------------------
@pytest.fixture
def window(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    win = MainWindow()
    yield win
    win.close()


def test_the_file_menu_offers_it(window):
    labels = [a.text() for a in window.menuBar().actions()[0].menu().actions()]
    assert "Export signals..." in labels


def test_exporting_writes_the_signals_and_says_so(window, tmp_path, monkeypatch):
    window.signals.push("DBC Engine", "Speed", 0.01, 1500.0, "rpm")
    window.signals.push("DBC Engine", "Speed", 0.02, 1520.0, "rpm")
    path = tmp_path / "out.csv"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(path), ""))

    window._export_signals()
    assert path.exists()
    assert "Exported 1 signal(s), 2 row(s)" in window.log.toPlainText()
    with open(path, newline="", encoding="utf-8") as handle:
        assert next(iter(csv.reader(handle))) == ["Time (s)", "DBC Engine/Speed (rpm)"]


def test_nothing_decoded_says_so_rather_than_opening_a_dialog(window, monkeypatch):
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", lambda *a, **k: pytest.fail("nothing to save")
    )
    window._export_signals()
    assert "nothing has been decoded yet" in window.log.toPlainText()


def test_cancelling_the_dialog_writes_nothing(window, monkeypatch):
    window.signals.push("g", "n", 0.0, 1.0)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: ("", ""))
    before = window.log.toPlainText()
    window._export_signals()
    assert window.log.toPlainText() == before, "a cancelled export is not worth a line"
