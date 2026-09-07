"""Two configurations side by side, and what is different about them.

"What is different about the unit that fails" is the question a commissioning
tool exists to answer, and pycangui could not answer it at all: it could save a
DCF and apply one, with nothing in between.

Most of this file is about the three things a comparison must not get wrong --
calling an absent object a difference, deciding for itself what to read, and
comparing two node-IDs without saying so -- because each of those produces a
confident answer that is wrong, which is worse than no answer.
"""

from pathlib import Path

import pytest

from pycangui import resources
from pycangui.canopen import compare
from pycangui.canopen.compare import DIFFERENT, LEFT_ONLY, RIGHT_ONLY, SAME, Reading

DEMO = str(resources.path("demo.eds"))


def reading(values, names=None, label="a", node_id=None):
    return Reading(label=label, values=values, names=names or {}, node_id=node_id)


def states(rows):
    return {row.where: row.state for row in rows}


# --- what agrees and what does not ---------------------------------------------------------
def test_the_same_value_on_both_sides_is_the_same():
    rows = compare.compare(reading({(0x1017, 0): 1000}), reading({(0x1017, 0): 1000}))
    assert states(rows) == {(0x1017, 0): SAME}


def test_a_changed_value_is_a_difference():
    rows = compare.compare(reading({(0x1017, 0): 1000}), reading({(0x1017, 0): 500}))
    assert rows[0].state == DIFFERENT
    assert (rows[0].left, rows[0].right) == (1000, 500)


def test_an_object_on_one_side_only_is_not_called_a_difference():
    """A file carries a value only for the objects that had one and a node
    answers only what it implements, so this is common and almost never a
    difference in configuration."""
    rows = compare.compare(reading({(0x2000, 0): 1}), reading({(0x2001, 0): 2}))
    assert states(rows) == {(0x2000, 0): LEFT_ONLY, (0x2001, 0): RIGHT_ONLY}
    assert not any(row.differs for row in rows)


def test_the_rows_come_back_in_index_order():
    """So that a comparison reads like the object dictionary it is about."""
    left = reading({(0x2000, 1): 1, (0x1017, 0): 2, (0x2000, 0): 3})
    rows = compare.compare(left, reading({}))
    assert [row.where for row in rows] == [(0x1017, 0), (0x2000, 0), (0x2000, 1)]


def test_the_communication_objects_below_0x1000_are_left_out():
    """The same floor save_dcf uses, so a DCF pycangui wrote and a node it read
    cover the same ground."""
    rows = compare.compare(reading({(0x1000, 0): 1, (0x0007, 0): 2}), reading({}))
    assert [row.index for row in rows] == [0x1000]


def test_an_object_is_named_from_whichever_side_knows_it():
    """A node read has no names of its own; the file beside it does."""
    left = reading({(0x1017, 0): 1}, {(0x1017, 0): "Producer heartbeat time"})
    rows = compare.compare(left, reading({(0x1017, 0): 2}))
    assert rows[0].name == "Producer heartbeat time"


# --- what counts as the same value ------------------------------------------------------------
def test_a_number_that_survived_a_round_trip_is_not_a_change():
    """A DCF holds text a parser turned into an integer; a node hands back an
    integer.  Reporting that as a change would flag every object in the file."""
    assert compare.same_value(1000, 1000.0)
    assert compare.same_value(0x1F80, 8064)


def test_a_bit_is_a_bit_however_it_was_written():
    assert compare.same_value(True, 1)
    assert compare.same_value(0, False)


def test_strings_and_bytes_compare_as_themselves():
    assert compare.same_value(b"\x01\x02", bytearray(b"\x01\x02"))
    assert compare.same_value("PYCANGUI", "PYCANGUI")
    assert not compare.same_value("PYCANGUI", "pycangui")


def test_a_value_of_zero_is_a_value():
    """Absent and zero are different answers, and the easy bug here is to treat
    a falsy value as missing."""
    rows = compare.compare(reading({(0x1017, 0): 0}), reading({(0x1017, 0): 0}))
    assert rows[0].state == SAME
    rows = compare.compare(reading({(0x1017, 0): 0}), reading({}))
    assert rows[0].state == LEFT_ONLY


