# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""A 29-bit identifier fits its column, whatever arrived first.

The column used to be fitted to its contents, and fitting measures a sample of
rows: on the demo bus, where CANopen's 11-bit ids come before J1939 claims an
address, it settled three digits wide and every J1939 id showed as "18...".
"""

from PySide6.QtWidgets import QHeaderView

from pycangui.core.context import Context
from pycangui.core.hooks import Hooks
from pycangui.ui import latest_model, trace_model
from pycangui.ui.trace_view import WIDEST_ID, TraceView


def test_an_eight_digit_id_fits_in_both_views(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    ctx = Context(log=print)
    view = TraceView(Hooks(ctx), ctx)
    for table, columns in (
        (view.table, trace_model.COLUMNS),
        (view.latest_table, latest_model.COLUMNS),
    ):
        column = columns.index("ID")
        header = table.horizontalHeader()
        assert header.sectionResizeMode(column) == QHeaderView.Fixed, "not left to a sample"
        assert header.sectionSize(column) > table.fontMetrics().horizontalAdvance(WIDEST_ID)
    view.deleteLater()


def test_the_data_column_starts_wide_enough_for_eight_bytes(app, tmp_path, monkeypatch):
    """Before any frame: fitted to its heading, it pushed the columns after it
    sideways the moment data arrived."""
    from pycangui.ui.trace_view import EIGHT_BYTES

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    ctx = Context(log=print)
    view = TraceView(Hooks(ctx), ctx)
    view.resize(1400, 400)
    view.show()
    app.processEvents()
    table = view.latest_table
    column = latest_model.COLUMNS.index("Data")
    header = table.horizontalHeader()
    assert header.sectionSize(column) > table.fontMetrics().horizontalAdvance(EIGHT_BYTES)
    assert header.sectionResizeMode(column) == QHeaderView.ResizeToContents, "CAN FD still fits"
    view.close()
    view.deleteLater()
