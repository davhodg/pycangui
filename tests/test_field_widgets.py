"""The seven ways a pane shows an object, and what each of them refuses.

The interesting behaviour is not the displaying, it is the not-writing.  Two
rules run through the lot:

A widget never invents a value.  ``flags`` and ``bits`` write part of an
object, and part of an object cannot be written -- three bits of a word go out
with the other twenty-nine -- so they read first and refuse until they have.

What is refused is refused here.  A number outside the limits the EDS declared
never reaches the bus, because a node is free to clamp it silently and a
parameter that did not take is far worse than one that was not sent.
"""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView

from pycangui.canopen.display import Display
from pycangui.custom_panes.model import Field
from pycangui.ui import field_widgets


def made(item: Field, display: Display | None = None):
    widget = field_widgets.build(item, display or Display())
    written: list[tuple] = []
    asked: list[tuple] = []
    said: list[str] = []
    widget.write_requested.connect(lambda *a: written.append(a))
    widget.read_requested.connect(lambda *a: asked.append(a))
    widget.message.connect(said.append)
    return widget, written, asked, said


# --- what gets built --------------------------------------------------------------------
@pytest.mark.parametrize(
    "kind, expected",
    [
        ("value", field_widgets.ValueWidget),
        ("number", field_widgets.NumberWidget),
        ("hex", field_widgets.HexWidget),
        ("enum", field_widgets.EnumWidget),
        ("flags", field_widgets.FlagsWidget),
        ("bits", field_widgets.BitsWidget),
        ("map", field_widgets.MapWidget),
    ],
)
def test_every_kind_has_a_widget(app, kind, expected):
    """Seven, and no eighth: the vocabulary is the promise."""
    item = Field(index=0x2001, kind=kind, bits={0: "Ready"}, choices={0: "Off"}, width=2)
    assert isinstance(field_widgets.build(item, Display()), expected)


def test_a_kind_nobody_recognises_is_shown_rather_than_guessed_at(app):
    item = Field(index=0x2001, kind="something new")
    assert isinstance(field_widgets.build(item, Display()), field_widgets.ValueWidget)


def test_a_field_asks_for_what_it_needs(app):
    widget, _w, asked, _s = made(Field(index=0x2001, sub=3, kind="number"))
    widget.refresh()
    assert asked == [(0x2001, 3)]


def test_a_value_for_another_object_is_not_this_field_s(app):
    widget, _w, _a, _s = made(Field(index=0x2001, kind="value"))
    widget.set_value(0x2002, 0, 999, None)
    assert widget.value.text() == "--"


# --- read only ---------------------------------------------------------------------------
def test_a_read_only_value_is_said_in_its_own_terms(app):
    widget, _w, _a, _s = made(
        Field(index=0x2001, kind="value"), Display(unit="ms", factor=0.001, decimals=0)
    )
    widget.set_value(0x2001, 0, 20000, None)
    assert widget.value.text() == "20 ms"


def test_a_value_that_could_not_be_read_says_why(app):
    widget, _w, _a, _s = made(Field(index=0x2001, kind="value"))
    widget.set_value(0x2001, 0, None, "Abort 0x06020000")
    assert widget.value.text() == "Abort 0x06020000"


# --- a number ----------------------------------------------------------------------------
def test_a_number_is_typed_in_the_units_it_is_shown_in(app):
    """Shown as 123.4 A, stored as 1234."""
    widget, written, _a, _s = made(
        Field(index=0x2001, kind="number"), Display(unit="A", factor=0.1, decimals=1)
    )
    widget.set_value(0x2001, 0, 1234, None)
    assert widget.edit.text() == "123.4"

    widget.edit.setText("50")
    widget.edit.editingFinished.emit()
    assert written == [(0x2001, 0, 500)]


def test_a_number_outside_the_limits_never_reaches_the_bus(app):
    widget, written, _a, said = made(Field(index=0x2001, kind="number"), Display(low=0, high=1000))
    widget.set_value(0x2001, 0, 250, None)
    widget.edit.setText("2000")
    widget.edit.editingFinished.emit()

    assert written == [], "a node may clamp silently, so it is refused here"
    assert said and "above the maximum" in said[0]
    assert widget.edit.text() == "250", "and put back to what the object actually says"