# --- saying how it went ----------------------------------------------------------------------
def test_the_summary_counts_each_sort_separately():
    rows = compare.compare(
        reading({(0x1000, 0): 1, (0x1001, 0): 1, (0x2000, 0): 1}),
        reading({(0x1000, 0): 1, (0x1001, 0): 2, (0x3000, 0): 1}),
    )
    said = compare.summarise(rows)
    assert "1 different" in said
    assert "1 only on the left" in said and "1 only on the right" in said
    assert "1 the same" in said


def test_two_files_that_agree_say_so_plainly():
    rows = compare.compare(reading({(0x1017, 0): 1}), reading({(0x1017, 0): 1}))
    assert "Identical" in compare.summarise(rows)


def test_nothing_to_compare_says_that_rather_than_identical():
    """An empty comparison and an agreeing one are not the same news."""
    assert compare.summarise([]) == "Nothing to compare."


def test_different_node_ids_are_remarked_on_rather_than_refused():
    """Comparing a controller against the file from the one beside it is a
    thing people do on purpose; the COB-ID noise is what they need telling
    about."""
    said = compare.node_id_warning(reading({}, node_id=5), reading({}, node_id=6))
    assert "node 5" in said and "node 6" in said
    assert "not a difference in configuration" in said


def test_the_same_node_id_is_not_remarked_on():
    assert compare.node_id_warning(reading({}, node_id=5), reading({}, node_id=5)) == ""
    assert compare.node_id_warning(reading({}), reading({}, node_id=5)) == ""


def test_the_text_form_carries_the_differences_and_their_labels():
    rows = compare.compare(
        reading({(0x1017, 0): 1000}, {(0x1017, 0): "Heartbeat"}), reading({(0x1017, 0): 500})
    )
    text = compare.as_text(rows, "shipped.dcf", "Node 5")
    assert "shipped.dcf" in text and "Node 5" in text
    assert "0x1017" in text and "Heartbeat" in text


def test_a_missing_value_reads_as_missing_rather_than_as_None():
    assert compare.shown(None) == "--"
    assert compare.shown(0) == "0"
    assert "0x" in compare.shown(4096)


# --- reading a file --------------------------------------------------------------------------
def test_an_eds_is_a_readable_side():
    """Comparing a device against the EDS it was built from answers "what has
    anybody changed on this", which is a different question from "does it match
    the file I was given" and just as often the one being asked."""
    got = compare.read_file(DEMO)
    assert got.label == "demo.eds"
    assert got.names, "an EDS knows what its objects are called"


def written_dcf(tmp_path, values, node_id, name="saved.dcf"):
    """A DCF the way pycangui writes one: the EDS with values filled in."""
    from pycangui.canopen.dcf import write_dcf

    eds_text = Path(DEMO).read_text(encoding="utf-8-sig")
    path = tmp_path / name
    path.write_text(write_dcf(eds_text, values, node_id), encoding="utf-8", newline="")
    return path


def test_a_dcf_carries_the_node_it_was_taken_from(tmp_path):
    """Read out of the text rather than off the parsed dictionary, because the
    parser wants the node-ID in order to do the parsing."""
    got = compare.read_file(written_dcf(tmp_path, {(0x1017, 0): "1000"}, node_id=7))
    assert got.node_id == 7
    assert got.values[(0x1017, 0)] == 1000


def test_two_dcfs_compare_as_the_values_in_them(tmp_path):
    """The whole point, end to end: two files pycangui wrote, and what changed
    between them."""
    before = compare.read_file(written_dcf(tmp_path, {(0x1017, 0): "1000"}, 5, "before.dcf"))
    after = compare.read_file(written_dcf(tmp_path, {(0x1017, 0): "500"}, 5, "after.dcf"))
    changed = [row for row in compare.compare(before, after) if row.differs]
    assert [(row.where, row.left, row.right) for row in changed] == [((0x1017, 0), 1000, 500)]


