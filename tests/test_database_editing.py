# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Changing a database in the Database pane: the dialogs, the changes, Save as."""

import shutil
from pathlib import Path

import cantools
import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QDialog, QMessageBox

from pycangui.ui import database_view, folders, messages
from pycangui.ui.database_edit import MessageDialog, SignalDialog

FILES = Path(__file__).parent / "dbc"


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    from pycangui.ui.main_window import MainWindow

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path / "home"))
    QSettings().clear()
    win = MainWindow()
    yield win
    win.close()


@pytest.fixture
def view(window, tmp_path):
    """The pane with a copy of a feature file loaded, and its first message chosen."""
    path = tmp_path / "network.dbc"
    shutil.copy(FILES / "tables_and_comments.dbc", path)
    assert window._load_dbc(str(path))
    pane = window.database_view
    assert pane.select_message("GearboxData")
    return pane


def answering(monkeypatch, dialog_class, *fills):
    """Each time the dialog is opened, fill it in as the next of ``fills``
    does and press OK -- or Cancel, for a fill of None."""
    queue = list(fills)

    def exec_(self):
        fill = queue.pop(0) if queue else None
        if fill is None:
            self.reject()
            return QDialog.Rejected
        fill(self)
        self._accept()
        return self.result()

    monkeypatch.setattr(dialog_class, "exec", exec_)


def listed(view):
    return [
        view.messages.topLevelItem(r).text(database_view.NAME)
        for r in range(view.messages.topLevelItemCount())
    ]


# --- the dialogs alone -------------------------------------------------------------------------
def test_a_dialog_opened_and_closed_with_ok_changes_nothing(app):
    db = cantools.database.load_file(str(FILES / "tables_and_comments.dbc"), sort_signals=None)
    msg = db.get_message_by_name("GearboxData")
    for dialog in (MessageDialog(None, msg), SignalDialog(None, msg.name, msg.signals[0])):
        dialog._accept()
        assert dialog.result() == QDialog.Accepted and dialog.changes() == []


def test_a_field_that_is_not_a_number_keeps_the_dialog_open(app):
    db = cantools.database.load_file(str(FILES / "tables_and_comments.dbc"), sort_signals=None)
    msg = db.get_message_by_name("EngineData")
    dialog = SignalDialog(None, msg.name, msg.signals[0])
    dialog.scale.setText("a quarter")
    dialog._accept()
    assert dialog.result() != QDialog.Accepted
    assert dialog.problem.isVisibleTo(dialog) and dialog.problem.text()


def test_only_what_was_changed_is_asked_for(app):
    db = cantools.database.load_file(str(FILES / "tables_and_comments.dbc"), sort_signals=None)
    msg = db.get_message_by_name("EngineData")
    dialog = SignalDialog(None, msg.name, msg.get_signal_by_name("Throttle"))
    dialog.unit.setText("percent")
    dialog.name.setText("Pedal")
    dialog._accept()
    asked = [(c.field, c.value) for c in dialog.changes()]
    assert asked == [("unit", "percent"), ("name", "Pedal")], "the rename last"


# --- in the pane -------------------------------------------------------------------------------
def test_a_message_changed_is_shown_changed_and_nothing_decodes_differently(
    window, view, monkeypatch
):
    answering(monkeypatch, MessageDialog, lambda d: d.cycle.setValue(25))
    view.edit_message()
    assert view.editing().message("GearboxData").cycle_time == 25
    assert view.edit_bar.isVisibleTo(view)
    row = view.messages.currentItem()
    assert row.font(database_view.NAME).italic(), "and its tooltip says so"
    assert window.dbc.message_by_name("GearboxData").cycle_time is None, "not until it is saved"


def test_a_signal_is_changed_from_its_dialog(view, monkeypatch):
    def fill(dialog):
        dialog.scale.setText("0.5")
        dialog.add_value(9, "Fault")

    answering(monkeypatch, SignalDialog, fill)
    view.edit_signal("Gear")
    gear = view.editing().message("GearboxData").get_signal_by_name("Gear")
    assert gear.scale == 0.5 and gear.choices[9] == "Fault"
    assert view.signal_chosen() == "Gear", "still the one chosen"


def test_a_rename_is_followed_in_the_list(view, monkeypatch):
    answering(monkeypatch, MessageDialog, lambda d: d.name.setText("Gearbox"))
    view.edit_message()
    assert "Gearbox" in listed(view) and "GearboxData" not in listed(view)
    assert view.message().name == "Gearbox"


