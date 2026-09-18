# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""ReadDTCInformation: every report the service offers, and only the boxes each needs.

The service is twenty-odd reports wearing one number, and an ECU answers the
wrong parameters with NRC 0x13 and nothing else. The table in pycangui.uds.dtc
is what lets the pane grey out what a report has no use for, so the tests that
matter most here are the ones checking that table against what udsoncan
actually puts on the wire.
"""

import pytest
from PySide6.QtCore import QSettings
from udsoncan.services import ReadDTCInformation as Service

from pycangui.core.bus import BusManager, Frame
from pycangui.core.context import Context
from pycangui.core.hooks import Hooks
from pycangui.uds import NO_ID, dtc
from pycangui.uds.manager import UdsManager
from pycangui.ui.main_window import MainWindow
from pycangui.ui.uds_view import UdsView

#: Distinct values, so which one landed in which byte of the request is
#: visible in the bytes that come out.
PROBE = {
    dtc.STATUS: 0xAA,
    dtc.SEVERITY: 0xBB,
    dtc.DTC: 0x112233,
    dtc.SNAPSHOT: 0x44,
    dtc.EXTENDED: 0x55,
    dtc.MEMORY: 0x66,
    dtc.GROUP: 0x77,
}


@pytest.fixture
def view(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    ctx = Context(log=print)
    manager = UdsManager(BusManager(), Hooks(ctx), ctx)
    widget = UdsView(manager, ctx)
    yield widget
    manager.shutdown()


def choose(view, subfunction):
    view.report.setCurrentIndex(view.report.findData(subfunction))


# --- the table against the wire -------------------------------------------------------
@pytest.mark.parametrize("report", dtc.REPORTS, ids=lambda r: f"{r.subfunction:02X}")
def test_every_report_takes_exactly_what_the_table_says(report):
    """Build the request with only the parameters listed, and see it come out.

    A missing entry raises; a superfluous one shows up as bytes that should
    not be there. This is the check that keeps the table honest, because
    getting it wrong is invisible until an ECU answers 0x13.
    """
    params = {field: PROBE[field] for field in report.needs}
    try:
        request = Service.make_request(report.subfunction, **params)
    except NotImplementedError as exc:
        assert report.note, f"0x{report.subfunction:02X} is refused and the table does not say so"
        assert "2020" in str(exc)
        return
    expected = b"".join(
        PROBE[field].to_bytes(3 if field == dtc.DTC else 1, "big") for field in report.needs
    )
    assert set(request.data or b"") == set(expected), (
        f"0x{report.subfunction:02X} sent {(request.data or b'').hex()} for {report.needs}"
    )


def test_the_reports_udsoncan_cannot_fully_build_are_marked(app):
    """0x1A and 0x56 are in the standard and only half in the library."""
    for subfunction in (0x1A, 0x56):
        assert dtc.BY_SUBFUNCTION[subfunction].note, f"0x{subfunction:02X} should carry a caveat"


def test_no_report_wants_both_kinds_of_record():
    """Which is why the pane offers one box for the two."""
    for report in dtc.REPORTS:
        assert not (dtc.SNAPSHOT in report.needs and dtc.EXTENDED in report.needs)


def test_the_list_covers_the_service(app):
    """Every subfunction udsoncan names is offered, so nothing is quietly missing."""
    named = {
        value
        for name, value in vars(Service.Subfunction).items()
        if isinstance(value, int) and name[0].islower()
    }
    assert named <= set(dtc.BY_SUBFUNCTION), sorted(named - set(dtc.BY_SUBFUNCTION))


# --- the pane -------------------------------------------------------------------------
def test_only_the_boxes_a_report_needs_are_live(view):
    choose(view, 0x02)  # DTCs by status mask
    assert view.dtc_mask.isEnabled()
    assert not view.severity.isEnabled()
    assert not view.dtc_number.isEnabled()
    assert not view.record.isEnabled()

    choose(view, 0x06)  # extended data by DTC
    assert view.dtc_number.isEnabled() and view.record.isEnabled()
    assert not view.dtc_mask.isEnabled()

    choose(view, 0x0A)  # supported DTCs: nothing at all
    assert not any(
        w.isEnabled()
        for w in (view.dtc_mask, view.severity, view.dtc_number, view.record, view.memory)
    )


def test_the_user_memory_reports_want_a_memory(view):
    choose(view, 0x17)
    assert view.dtc_mask.isEnabled() and view.memory.isEnabled()
    choose(view, 0x02)
    assert not view.memory.isEnabled()


def test_the_wwh_obd_reports_want_a_functional_group(view):
    choose(view, 0x55)
    assert view.functional_group.isEnabled()
    assert not view.dtc_mask.isEnabled()


def test_reading_sends_only_the_parameters_that_report_takes(view, monkeypatch):
    sent = {}
    monkeypatch.setattr(
        view.manager,
        "read_dtc_information",
        lambda subfunction, **params: sent.update({"sub": subfunction, **params}),
    )
    choose(view, 0x06)
    view.dtc_number.setText("112233")
    view.record.setText("55")
    view._read_dtcs()
    assert sent == {"sub": 0x06, dtc.DTC: 0x112233, dtc.EXTENDED: 0x55}


def test_the_dtc_setting_toggle_says_what_it_asked_for(view, monkeypatch):
    asked = []
    monkeypatch.setattr(view.manager, "set_dtc_setting", asked.append)
    assert view.dtc_setting.isChecked(), "an ECU records faults unless told not to"
    view.dtc_setting.setChecked(False)
    view.dtc_setting.setChecked(True)
    assert asked == [False, True]


def test_the_clear_group_can_be_narrowed(view, monkeypatch):
    """FFFFFF is everything; a group erases only part of it."""
    cleared = []
    monkeypatch.setattr(view.manager, "clear_dtcs", cleared.append)
    assert view.clear_group.text() == "FFFFFF"
    view.clear_group.setText("FFFF33")
    for button in view.findChildren(type(view.open_btn)):
        if button.text() == "Clear":
            button.click()
    assert cleared == [0xFFFF33]


def test_the_standard_edition_reaches_the_client(view):
    """The 2020 edition withdrew the mirror memory reports and udsoncan enforces it."""
    assert view.manager.standard_version == dtc.DEFAULT_STANDARD
    view.standard.setCurrentText("2013")
    assert view.manager.standard_version == 2013


def test_a_withdrawn_report_is_refused_with_the_reason(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    ctx = Context(log=print)
    manager = UdsManager(BusManager(), Hooks(ctx), ctx)
    lines = []
    manager.result.connect(lines.append)

    class Immediate:
        def submit(self, fn, callback):
            callback(fn(), None)

        def stop(self):
            pass

    class Refuses:
        def read_dtc_information(self, subfunction, **params):
            raise NotImplementedError(
                f"Subfunction 0x{subfunction:02x} is not allowed by ISO-14229:2020."
            )

        def close(self):
            pass

    manager._worker = Immediate()
    manager.client = Refuses()
    manager.read_dtc_information(0x0F, status_mask=0xFF)
    assert "not allowed by ISO-14229:2020" in lines[-1]
    assert "Mirror memory" in lines[-1], "and which report it was"
    manager.client = None


# --- choosing rather than typing -------------------------------------------------------
def test_a_session_can_be_chosen_including_one_of_your_own(view, monkeypatch):
    """0x40 to 0x5F are the manufacturer's, so only a hook can name them."""
    codes = [view.session.itemData(i) for i in range(view.session.count())]
    assert codes == [1, 2, 3, 4], "ISO's four to begin with"

    asked = []
    monkeypatch.setattr(view.manager, "change_session", asked.append)
    view.session.setCurrentIndex(codes.index(2))
    for button in view.findChildren(type(view.open_btn)):
        if button.text() == "Change":
            button.click()
    assert asked == [2]