def test_a_dcf_against_the_eds_it_came_from_shows_what_was_configured(tmp_path):
    """ "What has anybody changed on this device" -- a different question from
    "does it match the file I was given", and just as often the one asked."""
    plain = compare.read_file(DEMO)
    saved = compare.read_file(written_dcf(tmp_path, {(0x1017, 0): "2500"}, 5))
    rows = compare.compare(plain, saved)
    at = next(row for row in rows if row.where == (0x1017, 0))
    assert at.right == 2500
    assert at.state in (DIFFERENT, RIGHT_ONLY), "either way, it is not reported as unchanged"


def test_a_plain_eds_has_no_node_id_of_its_own():
    assert compare.read_file(DEMO).node_id is None


def test_a_file_that_is_not_one_raises_rather_than_comparing_nothing(tmp_path):
    """Silently comparing against an empty reading would report every object
    as missing from one side."""
    bad = tmp_path / "notes.txt"
    bad.write_text("this is not a device file\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Cannot import"):
        compare.read_file(bad)


# --- what a node should be asked for ------------------------------------------------------------
def test_only_the_objects_the_other_side_names_are_read():
    """Reading a whole dictionary to compare it against a file is minutes of
    SDO traffic to answer a question about the objects the file holds."""
    left = reading({(0x1017, 0): 1, (0x2000, 0): 2})
    assert compare.objects_to_read(left) == [(0x1017, 0), (0x2000, 0)]


def test_both_sides_contribute_and_nothing_is_asked_for_twice():
    left = reading({(0x1017, 0): 1})
    right = reading({(0x1017, 0): 1, (0x2000, 0): 2})
    assert compare.objects_to_read(left, right) == [(0x1017, 0), (0x2000, 0)]


def test_nothing_names_anything_means_nothing_to_read():
    """Which the pane turns into a refusal, rather than guessing at a range of
    indices and calling the result a comparison."""
    assert compare.objects_to_read() == []
    assert compare.objects_to_read(reading({})) == []