def test_a_refused_change_is_said_and_the_dialog_opens_again(view, monkeypatch):
    said = []
    monkeypatch.setattr(messages, "warning", lambda *a, **k: said.append(a))
    answering(monkeypatch, MessageDialog, lambda d: d.name.setText("EngineData"), None)
    view.edit_message()
    assert said, "a name another message has"
    assert not view.editing().changed


def test_messages_and_signals_are_added_and_removed(view, monkeypatch):
    def new_message(dialog):
        dialog.name.setText("Spare")
        dialog.ident.setText("400")
        dialog.length.setValue(2)

    answering(monkeypatch, MessageDialog, new_message)
    view.add_message()
    assert "Spare" in listed(view) and view.message().name == "Spare"

    def new_signal(dialog):
        dialog.name.setText("Flag")
        dialog.bits.setValue(1)

    answering(monkeypatch, SignalDialog, new_signal)
    view.add_signal()
    assert [s.name for s in view.editing().message("Spare").signals] == ["Flag"]
    view.remove_signal()
    assert view.editing().message("Spare").signals == []
    view.remove_message()
    assert "Spare" not in listed(view)


def test_undo_and_discard(view, monkeypatch):
    answering(monkeypatch, MessageDialog, lambda d: d.cycle.setValue(25))
    view.edit_message()
    view.undo()
    assert not view.editing().changed and not view.edit_bar.isVisibleTo(view)

    answering(monkeypatch, MessageDialog, lambda d: d.cycle.setValue(30))
    view.edit_message()
    monkeypatch.setattr(messages, "question", lambda *a, **k: QMessageBox.Discard)
    view.discard()
    assert view.editing() is None and not view.edit_bar.isVisibleTo(view)


def test_save_as_writes_a_new_file_and_it_is_loaded_in_place(window, view, tmp_path, monkeypatch):
    original = view._path
    answering(monkeypatch, MessageDialog, lambda d: d.cycle.setValue(25))
    view.edit_message()
    target = tmp_path / "changed.dbc"
    monkeypatch.setattr(folders, "save_file", lambda *a, **k: str(target))
    monkeypatch.setattr(messages, "question", lambda *a, **k: QMessageBox.Yes)
    view.save_as()

    assert (
        target.is_file()
        and Path(original).read_bytes() == (FILES / "tables_and_comments.dbc").read_bytes()
    ), "the file it came from untouched"
    assert str(target) in window.dbc.databases and original not in window.dbc.databases
    assert window.dbc.message_by_name("GearboxData").cycle_time == 25
    assert view.editing() is None and not view.edit_bar.isVisibleTo(view)
    remembered = window.ctx.settings.get("dbc.paths")
    assert len(remembered) == 1 and Path(remembered[0]).name == "changed.dbc"


def test_a_transmit_row_naming_a_renamed_message_is_said(window, view, tmp_path, monkeypatch):
    window.tx.add_dbc_message("GearboxData")
    answering(monkeypatch, MessageDialog, lambda d: d.name.setText("Gearbox"))
    view.edit_message()
    monkeypatch.setattr(folders, "save_file", lambda *a, **k: str(tmp_path / "renamed.dbc"))
    monkeypatch.setattr(messages, "question", lambda *a, **k: QMessageBox.Yes)
    warned = []
    window.events.posted.connect(lambda text, level: warned.append(level))
    view.save_as()
    assert "warning" in warned


def test_only_a_dbc_is_changed(window, tmp_path, monkeypatch):
    kcd = tmp_path / "network.kcd"
    db = cantools.database.load_file(str(FILES / "attributes.dbc"))
    kcd.write_text(db.as_kcd_string(), encoding="utf-8")
    assert window._load_dbc(str(kcd))
    pane = window.database_view
    pane.select_message("PumpStatus")
    told = []
    monkeypatch.setattr(messages, "information", lambda *a, **k: told.append(a))
    pane.edit_message()
    assert told and pane.editing() is None


def test_one_database_is_changed_at_a_time(window, view, tmp_path, monkeypatch):
    other = tmp_path / "other.dbc"
    shutil.copy(FILES / "attributes.dbc", other)
    window._load_dbc(str(other))
    answering(monkeypatch, MessageDialog, lambda d: d.cycle.setValue(25))
    view.edit_message()
    view.select_message("PumpStatus")
    told = []
    monkeypatch.setattr(messages, "information", lambda *a, **k: told.append(a))
    view.edit_message()
    assert told, "save or discard the first"