def test_the_did_box_offers_the_named_ones_and_still_takes_anything(view):
    from pycangui.ui.uds_view import _picked

    assert view.did.isEditable(), "most identifiers on a real ECU are not on any list"
    labels = [view.did.itemText(i) for i in range(view.did.count())]
    assert any(label.startswith("F190  VIN") for label in labels)
    assert _picked(view.did) == 0xF190, "a chosen entry is 'F190  VIN', so take the number"

    view.did.setCurrentText("0101")
    assert _picked(view.did) == 0x0101, "and a typed one is just the number"


def test_the_routine_box_offers_the_four_iso_names_and_yours(view):
    from pycangui.ui.uds_view import _picked

    numbers = [view.routine.itemData(i) for i in range(view.routine.count())]
    assert 0xFF00 in numbers, "erase memory is the one ISO names for flashing"
    assert 0x0202 in numbers, "and check memory comes from ROUTINE_NAMES"
    view.routine.setCurrentText("0203")
    assert _picked(view.routine) == 0x0203


def test_a_typed_identifier_is_remembered(app, tmp_path, monkeypatch):
    """An editable list is only useful if what you typed comes back."""
    from PySide6.QtCore import QSettings

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    ctx = Context(log=print)
    first = UdsView(UdsManager(BusManager(), Hooks(ctx), ctx), ctx)
    first.did.setCurrentText("0101")

    second = UdsView(UdsManager(BusManager(), Hooks(ctx), ctx), Context(log=print))
    assert second.did.currentText() == "0101"