def test_something_that_is_not_a_number_is_refused_the_same_way(app):
    widget, written, _a, said = made(Field(index=0x2001, kind="number"))
    widget.set_value(0x2001, 0, 7, None)
    widget.edit.setText("fifty")
    widget.edit.editingFinished.emit()
    assert written == []
    assert said and "not a number" in said[0]
    assert widget.edit.text() == "7"


def test_an_empty_box_is_not_a_zero(app):
    """Tabbing through a form must not write every field it passes."""
    widget, written, _a, _s = made(Field(index=0x2001, kind="number"))
    widget.edit.clear()
    widget.edit.editingFinished.emit()
    assert written == []


def test_a_source_that_cannot_be_written_to_does_not_offer_to(app):
    widget, written, _a, _s = made(Field(index=0x2001, kind="number"))
    widget.set_writable(False)
    assert widget.edit.isReadOnly()
    widget.edit.setText("5")
    widget.edit.editingFinished.emit()
    assert written == []


# --- hex ----------------------------------------------------------------------------------
def test_hex_is_shown_and_typed_in_hex(app):
    widget, written, _a, _s = made(Field(index=0x2001, kind="hex"))
    widget.set_value(0x2001, 0, 0x1F, None)
    assert widget.edit.text() == "0x1F"

    widget.edit.setText("2A")  # bare digits are hex, since that is what it shows
    widget.edit.editingFinished.emit()
    assert written == [(0x2001, 0, 0x2A)]


def test_a_hex_box_takes_a_prefix_too(app):
    widget, written, _a, _s = made(Field(index=0x2001, kind="hex"))
    widget.edit.setText("0x2A")
    widget.edit.editingFinished.emit()
    assert written == [(0x2001, 0, 0x2A)]


# --- a named choice -------------------------------------------------------------------------
def test_a_dropdown_offers_what_the_file_named(app):
    item = Field(index=0x2001, kind="enum", choices={0: "Off", 1: "Run", 2: "Fault"})
    widget, written, _a, _s = made(item)
    assert widget.box.count() == 3

    widget.set_value(0x2001, 0, 1, None)
    assert "Run" in widget.box.currentText()

    widget.box.setCurrentIndex(2)
    widget.box.activated.emit(2)
    assert written == [(0x2001, 0, 2)]


def test_a_value_nobody_named_is_admitted_rather_than_hidden(app):
    """A dropdown quietly showing the wrong entry is worse than one that says
    it does not know this value."""
    widget, _w, _a, _s = made(Field(index=0x2001, kind="enum", choices={0: "Off", 1: "Run"}))
    widget.set_value(0x2001, 0, 7, None)
    assert "7" in widget.box.currentText() and "not named" in widget.box.currentText()


# --- a word of flags --------------------------------------------------------------------------
def test_only_the_named_bits_get_a_tick(app):
    """A word with three meaningful bits should not put 32 boxes on a form."""
    widget, _w, _a, _s = made(Field(index=0x2001, kind="flags", bits={0: "Ready", 3: "Fault"}))
    assert set(widget.boxes) == {0, 3}


def test_the_ticks_follow_the_word(app):
    widget, _w, _a, _s = made(Field(index=0x2001, kind="flags", bits={0: "Ready", 3: "Fault"}))
    widget.set_value(0x2001, 0, 0b1001, None)
    assert widget.boxes[0].isChecked() and widget.boxes[3].isChecked()
    widget.set_value(0x2001, 0, 0b0001, None)
    assert widget.boxes[0].isChecked() and not widget.boxes[3].isChecked()


def test_ticking_one_flag_keeps_the_rest_of_the_word(app):
    widget, written, _a, _s = made(Field(index=0x2001, kind="flags", bits={0: "Ready", 3: "Fault"}))
    widget.set_value(0x2001, 0, 0b1010_0001, None)
    widget.boxes[3].click()
    assert written == [(0x2001, 0, 0b1010_1001)], "the bits nobody is showing are still there"


