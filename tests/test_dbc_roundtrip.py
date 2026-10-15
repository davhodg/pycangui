# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""What a DBC file loses when cantools reads it and writes it back.

The files in tests/dbc are written by hand from the format's grammar, a part
of it each, so that what is found here is what any file with that part in it
would lose. What they show is what the editor can promise.
"""

from pathlib import Path

import pytest

from pycangui import resources
from pycangui.core.dbc_roundtrip import Finding, main, report, round_trip, statements

FILES = Path(__file__).parent / "dbc"


def kinds(findings):
    return sorted({(f.kind, f.what) for f in findings})


@pytest.mark.parametrize("name", ["attributes.dbc", "tables_and_comments.dbc"])
def test_attributes_value_tables_and_comments_come_back_whole(name):
    """Definitions, defaults, values on every kind of object, relation
    attributes, value tables, comments with quotes and line breaks, several
    senders, float and double signals: all kept, in order."""
    assert round_trip(FILES / name) == []


def test_the_demo_database_comes_back_whole():
    assert round_trip(resources.path("demo.dbc")) == []


def test_multiplexing_comes_back_meaning_the_same_written_more_fully():
    """Every multiplexed signal is given an SG_MUL_VAL_ line, simple ones
    too: the same multiplexing, said the long way. Nothing else differs."""
    found = round_trip(FILES / "multiplexed.dbc")
    assert [f.where for f in found] == ["SG_MUL_VAL_"]


def test_a_long_name_is_written_short_with_the_whole_name_as_an_attribute():
    """The format's own way past its 32-character limit, which other tools
    read as cantools does. The names read back whole; the file gains the
    attributes that carry them."""
    found = round_trip(FILES / "extras.dbc")
    assert {f.kind for f in found} == {"statement", "attribute"}
    assert not any(f.kind in ("message", "signal") for f in found), "nothing read back differs"


def test_a_file_with_what_cantools_does_not_read_is_not_read_at_all():
    """A baud rate on BS_, and ENVVAR_DATA_: both in the format, neither read.
    A file with either cannot be loaded, let alone edited."""
    assert kinds(round_trip(FILES / "not_read.dbc"))[0][0] == "unreadable"


# --- the census and the report ----------------------------------------------------------------
def test_statements_are_counted_by_what_they_are_about():
    text = (FILES / "attributes.dbc").read_text(encoding="utf-8")
    counted = statements(text)
    assert counted["BA_ BU_"] == 3 and counted["BA_ BO_"] == 4 and counted["BA_ SG_"] == 3
    assert counted["BA_"] == 2, "the network's own"
    assert counted["BA_REL_"] == 2
    assert counted["BO_"] == 2 and counted["SG_"] == 5
    assert counted["NS_"] == 1, "and none of the names listed under it"


def test_a_report_names_nothing_in_the_file_unless_asked():
    """So a report on a file that may not leave its owner's computer can be
    read out without giving the file away."""
    found = [Finding("signal", "SecretMessage.SecretSignal", "a signal's unit differs")]
    assert "Secret" not in report("x.dbc", found)
    assert "Secret" in report("x.dbc", found, names=True)


def test_it_runs_from_the_command_line(capsys):
    assert main([str(FILES / "attributes.dbc")]) == 0
    assert "nothing lost" in capsys.readouterr().out
    assert main([]) == 2