# --- the layout -----------------------------------------------------------------------
def test_each_label_sits_directly_above_its_own_box(view):
    """Six label-and-box pairs across a stretched row put every label nearer
    its neighbour's box than its own, which is the confusing part."""
    grid = view.dtc_mask.parentWidget().layout()
    for _field, label, widget in view._dtc_fields:
        label_row, label_column, _, _ = grid.getItemPosition(grid.indexOf(label))
        box_row, box_column, _, _ = grid.getItemPosition(grid.indexOf(widget))
        assert label_column == box_column, f"{label.text()} is not over its box"
        assert box_row == label_row + 1


def test_a_greyed_box_takes_its_label_with_it(view):
    """A live-looking name over a dead box is what makes a form look broken."""
    choose(view, 0x0A)  # supported DTCs: no parameters at all
    assert not any(label.isEnabled() for _f, label, _w in view._dtc_fields)
    choose(view, 0x02)  # by status mask
    live = {label.text() for _f, label, _w in view._dtc_fields if label.isEnabled()}
    assert live == {"Status mask"}


def test_the_dtc_box_starts_at_all_of_them(view):
    """FFFFFF is how most ECUs are asked for every fault they hold."""
    assert view.dtc_number.text() == "FFFFFF"


# --- what the trace is told is UDS ------------------------------------------------------
def test_only_the_configured_addresses_are_called_uds(app, tmp_path, monkeypatch):
    """The whole 0x7E0 to 0x7EF range used to be read as UDS wherever it
    turned up, which is a guess on a bus using those ids for something else."""
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    window = MainWindow()
    uds = window.uds

    def named(can_id):
        return uds.classify(Frame(0.0, "CAN", can_id, False, False, True, b"\x00"))

    assert named(0x7E0) == "UDS req", "the default addresses are the standard ones"
    assert named(0x7E8) == "UDS resp"
    assert named(0x7DF) == "UDS func"
    assert named(0x7E1) is None, "another ECU's addresses are not this one's"

    uds.config.tx_id = NO_ID
    uds.config.rx_id = NO_ID
    uds.config.functional_id = NO_ID
    assert named(0x7E0) is None, "cleared means this bus has no UDS on it"
    window.close()


def test_the_uds_pane_waits_for_addresses_before_opening(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    window = MainWindow()
    view = window.uds_view

    assert view.open_btn.isEnabled(), "the standard addresses are filled in to start with"
    assert view.functional_id.text() == "7DF", "and functional addressing has a box of its own"

    view.tx_id.setText("")
    assert not view.open_btn.isEnabled(), "nothing to open a session with"
    view.tx_id.setText("7E0")
    assert view.open_btn.isEnabled()
    window.close()
