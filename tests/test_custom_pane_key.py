# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""A pane's fields are addressed by key, and only the source looks inside one.

``(index, sub)`` on CANopen, the A2L name on XCP and CCP, the DID on UDS. The
point is that the pane, its widgets and the poller pass a key along without
knowing which: a source for another protocol then needs nothing changed above
it. Pinned down before anything outside pycangui provides sources, because
changing the shape afterwards would break whatever had.

And a CANopen pane's file is exactly what it always was.
"""

import json

import pytest
from PySide6.QtCore import QObject, QSettings, QTimer, Signal

from pycangui.canopen.display import Display
from pycangui.custom_panes import model
from pycangui.custom_panes.model import CANOPEN, UDS, XCP, CustomPane, Field
from pycangui.custom_panes.polling import Poller
from pycangui.ui.main_window import MainWindow


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    return tmp_path


def settle(app, times=5):
    for _ in range(times):
        app.processEvents()


# --- the key -----------------------------------------------------------------------------
def test_each_protocol_s_field_has_its_own_key():
    assert Field(index=0x2001, sub=3).key == (0x2001, 3)
    assert Field(name="EngineSpeed").key == "EngineSpeed"
    assert Field(did=0xF190).key == 0xF190


def test_a_canopen_pane_s_file_is_as_it_always_was(home):
    """Written before keys, read and saved again: the same JSON, no new keys."""
    written = {
        "title": "Battery limits",
        "node": 5,
        "fields": [
            {"index": "0x2001", "kind": "number", "label": "Motor current"},
            {"index": "0x2002", "kind": "value", "sub": 1},
        ],
    }
    model.directory().mkdir(parents=True, exist_ok=True)
    model.path_for("battery").write_text(json.dumps(written), encoding="utf-8")

    pane = model.load("battery")
    assert pane.protocol == CANOPEN
    assert [f.key for f in pane.fields] == [(0x2001, 0), (0x2002, 1)]

    model.save("battery", pane)
    again = json.loads(model.path_for("battery").read_text(encoding="utf-8"))
    assert "protocol" not in again
    assert all(set(f) <= {"index", "sub", "kind", "label"} for f in again["fields"])


@pytest.mark.parametrize(
    "pane",
    [
        CustomPane(title="Calibration", protocol=XCP, fields=[Field(name="EngineSpeed")]),
        CustomPane(title="Identification", protocol=UDS, fields=[Field(did=0xF190)]),
    ],
)
def test_a_pane_for_another_protocol_survives_its_file(home, pane):
    model.save("other", pane)
    assert model.load("other") == pane
    data = json.loads(model.path_for("other").read_text(encoding="utf-8"))
    assert data["protocol"] == pane.protocol
    assert "index" not in data["fields"][0], "addressed its own way, not by a dummy index"


@pytest.mark.parametrize(
    "pane",
    [
        CustomPane(title="t", protocol=XCP, fields=[Field(index=0x2001)]),
        CustomPane(title="t", protocol=UDS, fields=[Field(name="EngineSpeed")]),
        CustomPane(title="t", protocol=UDS, fields=[Field(did=0x1_0000)]),
        CustomPane(title="t", protocol=CANOPEN, fields=[Field(name="EngineSpeed")]),
        CustomPane(title="t", protocol=XCP, fields=[Field(name="Curve", kind="map")]),
        CustomPane(title="t", protocol="ethercat", fields=[]),
    ],
    ids=["index-on-xcp", "name-on-uds", "did-too-big", "name-on-canopen", "map-on-xcp", "unknown"],
)
def test_a_field_addressed_the_wrong_way_for_its_pane_is_said(pane):
    assert model.problems(pane)


def test_good_panes_for_each_protocol_have_nothing_wrong():
    for pane in (
        CustomPane(title="t", fields=[Field(index=0x2001)]),
        CustomPane(title="t", protocol=XCP, fields=[Field(name="EngineSpeed")]),
        CustomPane(title="t", protocol=UDS, fields=[Field(did=0xF190)]),
    ):
        assert model.problems(pane) == []


def test_the_same_name_twice_is_a_copy_and_paste():
    pane = CustomPane(
        title="t", protocol=XCP, fields=[Field(name="EngineSpeed"), Field(name="EngineSpeed")]
    )
    assert model.problems(pane)


def test_panes_can_be_listed_by_protocol(home):
    model.save("limits", CustomPane(title="Limits", fields=[Field(index=0x2001)]))
    model.save("cal", CustomPane(title="Cal", protocol=XCP, fields=[Field(name="EngineSpeed")]))
    assert model.names() == ["cal", "limits"]
    assert model.names(CANOPEN) == ["limits"]
    assert model.names(XCP) == ["cal"]


# --- the poller ---------------------------------------------------------------------------
def test_the_poller_asks_by_key_and_finishes_on_the_key(app):
    made = Poller()
    asked, read = [], []
    made.asked.connect(asked.append)
    made.read.connect(lambda index, sub: read.append((index, sub)))
    made.set_objects(["EngineSpeed", "Lambda"])
    made.start(1.0)
    assert asked == ["EngineSpeed", "Lambda"]
    assert read == [], "index and sub only for a key that is one"
    made.answered("EngineSpeed")
    made.answered("Lambda")
    assert made._rounds, "the round finished on its own keys"
    made.stop()


def test_the_poller_still_speaks_index_and_sub_to_what_was_written_for_it(app):
    """The CiA 402 pane and the object dictionary's polling connect ``read``
    and answer with an index and a sub, as before keys."""
    made = Poller()
    read = []
    made.read.connect(lambda index, sub: read.append((index, sub)))
    made.set_objects([(0x6041, 0)])
    made.start(1.0)
    assert read == [(0x6041, 0)]
    made.answered(0x6041, 0)
    assert made._rounds
    made.stop()


# --- a pane over a source keyed by name ------------------------------------------------
class NamedSource(QObject):
    """A source whose objects are names: what an XCP source will be."""

    value = Signal(object, object, object)
    changed = Signal()
    protocol = XCP
    label = "Named"
    writable = True
    live = True

    def __init__(self):
        super().__init__()
        self.asked = []
        self.written = []

    def display(self, key):
        return Display(name=f"{key} shown", unit="rpm")

    def request(self, key):
        self.asked.append(key)
        QTimer.singleShot(0, lambda: self.value.emit(key, 1500, None))

    def write(self, key, raw):
        self.written.append((key, raw))
        QTimer.singleShot(0, lambda: self.value.emit(key, raw, None))

    def objects(self):
        return [("EngineSpeed", "EngineSpeed", "rw")]


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    win = MainWindow()
    yield win
    win.close()


def named_pane(window):
    model.save(
        "cal",
        CustomPane(
            title="Calibration",
            protocol=XCP,
            fields=[Field(name="EngineSpeed", kind="number"), Field(name="Lambda", kind="value")],
        ),
    )
    window.open_custom_pane("cal")
    return window._custom_pane_view("cal")


def test_a_pane_reads_and_writes_by_name_through_its_source(app, window):
    view = named_pane(window)
    source = NamedSource()
    view.bind(source)
    settle(app)
    assert source.asked == ["EngineSpeed", "Lambda"]
    speed = view._widgets[0]
    assert speed.display.unit == "rpm", "the display is the source's, asked by name"
    assert speed._raw == 1500

    speed._typed.setText("2000")
    speed._on_entered()
    settle(app)
    assert source.written == [("EngineSpeed", 2000)]
    assert speed._raw == 2000


def test_a_pane_polls_by_name(app, window):
    view = named_pane(window)
    source = NamedSource()
    view.bind(source)
    settle(app)
    source.asked.clear()
    view.poll.setChecked(True)
    settle(app)
    assert source.asked[:2] == ["EngineSpeed", "Lambda"]
    assert view.poller._rounds, "its own answers finished the round"
    view.poll.setChecked(False)


def test_a_pane_for_another_protocol_is_not_offered_the_nodes(app, window):
    model.save("ids", CustomPane(title="Identification", protocol=UDS, fields=[Field(did=0xF190)]))
    window.open_custom_pane("ids")
    view = window._custom_pane_view("ids")
    offered = [view.sources.itemData(i) for i in range(view.sources.count())]
    assert offered == [None], "no node, no CANopen file: nothing of its protocol yet"


def test_the_editor_keeps_the_pane_s_protocol(app, window):
    from pycangui.ui.custom_pane_view import CustomPaneEditor

    view = named_pane(window)
    editor = CustomPaneEditor(view, view.pane, None)
    assert editor.result_pane().protocol == XCP
    editor.deleteLater()
