"""Loading databases that real tools produce, and transmitting from them."""

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QMessageBox

from pycangui.core.dbc import DbcDecoder
from pycangui.ui.main_window import MainWindow
from pycangui.ui.tx_view import COL_DATA, COL_NAME

#: A signal with a VAL_ table *and* a start value.  cantools hands the start
#: value back as a NamedSignalValue -- "Run", not 1 -- which is neither a
#: string nor formattable as a number.  Most real databases name their
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

#: Two signals sharing bits.  cantools' strict check refuses the whole file;
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
    assert values["Mode"] == "Run", "the name is what the database says, so show it"
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


def test_the_choices_are_offered_in_the_tooltip(app, tmp_path, dbc_file, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    window = MainWindow()
    window._load_dbc(dbc_file(NAMED_VALUES))
    row = window.tx.add_message({"kind": "dbc", "message": "WithChoices", "period": 100})
    tip = window.tx.item(row).child(0).toolTip(COL_DATA)
    assert "0 = Idle" in tip and "2 = Fault" in tip
    assert not window.tx.item(row).child(1).toolTip(COL_DATA), "no choices, nothing to say"
    window.close()


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
        QMessageBox, "question", lambda *a, **k: (asked.append(a[2]), QMessageBox.Yes)[1]
    )
    assert window._load_dbc(dbc_file(OVERLAPPING), offer_relaxing=True)
    assert asked and "overlapping" in asked[0], "say what was actually wrong"
    assert window.dbc.loaded
    window.close()


def test_declining_leaves_the_database_unloaded(app, tmp_path, dbc_file, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    window = MainWindow()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Cancel)
    assert not window._load_dbc(dbc_file(OVERLAPPING), offer_relaxing=True)
    assert not window.dbc.loaded
    window.close()


def test_startup_never_asks(app, tmp_path, dbc_file, monkeypatch):
    """Databases restored at startup must not put a dialog over a window that
    is still opening -- they fail into the log and say why."""
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    window = MainWindow()
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: pytest.fail("startup must not ask")
    )
    assert not window._load_dbc(dbc_file(OVERLAPPING))
    assert "DBC load failed" in window.log.toPlainText()
    window.close()


def test_the_tools_switch_stops_the_asking(app, tmp_path, dbc_file, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    window = MainWindow()
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: pytest.fail("it should not need to ask")
    )
    window.relaxed_dbc.setChecked(True)
    assert window._load_dbc(dbc_file(OVERLAPPING), offer_relaxing=True)
    assert window.dbc.loaded
    assert "relaxed" in window.log.toPlainText()
    window.close()
