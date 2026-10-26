# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Changing a database in the Database pane: the dialogs, the changes, Save as."""

import shutil
from pathlib import Path

import cantools
import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QDialog, QMessageBox

from pycangui.core.dbc_edit import (
    AddAttributeDefinition,
    AddNode,
    AddSignalGroup,
    AddValueTable,
    AttributeSpec,
    DatabaseEdit,
    RemoveAttributeDefinition,
    RemoveNode,
    RemoveValueTable,
    SetAttribute,
    SetAttributeDefinition,
    SetNode,
    SetSignalGroup,
    SetValueTable,
)
from pycangui.ui import database_edit, database_view, folders, messages
from pycangui.ui.database_edit import (
    AttributesDialog,
    DefinitionsDialog,
    MessageDialog,
    NodesDialog,
    SignalDialog,
    SignalGroupsDialog,
    ValueTablesDialog,
)

FILES = Path(__file__).parent / "dbc"


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    from pycangui.ui.main_window import MainWindow

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path / "home"))
    QSettings().clear()
    win = MainWindow()
    yield win
    win.database_view._edit = None  # what a test left changed is not asked about
    if not win._closing:  # closed already by the test, which a second close would undo
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


# --- changes not saved --------------------------------------------------------------------------
@pytest.fixture
def changed(view, monkeypatch):
    answering(monkeypatch, MessageDialog, lambda d: d.cycle.setValue(25))
    view.edit_message()
    assert view.editing().changed
    return view


def test_closing_with_changes_asks_and_cancel_keeps_the_window(window, changed, monkeypatch):
    asked = []
    monkeypatch.setattr(messages, "question", lambda *a, **k: asked.append(a) or QMessageBox.Cancel)
    assert not window.close()
    assert asked and changed.editing().changed, "nothing lost"


def test_discard_lets_the_window_close(window, changed, monkeypatch):
    monkeypatch.setattr(messages, "question", lambda *a, **k: QMessageBox.Discard)
    assert window.close()


def test_save_writes_them_first_and_offers_nothing_more(window, changed, tmp_path, monkeypatch):
    """Closing is no moment to ask whether to load the new file."""
    asked = []
    monkeypatch.setattr(messages, "question", lambda *a, **k: asked.append(a) or QMessageBox.Save)
    target = tmp_path / "kept.dbc"
    monkeypatch.setattr(folders, "save_file", lambda *a, **k: str(target))
    assert changed.may_discard()
    assert target.is_file() and len(asked) == 1


def test_removing_the_database_being_changed_asks_first(window, changed, monkeypatch):
    monkeypatch.setattr(messages, "question", lambda *a, **k: QMessageBox.Cancel)
    path = changed._path
    window._remove_dbc(path, path)
    assert path in window.dbc.databases, "still loaded"
    window._unload_dbcs()
    assert path in window.dbc.databases
    monkeypatch.setattr(messages, "question", lambda *a, **k: QMessageBox.Discard)
    window._remove_dbc(path, path)
    assert path not in window.dbc.databases


# --- multiplexing and value tables ---------------------------------------------------------------
def muxed_message(name="Simple"):
    db = cantools.database.load_file(
        str(FILES / "multiplexed.dbc"), strict=False, sort_signals=None
    )
    return db.get_message_by_name(name)


def test_a_multiplexed_signal_opened_and_closed_with_ok_changes_nothing(app):
    msg = muxed_message("Extended")
    for signal in msg.signals:
        others = [s.name for s in msg.signals if s.is_multiplexer and s.name != signal.name]
        dialog = SignalDialog(None, msg.name, signal, multiplexers=others)
        dialog._accept()
        assert dialog.result() == QDialog.Accepted and dialog.changes() == [], signal.name


def test_a_signal_is_put_under_a_multiplexer_from_its_dialog(app):
    msg = muxed_message()
    counter = msg.get_signal_by_name("Counter")
    dialog = SignalDialog(None, msg.name, counter, multiplexers=["Page"])
    assert not dialog.sent_values.isEnabled(), "no values to give while sent in every frame"
    dialog.sent.setCurrentIndex(dialog.sent.findData("Page"))
    dialog.sent_values.setText("0, 2-3")
    dialog._accept()
    assert [(c.field, c.value) for c in dialog.changes()] == [
        ("multiplexed_by", ("Page", (0, 2, 3)))
    ]


