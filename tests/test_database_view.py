# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""The Database pane: what is in the databases that are loaded."""

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSettings

from pycangui import resources
from pycangui.core.bus import Frame
from pycangui.core.dbc import DbcDecoder
from pycangui.ui import database_view
from pycangui.ui.database_view import NOW, ROLE_SIGNAL, bits_of
from pycangui.ui.main_window import MainWindow

#: One database: a message with a Motorola signal and a gap, one whose two
#: signals share a bit, and one whose signal has named values.
SAMPLE = """VERSION ""
NS_ :
BS_:
BU_: ECU
BO_ 256 Mixed: 4 ECU
 SG_ Low : 0|12@1+ (0.5,-10) [-10|2000] "rpm" ECU
 SG_ Big : 23|16@0- (1,0) [0|0] "" ECU
BO_ 512 Shared: 2 ECU
 SG_ First : 0|9@1+ (1,0) [0|0] "" ECU
 SG_ Second : 8|8@1+ (1,0) [0|0] "" ECU
BO_ 1280 State: 1 ECU
 SG_ Mode : 0|8@1+ (1,0) [0|0] "" ECU
VAL_ 1280 Mode 0 "Off" 1 "On" ;
"""
#: A second database with a message of the first one's name.
CLASHING = """VERSION ""
NS_ :
BS_:
BU_: ECU
BO_ 768 Mixed: 1 ECU
 SG_ Other : 0|8@1+ (1,0) [0|0] "" ECU
BO_ 1024 Mine: 1 ECU
 SG_ Alone : 0|8@1+ (1,0) [0|0] "" ECU
"""


def signal(start, length, order):
    return SimpleNamespace(start=start, length=length, byte_order=order)


# --- where a signal's bits are ---------------------------------------------------------------
def test_an_intel_signal_runs_up_from_its_start_bit():
    assert bits_of(signal(6, 4, "little_endian")) == [(0, 6), (0, 7), (1, 0), (1, 1)]


def test_a_motorola_signal_runs_down_the_byte_and_on_from_the_top_of_the_next():
    """Its start bit is its most significant, and the numbers do not run on:
    after bit 0 of a byte comes bit 7 of the next, which is fifteen more."""
    assert bits_of(signal(1, 4, "big_endian")) == [(0, 1), (0, 0), (1, 7), (1, 6)]


