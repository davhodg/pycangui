# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""What an EDS says about an object, including the parts a parser throws away.

CiA 306 defines no key for a unit or for scaling, so a maker with that to say
either invents a key or hides it in a comment -- and a comment survives every
conforming reader by being ignored.  Neither reaches the object dictionary the
``canopen`` package builds.

The file below uses an invented tag on purpose.  None of these fields are
CANopen, so pycangui reads none of them: what is tested is that they arrive
intact, are shown, and survive being written back out.
"""

import canopen
import pytest

from pycangui.canopen import eds_extras
from pycangui.canopen.dcf import values_from, write_dcf
from pycangui.canopen.display import (
    Display,
    as_number,
    from_variable,
    limits_text,
    out_of_range,
    text,
    with_overrides,
)

EDS = """[FileInfo]
FileName=acme.eds
EDSVersion=4.0
;ACMEFIELD FILE_COMMENT=not about any object
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
SupportedObjects=2
1=0x2001
2=0x2002
[2001]
ParameterName=Motor current
ObjectType=7
DataType=3
AccessType=rw
LowLimit=0
HighLimit=1000
DefaultValue=0
ObjFlags=1
;ACMEFIELD UNITS=A
;ACMEFIELD SCALING=0.1
;ACMEFIELD DESCRIPTION=Peak phase current
[2002]
ParameterName=Mode record
ObjectType=9
SubNumber=2
[2002sub0]
ParameterName=Number of entries
ObjectType=7
DataType=5
AccessType=ro
DefaultValue=1
[2002sub1]
ParameterName=Mode
ObjectType=7
DataType=5
AccessType=rw
DefaultValue=0
;ACMEFIELD CATEGORY=CONFIGURATION
"""


@pytest.fixture
def eds(tmp_path):
    path = tmp_path / "acme.eds"
    path.write_text(EDS, encoding="utf-8")
    return path


# --- what the parser threw away --------------------------------------------------------
def test_a_comment_inside_an_object_is_collected(eds):
    extras = eds_extras(eds)
    assert extras[(0x2001, 0)]["ACMEFIELD UNITS"] == "A"
    assert extras[(0x2001, 0)]["ACMEFIELD SCALING"] == "0.1"
    assert extras[(0x2001, 0)]["ACMEFIELD DESCRIPTION"] == "Peak phase current"


def test_an_ordinary_key_the_parser_does_not_read_is_collected_too(eds):
    assert eds_extras(eds)[(0x2001, 0)]["ObjFlags"] == "1"


def test_the_keys_the_parser_does_read_are_not_repeated(eds):
    """They are on the parsed variable; having them twice invites disagreement."""
    collected = eds_extras(eds)[(0x2001, 0)]
    for key in ("ParameterName", "LowLimit", "HighLimit", "DataType"):
        assert key not in collected


def test_a_sub_index_is_kept_apart_and_a_bare_object_is_sub_zero(eds):
    extras = eds_extras(eds)
    assert (0x2002, 1) in extras
    assert (0x2002, 0) not in extras
    assert (0x2001, 0) in extras


def test_comments_outside_an_object_are_not_attributed_to_one(eds):
    """[FileInfo] belongs to the file, not to any object."""
    for fields in eds_extras(eds).values():
        assert "ACMEFIELD FILE_COMMENT" not in fields


def test_an_unreadable_file_is_no_extras_rather_than_an_exception(tmp_path):
    assert eds_extras(tmp_path / "nothing.eds") == {}


def test_what_the_parser_does_keep_is_read_from_the_variable(eds):
    """Unit and Factor, where a file uses those keys, are already parsed."""
    var = canopen.import_od(str(eds))[0x2001]
    display = from_variable(var)
    assert display.name == "Motor current"
    assert (display.low, display.high) == (0, 1000)


# --- pycangui interprets none of it -----------------------------------------------------
def test_nothing_is_read_from_the_vendor_fields_by_default(app, tmp_path, monkeypatch):
    """None of those fields are CANopen: the tag, the names and the meanings
    are one maker's convention, and the next maker's SCALING could be a
    divisor.  A wrong scaling is worse than a raw number."""
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    from pycangui.core.context import Context
    from pycangui.core.hooks import Hooks

    hooks = Hooks(Context(log=print))
    extras = {"ACMEFIELD UNITS": "A", "ACMEFIELD SCALING": "0.1"}
    assert hooks.call("canopen", "object_display", 0x2001, 0, extras, None) is None


def test_the_hook_is_where_the_meaning_is_added(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    from pathlib import Path

    from pycangui.core.context import Context
    from pycangui.core.hooks import Hooks

    hooks = Hooks(Context(log=print))
    path = Path(hooks.ctx.hooks_dir, "canopen.py")
    source = path.read_text(encoding="utf-8")
    path.write_text(
        source.replace(
            "OBJECT_DISPLAY: dict[tuple[int, int], dict] = {}",
            'OBJECT_DISPLAY: dict[tuple[int, int], dict] = {(0x2001, 0): {"unit": "A"}}',
        ),
        encoding="utf-8",
    )
    hooks.reload()
    assert not hooks.errors(), hooks.errors()
    assert hooks.call("canopen", "object_display", 0x2001, 0, {}, None) == {"unit": "A"}


# --- how a value is shown ---------------------------------------------------------------
def test_a_scaled_value_is_shown_in_its_own_units():
    assert text(Display(unit="ms", factor=0.001), 20000) == "20 ms"


def test_only_the_converted_value_is_shown():
    """The raw one goes on the tooltip: it is worth having and not worth a column."""
    assert "[" not in text(Display(unit="A", factor=0.1, decimals=1), 1234)
    assert text(Display(unit="A", factor=0.1, decimals=1), 1234) == "123.4 A"


def test_an_unscaled_number_keeps_its_hex():
    assert text(Display(), 1234) == "1234 (0x4D2)"
    assert text(Display(), 5) == "5"


def test_a_named_value_says_what_it_means():
    assert text(Display(choices={0: "Off", 1: "Run"}), 1) == "1 (Run)"


def test_limits_are_shown_in_both(app):
    assert limits_text(Display(low=0, high=1000)) == "0 to 1000"
    assert limits_text(Display(low=0, high=1000, unit="A", factor=0.1, decimals=1)) == (
        "0 to 1000 (0.0 to 100.0 A)"
    )
    assert limits_text(Display()) == "", "a file that did not say gets no row"


def test_a_value_outside_the_limits_says_which_way():
    display = Display(low=0, high=1000)
    assert "above" in out_of_range(display, 2000)
    assert "below" in out_of_range(display, -1)
    assert out_of_range(display, 500) == ""


def test_the_bases_a_person_types_are_accepted():
    assert as_number("0x10") == 16
    assert as_number("16") == 16
    assert as_number("1.5") == 1.5
    assert as_number("nonsense") is None


def test_a_hook_answer_does_not_discard_what_the_file_said():
    """Naming a unit must not lose the limits the EDS declared."""
    base = Display(name="Motor current", low=0, high=1000)
    merged = with_overrides(base, {"unit": "A", "factor": 0.1})
    assert (merged.low, merged.high) == (0, 1000)
    assert merged.name == "Motor current"
    assert (merged.unit, merged.factor) == ("A", 0.1)


def test_a_hook_typo_does_not_take_the_object_with_it():
    merged = with_overrides(Display(unit="A"), {"factor": "not a number"})
    assert merged.unit == "A" and merged.factor == 1.0


# --- a DCF is the EDS with the values in ------------------------------------------------
def test_the_comments_survive_being_written_back(eds):
    source = eds.read_text(encoding="utf-8")
    out = write_dcf(source, values_from({(0x2001, 0): 500}), node_id=5)
    assert out.count(";ACMEFIELD") == source.count(";ACMEFIELD")
    assert "ACMEFIELD SCALING=0.1" in out


def test_the_value_lands_with_the_keys_not_after_the_comments(eds):
    out = write_dcf(eds.read_text(encoding="utf-8"), values_from({(0x2001, 0): 500}), node_id=5)
    body = out.split("[2001]")[1].split("[")[0]
    assert body.index("ParameterValue=500") < body.index(";ACMEFIELD")


def test_an_object_with_no_value_is_left_exactly_as_it_was(eds):
    """A parameter the node would not give up is better absent than guessed at."""
    out = write_dcf(eds.read_text(encoding="utf-8"), values_from({(0x2001, 0): 500}), node_id=5)
    assert out.count("ParameterValue=") == 1


def test_the_result_is_a_dcf_a_reader_accepts(eds, tmp_path):
    out = write_dcf(eds.read_text(encoding="utf-8"), values_from({(0x2001, 0): 500}), node_id=5)
    path = tmp_path / "out.dcf"
    path.write_text(out, encoding="utf-8", newline="")
    od = canopen.import_od(str(path))
    assert od.node_id == 5
    assert od[0x2001].value == 500


def test_saving_again_replaces_rather_than_repeats(eds):
    once = write_dcf(eds.read_text(encoding="utf-8"), values_from({(0x2001, 0): 500}), node_id=5)
    twice = write_dcf(once, values_from({(0x2001, 0): 600}), node_id=7)
    assert twice.count("ParameterValue=") == 1
    assert "ParameterValue=600" in twice
    assert twice.count("[DeviceComissioning]") == 1
    assert "NodeID=7" in twice and "NodeID=5" not in twice


def test_a_negative_value_is_written_so_it_can_be_read_back(eds, tmp_path):
    """The library's own writer formats one as "0x-4D2", which it then refuses."""
    out = write_dcf(eds.read_text(encoding="utf-8"), values_from({(0x2001, 0): -1234}), node_id=5)
    assert "ParameterValue=-1234" in out
    path = tmp_path / "neg.dcf"
    path.write_text(out, encoding="utf-8", newline="")
    assert canopen.import_od(str(path))[0x2001].value == -1234