def test_a_multiplexer_with_no_values_keeps_the_dialog_open(app):
    msg = muxed_message()
    dialog = SignalDialog(None, msg.name, msg.get_signal_by_name("Counter"), multiplexers=["Page"])
    dialog.sent.setCurrentIndex(dialog.sent.findData("Page"))
    dialog.sent_values.setText("")
    dialog._accept()
    assert dialog.result() != QDialog.Accepted and dialog.problem.isVisibleTo(dialog)


def test_named_values_come_from_a_value_table(app):
    msg = muxed_message()
    tables = {"OnOff": {1: "On", 0: "Off"}}
    dialog = SignalDialog(None, msg.name, msg.get_signal_by_name("Counter"), tables=tables)
    assert dialog.from_table.isVisibleTo(dialog)
    dialog.use_table("OnOff")
    dialog._accept()
    assert [(c.field, c.value) for c in dialog.changes()] == [("choices", {1: "On", 0: "Off"})]
    assert not SignalDialog(None, msg.name).from_table.isVisibleTo(dialog), "none to offer"


def test_a_signal_is_offered_only_multiplexers_it_could_be_sent_by(window, tmp_path):
    path = tmp_path / "muxed.dbc"
    shutil.copy(FILES / "multiplexed.dbc", path)
    assert window._load_dbc(str(path))
    extended = muxed_message("Extended")
    assert database_view._multiplexers(extended, "Raw") == ["Group", "Channel"]
    assert database_view._multiplexers(extended, "Group") == [], "not under what it chooses"


def test_the_value_tables_dialog_says_only_what_changed(app):
    tables = {"OnOff": {1: "On", 0: "Off"}, "GearTable": {7: "Reverse"}}
    dialog = ValueTablesDialog(None, tables)
    dialog._accept()
    assert dialog.result() == QDialog.Accepted and dialog.changes() == []

    dialog = ValueTablesDialog(None, tables)
    dialog.list.item(1).setText("Gears")
    dialog.list.setCurrentRow(0)
    dialog.values.item(0, 1).setText("Yes")
    dialog.add_table()
    dialog.add_value(5, "Five")
    dialog._accept()
    assert dialog.changes() == [
        SetValueTable("OnOff", "values", {1: "Yes", 0: "Off"}),
        AddValueTable("Table1", {5: "Five"}),
        SetValueTable("GearTable", "name", "Gears"),
    ]

    dialog = ValueTablesDialog(None, tables)
    dialog.list.setCurrentRow(0)
    dialog.remove_table()
    dialog._accept()
    assert dialog.changes() == [RemoveValueTable("OnOff")]


def test_a_value_table_name_not_allowed_keeps_the_dialog_open(app):
    dialog = ValueTablesDialog(None, {"OnOff": {1: "On"}})
    dialog.list.item(0).setText("On Off")
    dialog._accept()
    assert dialog.result() != QDialog.Accepted and dialog.problem.isVisibleTo(dialog)


def test_value_tables_are_changed_from_the_pane(view, monkeypatch):
    def fill(dialog):
        dialog.list.setCurrentRow(0)
        dialog.remove_table()

    answering(monkeypatch, ValueTablesDialog, fill)
    view.edit_value_tables()
    assert "OnOff" not in view._edit.value_tables()
    assert view.edit_bar.isVisibleTo(view)


# --- nodes, attributes, signal groups ------------------------------------------------------------
@pytest.fixture
def attributed():
    return DatabaseEdit(FILES / "attributes.dbc")