# --- and the pane ------------------------------------------------------------------------------
@pytest.fixture
def window(app, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings

    from pycangui.ui.main_window import MainWindow

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    win = MainWindow()
    win.show()
    for _ in range(5):
        app.processEvents()
    yield win
    win.close()


@pytest.fixture
def view(window):
    return window.panes.view("compare")


def test_it_is_a_pane_like_any_other(app, window, view):
    assert window.panes.docks["compare"].windowTitle() == "Compare"
    assert view is not None


def test_two_files_are_compared_when_asked(app, window, view, tmp_path):
    from pycangui.ui.compare_view import FILE

    before = written_dcf(tmp_path, {(0x1017, 0): "1000"}, 5, "before.dcf")
    after = written_dcf(tmp_path, {(0x1017, 0): "500"}, 5, "after.dcf")
    for side, path in ((view.left, before), (view.right, after)):
        side.kind.setCurrentText(FILE)
        side.path.setText(str(path))
    view.run()

    assert "1 different" in view.summary.text()
    assert view.table.topLevelItemCount() == 1, "differences only, by default"
    assert view.table.topLevelItem(0).text(0) == "0x1017"


def test_the_columns_are_named_after_the_two_sides(app, window, view, tmp_path):
    """ "Left" and "Right" say nothing once there is something in them."""
    from pycangui.ui.compare_view import FILE

    for side, name in ((view.left, "before.dcf"), (view.right, "after.dcf")):
        side.kind.setCurrentText(FILE)
        side.path.setText(str(written_dcf(tmp_path, {(0x1017, 0): "1"}, 5, name)))
    view.run()
    assert view.table.headerItem().text(3) == "before.dcf"
    assert view.table.headerItem().text(4) == "after.dcf"


def test_showing_everything_shows_what_agreed_too(app, window, view, tmp_path):
    from pycangui.ui.compare_view import FILE

    for side, value in ((view.left, "1000"), (view.right, "500")):
        side.kind.setCurrentText(FILE)
        # One object that differs and one that does not, so there is something
        # for the tick box to reveal.
        values = {(0x1017, 0): value, (0x1018, 1): "0x1A2"}
        side.path.setText(str(written_dcf(tmp_path, values, 5, f"{value}.dcf")))
    view.run()
    assert view.table.topLevelItemCount() == 1, "one difference"

    view.differences_only.setChecked(False)
    assert view.table.topLevelItemCount() == 2, "and one that agreed"


def test_a_file_that_will_not_read_is_reported_rather_than_compared(app, window, view, tmp_path):
    from pycangui.ui.compare_view import FILE

    bad = tmp_path / "notes.txt"
    bad.write_text("not a device file\n", encoding="utf-8")
    view.left.kind.setCurrentText(FILE)
    view.left.path.setText(str(bad))
    view.right.kind.setCurrentText(FILE)
    view.right.path.setText(str(written_dcf(tmp_path, {(0x1017, 0): "1"}, 5)))
    view.run()

    assert "could not be read" in window.log.toPlainText()
    assert view.table.topLevelItemCount() == 0


def test_with_nothing_chosen_it_says_so(app, window, view):
    view.run()
    assert "Pick a file" in window.log.toPlainText()


def test_a_node_is_read_for_the_objects_the_file_names(app, window, view, tmp_path):
    """The reason comparing against a device takes seconds rather than minutes,
    and the reason it works on a device nobody has an EDS for."""
    from pycangui.ui.compare_view import FILE, NODE

    asked = {}

    def read_objects(node_id, wanted, done):
        asked["node"], asked["wanted"] = node_id, list(wanted)
        done({(0x1017, 0): 250}, None)

    window.canopen.read_objects = read_objects
    view.left.kind.setCurrentText(FILE)
    view.left.path.setText(str(written_dcf(tmp_path, {(0x1017, 0): "1000"}, 5)))
    view.right.kind.setCurrentText(NODE)
    view.right.node.addItem("Node 5", 5)
    view.run()

    assert asked["node"] == 5
    assert (0x1017, 0) in asked["wanted"]
    assert asked["wanted"] == compare.objects_to_read(compare.read_file(view.left.path.text()))
    assert "1 different" in view.summary.text()
    assert view.table.topLevelItem(0).text(4) == compare.shown(250)


def test_a_node_that_will_not_answer_is_reported(app, window, view, tmp_path):
    from pycangui.ui.compare_view import FILE, NODE

    window.canopen.read_objects = lambda _n, _w, done: done(None, "no reply")
    view.left.kind.setCurrentText(FILE)
    view.left.path.setText(str(written_dcf(tmp_path, {(0x1017, 0): "1000"}, 5)))
    view.right.kind.setCurrentText(NODE)
    view.right.node.addItem("Node 5", 5)
    view.run()

    assert "could not be read" in window.log.toPlainText()


def test_two_nodes_with_nothing_to_say_what_to_read_are_refused(app, window, view):
    """Rather than reading a guessed-at range of indices and calling the result
    a comparison."""
    from pycangui.ui.compare_view import NODE

    for side in (view.left, view.right):
        side.kind.setCurrentText(NODE)
        side.node.addItem("Node 5", 5)
    view.run()

    assert "nothing to compare" in window.log.toPlainText().lower()


def test_what_the_two_sides_were_comes_back_next_time(app, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings

    from pycangui.ui.compare_view import FILE
    from pycangui.ui.main_window import MainWindow

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    first = MainWindow()
    view = first.panes.view("compare")
    view.left.kind.setCurrentText(FILE)
    view.left.path.setText(str(written_dcf(tmp_path, {(0x1017, 0): "1"}, 5, "a.dcf")))
    view.right.kind.setCurrentText(FILE)
    view.right.path.setText(str(written_dcf(tmp_path, {(0x1017, 0): "2"}, 5, "b.dcf")))
    view.run()
    first.close()

    again = MainWindow()
    assert again.panes.view("compare").left.path.text().endswith("a.dcf")
    again.close()