# --- the pane ----------------------------------------------------------------------------------
@pytest.fixture
def window(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    win = MainWindow()
    yield win
    win.close()


@pytest.fixture
def sample(window, tmp_path):
    path = tmp_path / "sample.dbc"
    path.write_text(SAMPLE, encoding="utf-8")
    # Two of its signals share a bit, on purpose, which the strict check refuses.
    window.ctx.settings.set("dbc.strict", False)
    assert window._load_dbc(str(path))
    return window.database_view


def listed(view) -> list[str]:
    return [
        view.messages.topLevelItem(row).text(database_view.NAME)
        for row in range(view.messages.topLevelItemCount())
    ]


def test_it_lists_what_is_loaded_and_follows_a_database_coming_and_going(window, sample):
    assert listed(sample) == ["Mixed", "Shared", "State"]
    assert window._load_dbc(str(resources.path("demo.dbc")))
    assert {"Mixed", "Shared", "DriveStatus", "EEC1"} <= set(listed(sample))
    window._unload_dbcs()
    assert listed(sample) == []
    assert sample.message() is None, "and what was chosen has gone with it"


def test_find_is_by_message_identifier_or_signal(sample):
    sample.search.setText("shar")
    assert listed(sample) == ["Shared"]
    sample.search.setText("100")  # 256 is 0x100
    assert listed(sample) == ["Mixed"]
    sample.search.setText("big")
    assert listed(sample) == ["Mixed"]
    assert sample.messages.topLevelItem(0).isExpanded(), "opened, to show what was found"
    sample.search.setText("")
    assert listed(sample) == ["Mixed", "Shared", "State"]


def test_one_database_can_be_shown_alone(window, sample):
    window._load_dbc(str(resources.path("demo.dbc")))
    sample.file.setCurrentIndex(sample.file.findText("sample.dbc"))
    assert listed(sample) == ["Mixed", "Shared", "State"]
    sample.file.setCurrentIndex(0)
    assert len(listed(sample)) > 3


def test_choosing_a_message_shows_its_signals_and_where_they_are(sample):
    assert sample.select_message("Mixed")
    names = [
        sample.signals.topLevelItem(row).data(0, ROLE_SIGNAL)
        for row in range(sample.signals.topLevelItemCount())
    ]
    assert names == ["Low", "Big"]
    layout = sample.layout_view
    assert layout.owners(0, 0) == ["Low"] and layout.owners(1, 3) == ["Low"]
    assert layout.owners(1, 4) == [], "the four bits nobody has"
    assert layout.owners(2, 7) == ["Big"] and layout.owners(3, 0) == ["Big"]
    assert layout.overlaps() == []


def test_a_bit_two_signals_claim_is_found(sample):
    """What a list of start bits does not show, and the reason for the picture."""
    assert sample.select_message("Shared")
    assert sample.layout_view.overlaps() == [(1, 0)]
    assert sample.layout_view.owners(1, 0) == ["First", "Second"]


def test_a_signal_chosen_in_the_list_of_messages_is_chosen_on_the_right(sample):
    top = sample.messages.topLevelItem(listed(sample).index("Mixed"))
    sample.messages.setCurrentItem(top.child(1))
    assert sample.message().name == "Mixed"
    assert sample.signal_chosen() == "Big"


def test_now_is_the_value_last_decoded(window, sample):
    sample.select_message("State")
    row = sample.signals.topLevelItem(0)
    sample.select_signal("Mode")
    assert row.text(NOW) == "", "nothing has arrived"
    assert not sample.plot_btn.isEnabled(), "so there is nothing to plot either"

    window._decode_frames([Frame(1.0, "v", 0x500, False, False, True, bytes([0x01]))])
    sample._show_now()

    assert row.text(NOW) == "On (1)", "written as Signals and Plot writes it"
    assert sample.plot_btn.isEnabled()


# --- into the other panes ------------------------------------------------------------------------
def test_add_to_transmit_makes_the_row_the_transmit_pane_would(window, sample):
    sample.select_message("Mixed")
    before = window.tx.tree.topLevelItemCount()
    sample.transmit_btn.click()
    assert window.tx.tree.topLevelItemCount() == before + 1
    row = window.tx.tree.topLevelItem(before)
    assert row.childCount() == 2, "a box for each signal"


def test_show_in_trace_fills_in_the_traces_own_filter(window, sample):
    sample.select_message("Shared")
    sample.trace_btn.click()
    assert window.trace.search.text() == "Shared"


def test_plot_plots_the_signal_chosen(window, sample):
    sample.select_message("State")
    window._decode_frames([Frame(1.0, "v", 0x500, False, False, True, bytes([0x01]))])
    sample.select_signal("Mode")
    sample._show_now()
    sample.plot_btn.click()
    assert "DBC State/Mode" in window.plot.plotted()


def test_load_is_the_file_menus_own(window, sample, monkeypatch):
    from pycangui.ui import folders

    asked = []
    monkeypatch.setattr(folders, "open_file", lambda *a, **k: asked.append(a[2]) or "")
    sample.load_btn.click()
    assert asked == [folders.DBC], "the same dialog, opening in the same folder"


# --- two databases with the same message --------------------------------------------------------
def test_the_message_not_in_use_is_known_as_that(tmp_path):
    """The first database loaded is the one frames are decoded with, which
    nothing showed: the second one's message looked as good as the first's."""
    first, second = tmp_path / "a.dbc", tmp_path / "b.dbc"
    first.write_text(SAMPLE, encoding="utf-8")
    second.write_text(CLASHING, encoding="utf-8")
    dbc = DbcDecoder()
    dbc.load(first, strict=False)
    db = dbc.load(second)

    shadowed = dbc.shadowed()

    by_name = {msg.name: msg for msg in db.messages}
    assert shadowed == {id(by_name["Mixed"]): str(first)}


def test_whatever_shows_the_databases_is_told_when_they_change(tmp_path):
    path = tmp_path / "a.dbc"
    path.write_text(SAMPLE, encoding="utf-8")
    dbc = DbcDecoder()
    told = []
    dbc.listeners.append(lambda: told.append(len(dbc.databases)))
    dbc.load(path, strict=False)
    dbc.unload(path)
    assert told == [1, 0]


# --- a multiplexed message ----------------------------------------------------------------------
MULTIPLEXED = """VERSION ""
NS_ :
BS_:
BU_: ECU
BO_ 1536 Paged: 4 ECU
 SG_ Page M : 0|8@1+ (1,0) [0|0] "" ECU
 SG_ Speed m0 : 8|16@1+ (1,0) [0|0] "" ECU
 SG_ Volts m1 : 8|16@1+ (1,0) [0|0] "" ECU
 SG_ Always : 24|8@1+ (1,0) [0|0] "" ECU
VAL_ 1536 Page 0 "Drive" 1 "Supply" ;
"""


@pytest.fixture
def paged(window, tmp_path):
    path = tmp_path / "paged.dbc"
    path.write_text(MULTIPLEXED, encoding="utf-8")
    assert window._load_dbc(str(path))
    view = window.database_view
    assert view.select_message("Paged")
    return view


def test_a_multiplexed_message_is_drawn_for_one_value_of_its_multiplexer(paged):
    """Its signals take turns at the same bits: drawn all at once, they looked
    like a database in error."""
    assert [paged.mux.itemData(i) for i in range(paged.mux.count())] == [0, 1]
    assert paged.multiplexer_value() == 0
    layout = paged.layout_view
    assert layout.owners(1, 0) == ["Speed"] and layout.owners(3, 0) == ["Always"]
    assert layout.overlaps() == []

    paged.set_multiplexer_value(1)
    assert layout.owners(1, 0) == ["Volts"]
    assert layout.overlaps() == []


def test_choosing_a_signal_draws_a_value_it_comes_with(paged):
    paged.select_signal("Volts")
    assert paged.multiplexer_value() == 1
    paged.select_signal("Always")
    assert paged.multiplexer_value() == 1, "always there, so nothing to change"


def test_a_message_that_is_not_multiplexed_has_no_value_to_choose(sample):
    sample.select_message("Mixed")
    assert sample.multiplexer_value() is None
    assert sample.mux.isHidden()