def test_dialogs_with_attributes_opened_and_closed_with_ok_change_nothing(app, attributed):
    msg = attributed.message("PumpStatus")
    nodes = [n.name for n in attributed.nodes()]
    dialogs = [
        MessageDialog(None, msg, nodes, attributed.attributes(("message", msg.name))),
        SignalDialog(
            None,
            msg.name,
            msg.signals[2],
            nodes=nodes,
            attributes=attributed.attributes(("signal", msg.name, msg.signals[2].name)),
        ),
        NodesDialog(
            None,
            [(n, "") for n in nodes],
            {n: attributed.attributes(("node", n)) for n in nodes},
        ),
        SignalGroupsDialog(None, msg.name, [s.name for s in msg.signals], []),
        AttributesDialog(None, attributed.attributes(())),
        DefinitionsDialog(
            None,
            attributed.definitions(),
            {d.name: attributed.uses(d.name) for d in attributed.definitions()},
        ),
    ]
    for dialog in dialogs:
        dialog._accept()
        assert dialog.result() == QDialog.Accepted, type(dialog).__name__
        assert dialog.changes() == [], type(dialog).__name__


def test_an_attribute_is_changed_in_the_message_dialog(app, attributed):
    msg = attributed.message("ValveCommand")
    dialog = MessageDialog(None, msg, attributes=attributed.attributes(("message", msg.name)))
    rows = [dialog.attributes.item(r, 0).text() for r in range(dialog.attributes.rowCount())]
    send = dialog.attributes.cellWidget(rows.index("GenMsgSendType"), 1)
    send.setCurrentIndex(send.findData("Cyclic"))
    dialog.attributes.item(rows.index("GenMsgStartDelayTime"), 1).setText("")
    dialog._accept()
    assert dialog.changes() == [
        SetAttribute(("message", "ValveCommand"), "GenMsgSendType", "Cyclic"),
        SetAttribute(("message", "ValveCommand"), "GenMsgStartDelayTime", None),
    ]


def test_an_attribute_that_is_not_a_number_keeps_the_dialog_open(app, attributed):
    dialog = AttributesDialog(None, attributed.attributes(()))
    rows = [dialog.table.item(r, 0).text() for r in range(dialog.table.rowCount())]
    dialog.table.item(rows.index("ProtocolVersion"), 1).setText("three")
    dialog._accept()
    assert dialog.result() != QDialog.Accepted and dialog.problem.isVisibleTo(dialog)


def test_senders_are_chosen_from_the_nodes(app):
    dialog = MessageDialog(None, nodes=["Engine", "Gearbox"])
    chooser = next(
        b for b in dialog.findChildren(database_edit.QPushButton) if b.text() == "Choose"
    )
    chooser.menu().aboutToShow.emit()
    gearbox = next(a for a in chooser.menu().actions() if a.text() == "Gearbox")
    gearbox.trigger()
    assert dialog.senders.text() == "Gearbox"
    gearbox.trigger()
    assert dialog.senders.text() == ""


def test_the_nodes_dialog_says_only_what_changed(app):
    dialog = NodesDialog(None, [("Engine", "Runs"), ("Dash", "")])
    dialog.list.item(1).setText("Dashboard")
    dialog.list.setCurrentRow(0)
    dialog.pages.widget(0).comment.setPlainText("Runs fast")
    dialog.add_entry()
    dialog.pages.currentWidget().comment.setPlainText("New")
    dialog._accept()
    assert dialog.changes() == [
        SetNode("Engine", "comment", "Runs fast"),
        AddNode("Node1", "New"),
        SetNode("Dash", "name", "Dashboard"),
    ]
    dialog = NodesDialog(None, [("Engine", ""), ("Dash", "")])
    dialog.list.setCurrentRow(1)
    dialog.remove_entry()
    dialog._accept()
    assert dialog.changes() == [RemoveNode("Dash")]


def test_the_signal_groups_dialog_says_only_what_changed(app):
    dialog = SignalGroupsDialog(None, "Status", ["A", "B", "C"], [("Pair", 1, ["A", "B"])])
    page = dialog.pages.widget(0)
    page.ticks.item(2).setCheckState(database_edit.Qt.Checked)
    page.repetitions.setValue(2)
    dialog.add_entry()
    dialog.pages.currentWidget().ticks.item(0).setCheckState(database_edit.Qt.Checked)
    dialog._accept()
    assert dialog.changes() == [
        SetSignalGroup("Status", "Pair", "repetitions", 2),
        SetSignalGroup("Status", "Pair", "signals", ("A", "B", "C")),
        AddSignalGroup("Status", "Group1", 1, ("A",)),
    ]


