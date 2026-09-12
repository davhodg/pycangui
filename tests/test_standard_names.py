# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""The parts of UDS and J1939 that are the same on every ECU.

What is standard is filled in, what is copyrighted or manufacturer specific is
left to the hooks -- and the call that fetches the standard answer lives in the
hook file, so it can be edited or removed.
"""

from pathlib import Path

import pytest

from pycangui.uds.standard import did_name, did_range


# --- UDS data identifiers: ISO 14229-1, by way of udsoncan ---------------------------
@pytest.mark.parametrize(
    ("did", "expected"),
    [
        (0xF190, "VIN"),
        (0xF186, "Active diagnostic session"),
        # udsoncan spells the ISO names out and runs the words together,
        # which is right for a constant and unreadable in a dropdown.
        (0xF18C, "ECU serial number"),
        (0xF180, "Boot software identification"),
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


# --- J1939 names, which live in the hook file ---------------------------------------
def test_the_j1939_tables_are_in_the_hook_file_not_the_package():
    """So that what is known is visible, and adding to it is obvious."""
    import pycangui.j1939 as pkg

    assert not hasattr(pkg, "FMI_NAMES"), "the table belongs in hooks/j1939.py"
    assert not hasattr(pkg, "PGN_NAMES"), "and so does this one"

    source = (Path(__file__).resolve().parents[1] / "pycangui/hooks/j1939.py").read_text(
        encoding="utf-8"
    )
    assert "FMI_NAMES: dict[int, str] = {" in source
    assert "PGN_NAMES: dict[int, str] = {" in source
    assert '4: "Voltage below normal' in source, "filled in, not commented out"
    assert '65226: "DM1"' in source


# --- the hooks carry the defaults ----------------------------------------------------
@pytest.fixture
def hooks(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    from pycangui.core.context import Context
    from pycangui.core.hooks import Hooks

    return Hooks(Context(log=print))


def test_the_standard_answer_comes_through_the_hook(app, hooks):
    """The lookups are in the hook files, so a user can change or drop them."""
    assert hooks.call("uds", "did_label", 0xF190) == "VIN"
    assert hooks.call("j1939", "fmi_description", 3) == (
        "Voltage above normal, or shorted to high source"
    )
    assert hooks.call("j1939", "pgn_name", 65226) == "DM1"


def test_a_reserved_failure_mode_is_left_for_you_to_fill_in(app, hooks):
    """22 to 30 are reserved by SAE, so pycangui does not invent them."""
    assert hooks.call("j1939", "fmi_description", 25) is None

    path = Path(hooks.ctx.hooks_dir, "j1939.py")
    text = path.read_text(encoding="utf-8")
    path.write_text(
        text.replace(
            "FMI_NAMES: dict[int, str] = {", 'FMI_NAMES: dict[int, str] = {25: "Our own mode",'
        ),
        encoding="utf-8",
    )
    hooks.reload()
    assert not hooks.errors(), hooks.errors()
    assert hooks.call("j1939", "fmi_description", 25) == "Our own mode"


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
    assert hooks.call("uds", "did_label", 0xF18C) == "ECU serial number"


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


def test_a_multi_line_import_survives_being_copied_into_a_hook_file(app, hooks):
    """The stub updater used to take the first line of one and drop the rest.

    That put "from pycangui.uds.standard import (" into the user's file and
    broke every hook in it, which is a poor reward for pressing Update.
    """
    from pathlib import Path

    path = Path(hooks.ctx.hooks_dir, "uds.py")
    path.write_text(
        '"""mine"""\n'
        "from pycangui.core.hooks import hook\n\n\n"
        "@hook\n"
        "def security_key(level, seed, *, ctx):\n"
        "    return None\n",
        encoding="utf-8",
    )
    hooks.update_stubs()
    text = path.read_text(encoding="utf-8")
    compile(text, str(path), "exec")  # the whole point: it still parses
    assert "import (" not in text, "an import was cut in half"

    hooks.reload()
    assert not hooks.errors(), hooks.errors()
    assert hooks.call("uds", "did_label", 0xF190) == "VIN"


def test_the_names_python_puts_on_a_class_are_not_mistaken_for_identifiers(app):
    """vars() on a udsoncan class hands back __firstlineno__, which is 18.

    That was quietly making identifier 0x0012 and routine 0x0057 look as
    though ISO 14229-1 named them individually.
    """
    from pycangui.uds.standard import did_name, routine_name

    assert did_name(0x0012) == ""
    assert routine_name(0x0057) == ""


def test_the_dropdown_lists_are_the_ones_iso_names(app):
    from pycangui.uds.standard import did_names, routine_names

    dids = did_names()
    assert dids[0xF190] == "VIN" and 0x0012 not in dids
    assert set(routine_names()) == {0xE200, 0xFF00, 0xFF01, 0xFF02}
