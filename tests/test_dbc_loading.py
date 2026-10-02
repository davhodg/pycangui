# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Loading databases that real tools produce, and transmitting from them."""

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QComboBox, QMessageBox

from pycangui.core.dbc import DbcDecoder
from pycangui.ui import messages
from pycangui.ui.main_window import MainWindow
from pycangui.ui.tx_view import COL_DATA, COL_NAME, ROLE_CHOICES, named

#: A signal with a VAL_ table *and* a start value. cantools hands the start
#: value back as a NamedSignalValue -- "Run", not 1 -- which is neither a
#: string nor formattable as a number. Most real databases name their
#: enumerations, so this is not an exotic case.
NAMED_VALUES = """VERSION ""
NS_ :
BS_:
BU_: ECU
BO_ 100 WithChoices: 8 ECU
 SG_ Mode : 0|8@1+ (1,0) [0|3] "" ECU
 SG_ Plain : 8|8@1+ (1,0) [0|255] "" ECU
VAL_ 100 Mode 0 "Idle" 1 "Run" 2 "Fault" ;
BA_DEF_ SG_ "GenSigStartValue" INT 0 65535;
BA_DEF_DEF_ "GenSigStartValue" 0;
BA_ "GenSigStartValue" SG_ 100 Mode 1;
"""

#: Two signals sharing bits. cantools' strict check refuses the whole file;
#: the messages in it are perfectly usable.
OVERLAPPING = """VERSION ""
NS_ :
BS_:
BU_: ECU
BO_ 256 Overlap: 8 ECU
 SG_ First : 0|16@1+ (1,0) [0|0] "" ECU
 SG_ Second : 8|16@1+ (1,0) [0|0] "" ECU
"""


@pytest.fixture
def dbc_file(tmp_path):
    def write(text, name="test.dbc"):
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        return str(path)

    return write


# --- named signal values -------------------------------------------------------------
def test_a_named_start_value_can_be_added_to_the_transmit_list(
    app, tmp_path, dbc_file, monkeypatch
):
    """This raised a TypeError and the row never appeared."""
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    window = MainWindow()
    assert window._load_dbc(dbc_file(NAMED_VALUES))

    row = window.tx.add_message({"kind": "dbc", "message": "WithChoices", "period": 100})
    item = window.tx.item(row)
    values = {item.child(i).text(COL_NAME): item.child(i).text(COL_DATA) for i in range(2)}
    assert values["Mode"] == named(1, "Run"), "the database's name, and the number behind it"
    assert item.text(COL_DATA).startswith("01"), "and it encodes to the value behind it"
    window.close()