def test_the_definitions_dialog_adds_changes_and_removes(app, attributed):
    specs = attributed.definitions()
    uses = {d.name: attributed.uses(d.name) for d in specs}
    dialog = DefinitionsDialog(None, specs, uses)
    names = [dialog.table.item(r, 0).text() for r in range(dialog.table.rowCount())]
    assert not dialog.table.cellWidget(names.index("NodeLayer"), 2).isEnabled(), "in use"
    dialog.table.item(names.index("ProtocolVersion"), 6).setText("2")
    dialog.table.setCurrentCell(names.index("SigUnitText"), 0)
    dialog.remove_definition()
    dialog.add_definition()
    added = dialog.table.rowCount() - 1
    dialog.table.cellWidget(added, 2).setCurrentText("STRING")
    dialog._accept()
    changes = dialog.changes()
    assert changes[0] == RemoveAttributeDefinition("SigUnitText")
    assert isinstance(changes[1], SetAttributeDefinition) and changes[1].spec.default == 2
    assert changes[2] == AddAttributeDefinition(
        AttributeSpec("Attribute1", "message", "STRING", None, None, (), None)
    )


def test_a_choice_attribute_with_no_choices_keeps_the_dialog_open(app):
    dialog = DefinitionsDialog(None, [], {})
    dialog.add_definition()
    dialog.table.cellWidget(0, 2).setCurrentText("ENUM")
    dialog._accept()
    assert dialog.result() != QDialog.Accepted and dialog.problem.isVisibleTo(dialog)


def test_nodes_attributes_and_groups_are_changed_from_the_pane(window, tmp_path, monkeypatch):
    path = tmp_path / "attributes.dbc"
    shutil.copy(FILES / "attributes.dbc", path)
    assert window._load_dbc(str(path))
    view = window.database_view
    assert view.select_message("PumpStatus")
    answering(monkeypatch, NodesDialog, lambda d: d.add_entry())
    view.edit_nodes()
    answering(
        monkeypatch,
        SignalGroupsDialog,
        lambda d: (
            d.add_entry(),
            d.pages.currentWidget().ticks.item(0).setCheckState(database_edit.Qt.Checked),
        ),
    )
    view.edit_signal_groups()
    answering(monkeypatch, DefinitionsDialog, lambda d: d.add_definition())
    view.edit_definitions()

    def protocol(d):
        rows = [d.table.item(r, 0).text() for r in range(d.table.rowCount())]
        d.table.item(rows.index("ProtocolVersion"), 1).setText("4")

    answering(monkeypatch, AttributesDialog, protocol)
    view.edit_database_attributes()
    edit = view._edit
    assert "Node1" in [n.name for n in edit.nodes()]
    assert [g.name for g in edit.message("PumpStatus").signal_groups] == ["Group1"]
    assert "Attribute1" in [d.name for d in edit.definitions()]
    assert {r.name: r.value for r in edit.attributes(())}["ProtocolVersion"] == 4
    assert len(edit.changes) == 4 and view.edit_bar.isVisibleTo(view)


def test_the_save_button_offers_the_new_file_with_paths_as_a_dialog_gives_them(
    window, tmp_path, monkeypatch
):
    path = tmp_path / "network.dbc"
    shutil.copy(FILES / "tables_and_comments.dbc", path)
    assert window._load_dbc(path.as_posix())
    view = window.database_view
    assert view.select_message("GearboxData")
    answering(monkeypatch, MessageDialog, lambda d: d.cycle.setValue(25))
    view.edit_message()
    target = tmp_path / "changed.dbc"
    monkeypatch.setattr(folders, "save_file", lambda *a, **k: target.as_posix())
    asked = []
    monkeypatch.setattr(messages, "question", lambda *a, **k: asked.append(a) or QMessageBox.Yes)
    view.save_btn.click()

    assert asked, "the new file is offered"
    assert list(window.dbc.databases) == [target.as_posix()], "and only it is loaded"
    assert window.dbc.message_by_name("GearboxData").cycle_time == 25
