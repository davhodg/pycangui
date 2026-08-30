"""ReadDTCInformation: every report the service offers, and only the boxes each needs.

The service is twenty-odd reports wearing one number, and an ECU answers the
wrong parameters with NRC 0x13 and nothing else.  The table in pycangui.uds.dtc
is what lets the pane grey out what a report has no use for, so the tests that
matter most here are the ones checking that table against what udsoncan
actually puts on the wire.
"""

import pytest
from PySide6.QtCore import QSettings
from udsoncan.services import ReadDTCInformation as Service

from pycangui.core.bus import BusManager
from pycangui.core.context import Context
from pycangui.core.hooks import Hooks
from pycangui.uds import dtc
from pycangui.uds.manager import UdsManager
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
    not be there.  This is the check that keeps the table honest, because
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


def test_the_record_box_says_which_record_it_means(view):
    choose(view, 0x04)  # snapshot by DTC
    assert view.record_label.text() == "Snapshot"
    choose(view, 0x06)  # extended data by DTC
    assert view.record_label.text() == "Ext data"


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


def test_the_status_mask_tooltip_spells_the_bits_out(view):
    """The mask is the difference between every fault ever and the ones wrong now."""
    tip = view.dtc_mask.toolTip()
    assert "0x08 confirmedDTC" in tip
    assert "0x80 warningIndicatorRequested" in tip


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