def test_a_flag_will_not_be_written_before_the_word_is_read(app):
    """Writing it would clear every bit the pane is not showing."""
    widget, written, asked, said = made(Field(index=0x2001, kind="flags", bits={3: "Fault"}))
    widget.boxes[3].click()

    assert written == []
    assert said and "not read yet" in said[0]
    assert not widget.boxes[3].isChecked(), "and the tick goes back"
    assert asked == [(0x2001, 0)], "and it asks, so the next attempt can work"


# --- a field inside a word ---------------------------------------------------------------------
def test_some_bits_of_a_word_are_read_out_of_it(app):
    widget, _w, _a, _s = made(Field(index=0x2001, kind="bits", first=4, width=3))
    widget.set_value(0x2001, 0, 0x50, None)
    assert widget.edit.text() == "5"


def test_writing_some_bits_keeps_the_others(app):
    widget, written, _a, _s = made(Field(index=0x2001, kind="bits", first=4, width=3))
    widget.set_value(0x2001, 0, 0xFF, None)
    widget.edit.setText("2")
    widget.edit.editingFinished.emit()
    assert written == [(0x2001, 0, 0xAF)]


def test_a_value_too_wide_for_the_field_is_refused(app):
    widget, written, _a, said = made(Field(index=0x2001, kind="bits", first=4, width=3))
    widget.set_value(0x2001, 0, 0x00, None)
    widget.edit.setText("9")  # three bits hold 0..7
    widget.edit.editingFinished.emit()
    assert written == []
    assert said and "does not fit in 3 bits" in said[0]


def test_named_bits_get_a_dropdown_and_unnamed_ones_a_box(app):
    """Once the bits are picked out that is the only difference between them."""
    named, _w, _a, _s = made(
        Field(index=0x2001, kind="bits", first=0, width=2, choices={0: "Off", 1: "Run"})
    )
    plain, _w2, _a2, _s2 = made(Field(index=0x2001, kind="bits", first=0, width=2))
    assert named.box is not None and named.edit is None
    assert plain.edit is not None and plain.box is None


def test_bits_will_not_be_written_before_the_word_is_read(app):
    widget, written, asked, said = made(Field(index=0x2001, kind="bits", first=4, width=3))
    widget.edit.setText("2")
    widget.edit.editingFinished.emit()
    assert written == []
    assert said and "not read yet" in said[0]
    assert asked == [(0x2001, 0)]


# --- a map --------------------------------------------------------------------------------------
def test_a_map_asks_how_many_points_before_asking_what_they_are(app):
    """An array says how many entries it has in sub 0.  Asking for the rest
    first would be guessing at the size of somebody's curve."""
    widget, _w, asked, _s = made(Field(index=0x2100, kind="map"))
    widget.refresh()
    assert asked == [(0x2100, 0)]

    widget.set_value(0x2100, 0, 3, None)
    assert asked[1:] == [(0x2100, 1), (0x2100, 2), (0x2100, 3)]
    assert widget.table.rowCount() == 3


def test_the_points_fill_the_table_and_the_graph_together(app):
    widget, _w, _a, _s = made(Field(index=0x2100, kind="map"))
    widget.refresh()
    widget.set_value(0x2100, 0, 3, None)
    for point, value in ((1, 10), (2, 20), (3, 30)):
        widget.set_value(0x2100, point, value, None)

    assert [widget.table.item(r, 1).text() for r in range(3)] == ["10", "20", "30"]
    xs, ys = widget.curve.getData()
    assert list(ys) == [10, 20, 30]
    assert list(xs) == [1, 2, 3], "the sub-index is the X where no X array was named"


def test_a_map_can_take_its_x_from_another_array(app):
    widget, _w, asked, _s = made(Field(index=0x2100, kind="map", x_index=0x2101))
    widget.refresh()
    widget.set_value(0x2100, 0, 2, None)
    assert (0x2101, 1) in asked and (0x2101, 2) in asked

    widget.set_value(0x2100, 1, 5, None)
    widget.set_value(0x2100, 2, 9, None)
    widget.set_value(0x2101, 1, 100, None)
    widget.set_value(0x2101, 2, 200, None)
    xs, ys = widget.curve.getData()
    assert list(xs) == [100, 200] and list(ys) == [5, 9]


