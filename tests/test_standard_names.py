"""The parts of UDS and J1939 that are the same on every ECU.

What is standard is filled in, what is copyrighted or manufacturer specific is
left to the hooks -- and the call that fetches the standard answer lives in the
hook file, so it can be edited or removed.
"""

import pytest

from pycangui.j1939 import FMI_NAMES, fmi_name
from pycangui.uds.standard import did_name, did_range


# --- UDS data identifiers: ISO 14229-1, by way of udsoncan ---------------------------
@pytest.mark.parametrize(
    ("did", "expected"),
    [
        (0xF190, "VIN"),
        (0xF186, "Active diagnostic session"),
        (0xF18C, "ECUSerialNumberDataIdentifier"),
    ],
)
def test_did_name(did, expected):
    assert did_name(did) == expected


@pytest.mark.parametrize("did", [0x0102, 0x0200, 0xF400, 0x8000])
def test_an_identifier_in_a_range_has_no_name_of_its_own(did):
    """Otherwise every DID an ECU actually uses carries "(manufacturer
    specific)" on every line: true, and no help after the first time."""
    assert did_name(did) == ""
    assert did_range(did), "the range itself is still worth knowing"


def test_every_identifier_belongs_to_some_range():
    for did in (0x0000, 0x1234, 0x8000, 0xA600, 0xF1FF, 0xFFFF):
        assert did_range(did), f"0x{did:04X} came back empty"


# --- J1939 failure modes: SAE J1939-73 ----------------------------------------------
def test_fmi_names_cover_the_defined_values():
    assert fmi_name(4) == "Voltage below normal, or shorted to low source"
    assert fmi_name(31) == "Condition exists"
    assert set(FMI_NAMES) <= set(range(32)), "an FMI is five bits"


def test_reserved_fmis_say_nothing_rather_than_guess():
    assert fmi_name(25) == ""
    assert fmi_name(99) == ""


# --- the hooks carry the defaults ----------------------------------------------------
@pytest.fixture
def hooks(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    from pycangui.core.context import Context
    from pycangui.core.hooks import Hooks

    return Hooks(Context(log=print))


def test_the_standard_answer_comes_through_the_hook(app, hooks):
    """The library call is in hooks/uds.py, so a user can change or drop it."""
    assert hooks.call("uds", "did_label", 0xF190) == "VIN"
    assert hooks.call("j1939", "fmi_description", 3) == (
        "Voltage above normal, or shorted to high source"
    )


def test_a_user_table_wins_over_the_standard_one(app, hooks):
    """Filling in DID_NAMES is the documented way to override a name."""
    from pathlib import Path

    path = Path(hooks.ctx.hooks_dir, "uds.py")
    text = path.read_text(encoding="utf-8")
    path.write_text(
        text.replace(
            "DID_NAMES: dict[int, str] = {",
            'DID_NAMES: dict[int, str] = {0xF190: "Chassis number", 0x0101: "Battery volts",',
        ),
        encoding="utf-8",
    )
    hooks.reload()
    assert not hooks.errors(), hooks.errors()
    assert hooks.call("uds", "did_label", 0xF190) == "Chassis number", "mine beats ISO's"
    assert hooks.call("uds", "did_label", 0x0101) == "Battery volts", "and names one ISO cannot"
    assert hooks.call("uds", "did_label", 0xF18C) == "ECUSerialNumberDataIdentifier"


def test_an_empty_string_suppresses_the_standard_name(app, hooks):
    """None means "do the usual thing", so it cannot mean "show nothing".

    Deleting the fallback from the user's copy is not enough on its own:
    returning None hands the question to pycangui's own copy of the function,
    which still looks the name up.  An empty string is an answer.
    """
    from pathlib import Path

    path = Path(hooks.ctx.hooks_dir, "uds.py")
    text = path.read_text(encoding="utf-8")

    # Dropping the call alone: pycangui answers instead, so ISO still wins.
    path.write_text(
        text.replace(
            "return DID_NAMES.get(did) or did_name(did) or None", "return DID_NAMES.get(did)"
        ),
        encoding="utf-8",
    )
    hooks.reload()
    assert hooks.call("uds", "did_label", 0xF190) == "VIN"

    # Saying "nothing" explicitly is what actually turns it off.
    path.write_text(
        text.replace(
            "return DID_NAMES.get(did) or did_name(did) or None",
            'return DID_NAMES.get(did) or ""',
        ),
        encoding="utf-8",
    )
    hooks.reload()
    assert not hooks.errors(), hooks.errors()
    assert hooks.call("uds", "did_label", 0xF190) == ""


def test_no_dtc_description_is_invented(app, hooks):
    """There is no standard list, so an unknown DTC must not get a made-up one."""
    assert hooks.call("uds", "dtc_description", 0x012312) is None
    assert hooks.call("j1939", "spn_description", 190) is None


def test_an_old_hook_file_gains_the_tables_it_needs(app, hooks):
    """A hook appended without its table would raise on every call."""
    from pathlib import Path

    path = Path(hooks.ctx.hooks_dir, "uds.py")
    path.write_text(
        '"""mine"""\n'
        "from pycangui.core.hooks import hook\n\n\n"
        "@hook\n"
        "def security_key(level, seed, *, ctx):\n"
        "    return bytes(b ^ 0xFF for b in seed)\n",
        encoding="utf-8",
    )
    added = hooks.update_stubs()["uds"]
    assert "did_label" in added
    text = path.read_text(encoding="utf-8")
    assert "b ^ 0xFF" in text, "the user's own code must survive untouched"
    assert "DID_NAMES" in text, "and the table the new hook reads must come with it"

    hooks.reload()
    assert not hooks.errors(), hooks.errors()
    assert hooks.call("uds", "did_label", 0xF190) == "VIN"
    assert hooks.call("uds", "security_key", 1, b"\x01") == b"\xfe"