def test_a_name_can_be_typed_and_a_typo_is_not_silently_zero(app, tmp_path, dbc_file, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    window = MainWindow()
    window._load_dbc(dbc_file(NAMED_VALUES))
    row = window.tx.add_message({"kind": "dbc", "message": "WithChoices", "period": 100})
    item = window.tx.item(row)

    item.child(0).setText(COL_DATA, "Fault")
    window.tx._encode_row(row)
    assert item.text(COL_DATA).startswith("02"), "cantools maps the name back"

    before = item.text(COL_DATA)
    item.child(0).setText(COL_DATA, "Nonsense")
    window.tx._encode_row(row)
    assert item.text(COL_DATA) == before, "a name that means nothing must not transmit zero"
    assert "encode failed" in window.log.toPlainText()
    window.close()


@pytest.fixture
def choices_row(app, tmp_path, dbc_file, monkeypatch):
    """A transmit row whose first signal, Mode, has named values."""
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    window = MainWindow()
    window._load_dbc(dbc_file(NAMED_VALUES))
    row = window.tx.add_message({"kind": "dbc", "message": "WithChoices", "period": 100})
    yield window, row, window.tx.item(row)
    window.close()


def test_a_signal_with_named_values_is_picked_from_a_list(app, choices_row):
    window, _row, item = choices_row
    mode, other = item.child(0), item.child(1)
    assert [name for _number, name in mode.data(COL_DATA, ROLE_CHOICES)] == ["Idle", "Run", "Fault"]
    assert not other.data(COL_DATA, ROLE_CHOICES), "no names, so an ordinary box"

    tree = window.tx.tree
    tree.edit(tree.indexFromItem(mode, COL_DATA))
    app.processEvents()
    box = tree.findChild(QComboBox)
    assert box is not None and box.isEditable(), "a list, which can still be typed into"
    offered = [box.itemText(i) for i in range(box.count())]
    assert offered == [named(0, "Idle"), named(1, "Run"), named(2, "Fault")]

    box.setCurrentIndex(2)
    box.activated.emit(2)
    app.processEvents()
    assert mode.text(COL_DATA) == named(2, "Fault"), "picked, and taken at once"
    assert item.text(COL_DATA).startswith("02")


@pytest.mark.parametrize("typed", ["2", "Fault", "Fault (2)", " 2 "])
def test_however_it_is_put_in_it_is_shown_as_name_and_number(app, choices_row, typed):
    _window, _row, item = choices_row
    item.child(0).setText(COL_DATA, typed)
    assert item.child(0).text(COL_DATA) == named(2, "Fault")
    assert item.text(COL_DATA).startswith("02"), "and the number is what is sent"


def test_a_number_the_table_does_not_name_is_sent_as_that_number(app, choices_row):
    _window, _row, item = choices_row
    item.child(0).setText(COL_DATA, "3")
    assert item.child(0).text(COL_DATA) == "3"
    assert item.text(COL_DATA).startswith("03")


def test_the_named_value_comes_back_with_the_row(app, choices_row):
    window, row, item = choices_row
    item.child(0).setText(COL_DATA, "Fault")
    saved = window.tx._spec(row)
    again = window.tx.add_message(saved)
    assert window.tx.item(again).child(0).text(COL_DATA) == named(2, "Fault")
    assert window.tx.item(again).text(COL_DATA).startswith("02")

    # A row saved before names and numbers were shown together held the bare name.
    saved["signals"]["Mode"] = "Run"
    older = window.tx.add_message(saved)
    assert window.tx.item(older).child(0).text(COL_DATA) == named(1, "Run")


# --- strict checking -----------------------------------------------------------------
def test_strict_is_the_default(app, dbc_file):
    decoder = DbcDecoder()
    with pytest.raises(Exception, match="overlapping"):
        decoder.load(dbc_file(OVERLAPPING))
    assert decoder.load(dbc_file(OVERLAPPING), strict=False).messages


def test_a_strict_failure_is_offered_as_a_question(app, tmp_path, dbc_file, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    window = MainWindow()
    asked = []
    monkeypatch.setattr(
        messages, "question", lambda *a, **k: (asked.append(a[2]), QMessageBox.Yes)[1]
    )
    assert window._load_dbc(dbc_file(OVERLAPPING), offer_relaxing=True)
    assert asked and "overlapping" in asked[0], "say what was actually wrong"
    assert window.dbc.loaded
    window.close()


def test_declining_leaves_the_database_unloaded(app, tmp_path, dbc_file, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    window = MainWindow()
    monkeypatch.setattr(messages, "question", lambda *a, **k: QMessageBox.Cancel)
    assert not window._load_dbc(dbc_file(OVERLAPPING), offer_relaxing=True)
    assert not window.dbc.loaded
    window.close()


def test_startup_never_asks(app, tmp_path, dbc_file, monkeypatch):
    """Databases restored at startup must not put a dialog over a window that
    is still opening -- they fail into the log and say why."""
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    window = MainWindow()
    monkeypatch.setattr(messages, "question", lambda *a, **k: pytest.fail("startup must not ask"))
    before = window.log.toPlainText()
    assert not window._load_dbc(dbc_file(OVERLAPPING))
    assert window.log.toPlainText() != before
    window.close()


def test_strict_is_on_by_default(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    window = MainWindow()
    assert window.strict_dbc.isChecked(), "a database should be checked unless you say not to"
    window.close()


def test_turning_the_check_off_stops_the_asking(app, tmp_path, dbc_file, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    window = MainWindow()
    monkeypatch.setattr(
        messages, "question", lambda *a, **k: pytest.fail("it should not need to ask")
    )
    window.strict_dbc.setChecked(False)
    assert window._load_dbc(dbc_file(OVERLAPPING), offer_relaxing=True)
    assert window.dbc.loaded
    window.close()


# --- removing one database at a time ---------------------------------------------------
def test_one_database_can_be_removed_without_the_others(app, tmp_path, dbc_file, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    window = MainWindow()
    first = dbc_file(NAMED_VALUES, "first.dbc")
    second = dbc_file(NAMED_VALUES.replace("WithChoices", "Other"), "second.dbc")
    for path in (first, second):
        assert window._load_dbc(path)
        window.ctx.settings.set("dbc.paths", [*window.ctx.settings.get("dbc.paths", []), path])

    window._remove_dbc(first, first)

    assert first not in window.dbc.databases
    assert second in window.dbc.databases, "removing one must leave the rest loaded"
    assert window.ctx.settings.get("dbc.paths") == [second], "and it stops being remembered"
    window.close()


def test_a_database_that_has_moved_can_be_removed(app, tmp_path, dbc_file, monkeypatch):
    """The whole point: a file that has gone cannot be loaded, so removing it
    must not depend on it being loaded."""
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    window = MainWindow()
    gone = str(tmp_path / "moved-away.dbc")
    window.ctx.settings.set("dbc.paths", [gone])

    window._build_dbc_menu()
    labels = [action.text() for action in window.dbc_menu.actions()]
    assert any("moved-away.dbc" in label for label in labels), "it is on the menu"

    window._remove_dbc(gone, gone)
    assert window.ctx.settings.get("dbc.paths") == []
    window.close()


def test_a_missing_database_says_where_to_remove_it(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    window = MainWindow()
    before = window.log.toPlainText()

    assert not window._load_dbc(str(tmp_path / "not-here.dbc"))

    assert window.log.toPlainText() != before, "it says so rather than failing silently"
    window.close()


def test_the_menu_lists_what_is_loaded_and_offers_remove_all(app, tmp_path, dbc_file, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    window = MainWindow()
    assert window._load_dbc(dbc_file(NAMED_VALUES))

    window._build_dbc_menu()
    actions = [a for a in window.dbc_menu.actions() if not a.isSeparator()]
    assert len(actions) == 2, "the database, and Remove all"
    actions[-1].trigger()

    assert not window.dbc.loaded, "Remove all unloads everything"
    assert window.ctx.settings.get("dbc.paths") == []
    window.close()


def test_removing_a_database_takes_its_signals_away(app, tmp_path, dbc_file, monkeypatch):
    """Reported: the Signals list still showed a removed database's signals."""
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    window = MainWindow()
    # The first has WithChoices and a message of its own; the second has
    # WithChoices too.
    own = 'BO_ 102 OnlyFirst: 8 ECU\n SG_ A : 0|8@1+ (1,0) [0|255] "" ECU\nVAL_ 100'
    first = dbc_file(NAMED_VALUES.replace("VAL_ 100", own), "first.dbc")
    second = dbc_file(NAMED_VALUES, "second.dbc")
    for path in (first, second):
        assert window._load_dbc(path)
    for group in ("DBC WithChoices", "DBC OnlyFirst"):
        window.signals.push(group, "Mode", 0.0, 1.0)

    window._remove_dbc(first, first)
    assert window.signals.groups() == ["DBC WithChoices"], (
        "its own message goes; one the other database still has stays"
    )
    window._unload_dbcs()
    assert window.signals.groups() == [], "and with every database gone, all of them go"
    window.close()


def test_a_database_describing_a_tpdo_wins_over_the_pdo_decode(app, tmp_path, monkeypatch):
    """Reported: a CANopen device's TPDO showed in Signals twice, once from
    its DBC with scaling and once from the PDO decode raw, and the numbers
    disagreed. The database was loaded to say what the frame means."""
    from types import SimpleNamespace

    from pycangui import resources

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    window = MainWindow()
    ids = {"TxPDO1": 0x185, "TxPDO2": 0x285}  # demo.dbc has 0x185 and not 0x285
    monkeypatch.setattr(window.canopen, "tpdo_cob_id", lambda _node, name: ids.get(name))
    tpdo = [
        SimpleNamespace(direction="TPDO", name=name, cob_id=cob_id) for name, cob_id in ids.items()
    ]
    monkeypatch.setattr(window.canopen, "pdo_configs", lambda _node: tpdo)
    network = SimpleNamespace(nodes={5: None}, listeners=[])  # listeners, for closing
    monkeypatch.setattr(window.canopen, "network", network)

    window._on_pdo_update(5, "TxPDO1", {"Odometer": 12345})
    assert window.signals.groups() == ["CANopen node 5 TxPDO1"], "decoded while no DBC says"

    assert window._load_dbc(resources.path("demo.dbc"))
    assert window.signals.groups() == [], "listed before the DBC, and dropped once it loads"
    window._on_pdo_update(5, "TxPDO1", {"Odometer": 12345})
    window._on_pdo_update(5, "TxPDO2", {"Temperature": 40})
    assert window.signals.groups() == ["CANopen node 5 TxPDO2"], (
        "the DBC's TPDO is left to it, and one it does not describe is still decoded"
    )
    window.close()


# --- two databases with the same message ----------------------------------------------------
PUMP = """VERSION ""
NS_ :
BS_:
BU_:
BO_ {ident} {name}: 2 Vector__XXX
 SG_ Speed : 0|16@1+ (1,0) [0|0] "rpm" Vector__XXX
"""


def write_dbc(folder, file_name, name, ident):
    path = folder / file_name
    path.write_text(PUMP.format(name=name, ident=ident), encoding="utf-8")
    return path


def test_a_name_or_an_identifier_already_loaded_is_a_clash(tmp_path):
    from pycangui.core.dbc import DbcDecoder

    decoder = DbcDecoder()
    first = write_dbc(tmp_path, "first.dbc", "Pump", 0x100)
    decoder.load(first)
    assert decoder.clashes(first) == [], "nothing was loaded before it"

    same_name = write_dbc(tmp_path, "same_name.dbc", "Pump", 0x200)
    decoder.load(same_name)
    assert decoder.clashes(same_name) == [("Pump", str(first))]
    assert decoder.source_of("Pump") == str(first), "and the earlier file is the one used"

    same_id = write_dbc(tmp_path, "same_id.dbc", "Motor", 0x100)
    decoder.load(same_id)
    assert decoder.clashes(same_id) == [("0x100", str(first))]

    apart = write_dbc(tmp_path, "apart.dbc", "Fan", 0x300)
    decoder.load(apart)
    assert decoder.clashes(apart) == []


def test_loading_a_clashing_database_says_so(app, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings

    from pycangui.core.events import WARNING
    from pycangui.ui.main_window import MainWindow

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path / "home"))
    QSettings().clear()
    window = MainWindow()
    try:
        said = []
        window.events.posted.connect(lambda *args: said.append(args))
        assert window._load_dbc(str(write_dbc(tmp_path, "first.dbc", "Pump", 0x100)))
        before = len([a for a in said if WARNING in a])
        assert window._load_dbc(str(write_dbc(tmp_path, "second.dbc", "Pump", 0x200)))
        assert len([a for a in said if WARNING in a]) == before + 1
    finally:
        window.close()
