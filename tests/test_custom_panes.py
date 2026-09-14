# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""What a pane is, and where its values come from.

Two things are being pinned down here.  The *model* -- a title and a list of
objects with labels -- has to survive a round trip through a file somebody is
expected to open in a text editor, and has to forgive them when they get it
wrong: a typo in one field should cost that field, not the pane.

The *source* is the one that would be expensive to get wrong.  A pane is bound
to a source and never to a node, so that the same pane serves a live
controller, a DCF and an EDS's defaults.  Bound to a node instead, each of
those becomes its own screen and comparing two of them becomes a fourth --
and every pane written before the seam existed would have to be rewritten.
"""

import json

import pytest

from pycangui import custom_panes
from pycangui.canopen.manager import CanopenManager
from pycangui.core import workspaces
from pycangui.core.bus import BusManager
from pycangui.core.context import Context
from pycangui.core.hooks import Hooks
from pycangui.custom_panes import CustomPane, Field


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    return tmp_path


def settle(app, times=5):
    for _ in range(times):
        app.processEvents()


# --- the model --------------------------------------------------------------------------
def sample() -> CustomPane:
    return CustomPane(
        title="Battery limits",
        node=5,
        fields=[
            Field(index=0x2001, kind="number", label="Motor current", unit="A", factor=0.1),
            Field(index=0x2002, sub=1, kind="bits", first=4, width=3, choices={0: "Off", 1: "Run"}),
            Field(index=0x2003, kind="flags", bits={0: "Ready", 3: "Fault"}),
        ],
    )


def test_a_custom_pane_survives_the_trip_through_its_file(home):
    custom_panes.save("battery", sample())
    assert custom_panes.load("battery") == sample()


def test_the_file_says_only_what_was_said(home):
    """Twenty keys with eighteen at their defaults is a file nobody edits twice."""
    custom_panes.save("battery", sample())
    written = json.loads(custom_panes.path_for("battery").read_text())
    assert written["fields"][0] == {
        "index": "0x2001",
        "kind": "number",
        "label": "Motor current",
        "unit": "A",
        "factor": 0.1,
    }


def test_the_index_is_written_the_way_it_is_spoken(home):
    """Nobody says object 8193."""
    custom_panes.save("battery", sample())
    assert '"index": "0x2001"' in custom_panes.path_for("battery").read_text()


@pytest.mark.parametrize("written", [0x2001, "0x2001", "2001h", "8193"])
def test_an_index_is_read_however_it_was_typed(home, written):
    path = custom_panes.path_for("hand")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"title": "x", "fields": [{"index": written}]}), encoding="utf-8")
    assert custom_panes.load("hand").fields[0].index == 0x2001


def test_a_typo_costs_the_field_and_not_the_pane(home):
    path = custom_panes.path_for("hand")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "title": "Mixed",
                "fields": [
                    {"index": "0x2001", "kind": "numbre", "factor": "not a number"},
                    {"index": "0x2002", "kind": "number"},
                ],
            }
        ),
        encoding="utf-8",
    )
    pane = custom_panes.load("hand")
    assert len(pane.fields) == 2, "the good one is still there"
    assert pane.fields[0].kind == "value", "and the bad one is shown, not guessed at"
    assert pane.fields[0].factor is None


def test_a_file_that_is_not_a_custom_pane_is_no_custom_pane_rather_than_a_crash(home):
    path = custom_panes.path_for("broken")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ truncated", encoding="utf-8")
    assert custom_panes.load("broken") is None
    assert custom_panes.load("never written") is None


def test_custom_panes_live_beside_the_hooks_that_explain_them(home):
    """A pane is knowledge about a product in the way a hook is."""
    custom_panes.save("battery", sample())
    assert custom_panes.directory().parent == workspaces.active_dir()
    assert custom_panes.names() == ["battery"]
    custom_panes.delete("battery")
    assert custom_panes.names() == []


def test_a_custom_pane_belongs_to_its_workspace(home):
    custom_panes.save("battery", sample())
    workspaces.create("other")
    workspaces.set_active("other")
    assert custom_panes.names() == [], "another product, another set of custom_panes"


# --- bits ---------------------------------------------------------------------------------
def test_a_field_reads_its_own_bits_out_of_the_word():
    packed = Field(index=0x2002, kind="bits", first=4, width=3)
    assert packed.extract(0x50) == 5
    assert packed.extract(0xFF) == 7
    assert packed.mask == 0x70


def test_writing_some_bits_keeps_the_rest():
    """Three bits of a 32 bit word cannot be written without the other 29, so
    a pane that did not read first would zero everything it was not showing."""
    packed = Field(index=0x2002, kind="bits", first=4, width=3)
    assert packed.insert(0xFF, 2) == 0xAF
    assert packed.insert(0x00, 7) == 0x70
    assert packed.insert(0xDEAD, 0) == 0xDE8D


def test_a_whole_object_is_not_a_bit_field():
    plain = Field(index=0x2001, kind="number")
    assert plain.extract(1234) == 1234
    assert plain.insert(9999, 7) == 7


# --- is it usable -----------------------------------------------------------------------------
def test_a_good_custom_pane_has_nothing_wrong_with_it():
    assert custom_panes.problems(sample()) == []


@pytest.mark.parametrize(
    "pane, because",
    [
        (CustomPane(title="", fields=[]), "no title"),
        (CustomPane(title="t", fields=[Field(index=0x2000, kind="flags")]), "would show nothing"),
        (CustomPane(title="t", fields=[Field(index=0x2000, kind="enum")]), "choices naming"),
        (CustomPane(title="t", fields=[Field(index=0x2000, kind="bits", width=0)]), "bits wide"),
        (
            CustomPane(title="t", fields=[Field(index=0x2000, kind="bits", width=3, first=99)]),
            "0 to 63",
        ),
        (CustomPane(title="t", fields=[Field(index=0x1FFFFF)]), "0 to 0xFFFF"),
    ],
)
def test_what_is_wrong_is_said_rather_than_raised(pane, because):
    """A pane with one bad field is still a pane, and refusing to open it
    leaves nobody able to see which field was the problem."""
    found = custom_panes.problems(pane)
    assert any(because in line for line in found), found


def test_the_same_field_twice_is_a_copy_and_paste():
    twice = CustomPane(title="t", fields=[Field(index=0x2001), Field(index=0x2001)])
    assert any("twice" in line for line in custom_panes.problems(twice))


def test_the_same_object_shown_two_ways_is_a_layout():
    """A word of flags beside one of its own bits is a real thing to want."""
    both = CustomPane(
        title="t",
        fields=[
            Field(index=0x2001, kind="flags", bits={0: "Ready"}),
            Field(index=0x2001, kind="bits", first=4, width=2, choices={0: "Off"}),
        ],
    )
    assert custom_panes.problems(both) == []


@pytest.mark.parametrize(
    "name, because",
    [("", "needs a name"), ("a/b", "file name"), (".hidden", "file name"), ("x" * 65, "at most")],
)
def test_a_custom_name_has_to_survive_being_a_file_name(home, name, because):
    assert because in custom_panes.why_not(name)


def test_a_name_already_taken_is_refused(home):
    custom_panes.save("battery", sample())
    assert "already a pane" in custom_panes.why_not("battery")


# --- the source: a live node -----------------------------------------------------------------
@pytest.fixture
def manager(home):
    bus = BusManager()
    made = CanopenManager(bus)
    yield made
    made.shutdown()
    bus.disconnect_bus()


def test_a_node_source_only_hears_about_its_own_node(app, manager):
    """The whole of what makes two custom_panes on two nodes independent."""
    five = custom_panes.NodeSource(manager, 5)
    seven = custom_panes.NodeSource(manager, 7)
    heard_five, heard_seven = [], []
    five.value.connect(lambda *a: heard_five.append(a))
    seven.value.connect(lambda *a: heard_seven.append(a))

    manager.sdo_result.emit(5, 0x2001, 0, 1234, None)
    settle(app)
    assert heard_five == [(0x2001, 0, 1234, None)]
    assert heard_seven == []


def test_a_node_source_reads_and_writes_through_the_manager(app, manager, monkeypatch):
    asked, written = [], []
    monkeypatch.setattr(manager, "sdo_read", lambda *a: asked.append(a))
    monkeypatch.setattr(manager, "sdo_write", lambda *a: written.append(a))
    source = custom_panes.NodeSource(manager, 5)

    source.request(0x2001, 0)
    source.write(0x2001, 0, 1234)
    assert asked == [(5, 0x2001, 0)]
    assert written == [(5, 0x2001, 0, "1234")]


def test_an_error_from_the_node_arrives_as_an_error(app, manager):
    source = custom_panes.NodeSource(manager, 5)
    heard = []
    source.value.connect(lambda *a: heard.append(a))
    manager.sdo_result.emit(5, 0x2001, 0, None, "Abort 0x06020000")
    settle(app)
    assert heard == [(0x2001, 0, None, "Abort 0x06020000")]


def test_a_live_node_is_writable_and_says_which_node_it_is(app, manager):
    source = custom_panes.NodeSource(manager, 5)
    assert source.writable
    assert source.label == "Node 5"


# --- the source: a file ------------------------------------------------------------------------
EDS = """[FileInfo]
FileName=acme.eds
EDSVersion=4.0
[DeviceInfo]
VendorName=Acme
VendorNumber=1
ProductName=Widget
ProductNumber=2
RevisionNumber=3
[MandatoryObjects]
SupportedObjects=1
1=0x1000
[1000]
ParameterName=Device type
ObjectType=7
DataType=7
AccessType=ro
DefaultValue=0
[OptionalObjects]
SupportedObjects=1
1=0x2001
[2001]
ParameterName=Motor current
ObjectType=7
DataType=3
AccessType=rw
LowLimit=0
HighLimit=1000
DefaultValue=250
;ACMEFIELD UNITS=A
"""


@pytest.fixture
def eds(tmp_path):
    path = tmp_path / "acme.eds"
    path.write_text(EDS, encoding="utf-8")
    return path


def test_a_file_source_reads_what_the_file_says(app, eds):
    source = custom_panes.FileSource(eds)
    heard = []
    source.value.connect(lambda *a: heard.append(a))
    source.request(0x2001, 0)
    settle(app)
    assert heard == [(0x2001, 0, 250, None)], "the EDS default, since it has no value"


def test_a_file_source_answers_the_way_a_node_does(app, eds):
    """Through the event loop, so the pane has one path through it rather
    than a fast one that only ever runs in tests."""
    source = custom_panes.FileSource(eds)
    heard = []
    source.value.connect(lambda *a: heard.append(a))
    source.request(0x2001, 0)
    assert heard == [], "not before the caller has finished"
    settle(app)
    assert heard


def test_an_object_the_file_does_not_have_says_so(app, eds):
    source = custom_panes.FileSource(eds)
    heard = []
    source.value.connect(lambda *a: heard.append(a))
    source.request(0x9999, 0)
    settle(app)
    assert heard[0][3] == "not in this file"


def test_editing_a_file_changes_the_file_and_not_a_machine(app, eds):
    """The difference between building a configuration at a desk and
    configuring something."""
    source = custom_panes.FileSource(eds)
    source.write(0x2001, 0, 500)
    settle(app)
    assert source.edited == {(0x2001, 0): 500}
    assert "ParameterValue" not in eds.read_text(), "nothing written until it is saved"


def test_a_written_value_is_what_is_read_back(app, eds):
    source = custom_panes.FileSource(eds)
    source.write(0x2001, 0, 500)
    settle(app)
    heard = []
    source.value.connect(lambda *a: heard.append(a))
    source.request(0x2001, 0)
    settle(app)
    assert heard == [(0x2001, 0, 500, None)]


def test_saving_writes_a_dcf_through_the_original_text(app, eds, tmp_path):
    """A round trip that loses the comments loses the units and the scaling."""
    source = custom_panes.FileSource(eds)
    source.write(0x2001, 0, 500)
    settle(app)
    out = source.save(tmp_path / "out.dcf", node_id=5)
    text = out.read_text()
    assert "ParameterValue=500" in text
    assert ";ACMEFIELD UNITS=A" in text
    assert "NodeID=5" in text


def test_a_file_source_reads_the_meaning_the_file_carries(app, eds):
    display = custom_panes.FileSource(eds).display(0x2001, 0)
    assert display.name == "Motor current"
    assert (display.low, display.high) == (0, 1000)


def test_a_file_source_asks_the_hook_with_the_file_s_own_extras(app, eds, home):
    """The same hook a live node goes through, so a DCF is labelled the same
    way the controller it came from is."""
    ctx = Context(log=print)
    hooks = Hooks(ctx)
    seen = []
    real = hooks.call

    def spy(module, name, *args, **kwargs):
        if name == "object_display":
            seen.append(args)
        return real(module, name, *args, **kwargs)

    hooks.call = spy
    custom_panes.FileSource(eds, hooks=hooks).display(0x2001, 0)
    assert seen and seen[0][0] == 0x2001
    assert seen[0][2].get("ACMEFIELD UNITS") == "A", "what the parser dropped"
    assert seen[0][3] is not None and seen[0][3].vendor_id == 1


def test_a_file_that_is_not_an_eds_is_an_empty_source_rather_than_a_crash(app, tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("nothing to do with CANopen", encoding="utf-8")
    source = custom_panes.FileSource(path)
    heard = []
    source.value.connect(lambda *a: heard.append(a))
    source.request(0x2001, 0)
    settle(app)
    assert heard[0][3] == "not in this file"


# --- and saving it -----------------------------------------------------------------------------
def test_a_file_source_knows_when_the_disk_is_behind(app, eds, tmp_path):
    source = custom_panes.FileSource(eds)
    heard = []
    source.modified.connect(heard.append)
    assert not source.unsaved, "nothing edited yet"

    source.write(0x2001, 0, 500)
    settle(app)
    assert source.unsaved and heard == [True]

    source.save(tmp_path / "acme.dcf")
    assert not source.unsaved and heard[-1] is False


def test_saved_under_another_name_the_source_becomes_that_file(app, eds, tmp_path):
    """The edits that follow belong to the copy, and so does the next Save."""
    source = custom_panes.FileSource(eds)
    source.write(0x2001, 0, 500)
    out = source.save(tmp_path / "desk.dcf")
    assert (source.path, source.label) == (out, "desk.dcf")
    assert "ParameterValue" not in eds.read_text(), "the EDS keeps its defaults"

    source.write(0x2001, 0, 600)
    source.save()
    settle(app)
    text = out.read_text()
    assert "ParameterValue=600" in text
    assert text.count("ParameterValue") == 1, "replaced, not added a second time"


def test_only_a_dcf_is_saved_over(app, eds, tmp_path):
    assert custom_panes.FileSource(eds).needs_new_name
    dcf = tmp_path / "acme.dcf"
    dcf.write_text(EDS, encoding="utf-8")
    assert not custom_panes.FileSource(dcf).needs_new_name