def test_editing_a_point_writes_that_sub_index(app):
    widget, written, _a, _s = made(Field(index=0x2100, kind="map"))
    widget.refresh()
    widget.set_value(0x2100, 0, 2, None)
    widget.set_value(0x2100, 1, 10, None)
    widget.set_value(0x2100, 2, 20, None)

    widget.table.item(1, 1).setText("25")
    assert written == [(0x2100, 2, 25)], "row two is sub-index two"


def test_a_point_outside_the_limits_is_refused_like_any_other_number(app):
    widget, written, _a, said = made(Field(index=0x2100, kind="map"), Display(low=0, high=100))
    widget.refresh()
    widget.set_value(0x2100, 0, 1, None)
    widget.set_value(0x2100, 1, 50, None)

    widget.table.item(0, 1).setText("500")
    assert written == []
    assert said and "point 1" in said[0] and "above the maximum" in said[0]
    assert widget.table.item(0, 1).text() == "50", "and put back"


def test_a_map_is_scaled_the_way_a_number_is(app):
    widget, written, _a, _s = made(
        Field(index=0x2100, kind="map"), Display(unit="A", factor=0.1, decimals=1)
    )
    widget.refresh()
    widget.set_value(0x2100, 0, 1, None)
    widget.set_value(0x2100, 1, 1234, None)
    assert widget.table.item(0, 1).text() == "123.4"

    widget.table.item(0, 1).setText("50")
    assert written == [(0x2100, 1, 500)]


def test_a_map_that_cannot_be_written_is_not_editable(app):
    widget, _w, _a, _s = made(Field(index=0x2100, kind="map"))
    widget.set_writable(False)
    assert widget.table.editTriggers() == QAbstractItemView.NoEditTriggers


def test_a_nonsense_count_does_not_ask_for_a_thousand_points(app):
    widget, _w, asked, _s = made(Field(index=0x2100, kind="map"))
    widget.refresh()
    widget.set_value(0x2100, 0, 100000, None)
    assert len(asked) - 1 == field_widgets.MAX_POINTS


# --- the pane's word over the source's -------------------------------------------------------
def test_a_panel_may_say_what_an_object_means(app):
    """The hook says what an object means on this product; the pane says what
    it means on this screen, which is occasionally narrower."""
    item = Field(index=0x2001, kind="number", label="Peak current", unit="A", factor=0.1)
    widget, _w, _a, _s = made(item, Display(name="Object 2001", low=0, high=1000))
    assert widget.label_text() == "Peak current"
    assert widget.display.unit == "A"
    assert (widget.display.low, widget.display.high) == (0, 1000), "the limits are not discarded"


def test_the_tooltip_carries_what_the_column_cannot(app):
    widget, _w, _a, _s = made(
        Field(index=0x2001, sub=2, kind="number"),
        Display(description="Peak phase current", low=0, high=1000, unit="A", factor=0.1),
    )
    widget.set_value(0x2001, 2, 1234, None)
    tip = widget.edit.toolTip()
    assert "0x2001:02" in tip
    assert "Peak phase current" in tip
    assert "Limits:" in tip
    assert "Raw: 1234" in tip, "a scaled reading is a claim; the raw one is what the wire said"


def test_a_flags_widget_shows_which_bit_is_which(app):
    widget, _w, _a, _s = made(Field(index=0x2001, kind="flags", bits={0: "Ready", 3: "Fault"}))
    assert widget.boxes[3].text() == "Fault"
    widget.set_value(0x2001, 0, 0, None)
    assert widget.boxes[3].toolTip() == "Bit 3"


def test_a_map_labels_its_axes_from_the_panel(app):
    widget, _w, _a, _s = made(
        Field(index=0x2100, kind="map", x_label="Speed", y_label="Torque limit")
    )
    assert widget.table.horizontalHeaderItem(0).text() == "Speed"
    assert widget.table.horizontalHeaderItem(1).text() == "Torque limit"


def test_a_map_without_an_x_array_will_not_let_you_edit_the_x(app):
    """The sub-index number is the X, and a sub-index is not a value."""
    widget, _w, _a, _s = made(Field(index=0x2100, kind="map"))
    widget.refresh()
    widget.set_value(0x2100, 0, 2, None)
    assert not widget.table.item(0, 0).flags() & Qt.ItemIsEditable
