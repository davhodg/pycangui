# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""The standard seed and key DLL: bitness, calling it, and the fallback order.

The bitness half is tested against real DLLs rather than fixtures, because
that is the half that goes wrong and a fixture would only prove that the
fixture was built as expected. Every 64-bit Windows carries a 32-bit
kernel32 in SysWOW64 and a 64-bit one in System32, so both answers are
available without shipping a binary.

The calls themselves go to stand-in DLLs: Python functions wrapped as real
C function pointers by ctypes, so the arguments, buffers, lengths and
return codes go through exactly what a DLL's would. A real seed and key DLL
cannot be built here without a compiler, and cannot be committed without
shipping somebody's algorithm.
"""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from pycangui.core import seedkey

WINDOWS = sys.platform == "win32"
X64 = Path(r"C:\Windows\System32\kernel32.dll")
X86 = Path(r"C:\Windows\SysWOW64\kernel32.dll")

needs_windows = pytest.mark.skipif(not WINDOWS, reason="a DLL is a Windows thing")


# --- what a file is --------------------------------------------------------------------
@needs_windows
def test_it_reads_the_bitness_out_of_the_file():
    """Read, not inferred from a failed load: Windows reports the wrong
    bitness as "not a valid Win32 application", which tells nobody what to
    do about it."""
    assert seedkey.machine(X64) == "64-bit"
    assert seedkey.machine(X86) == "32-bit"


def test_something_that_is_not_a_dll_is_not_guessed_at(tmp_path):
    plain = tmp_path / "notes.txt"
    plain.write_text("this is not a PE file", encoding="utf-8")
    assert seedkey.machine(plain) == ""
    assert seedkey.machine(tmp_path / "nothing at all") == ""


def test_a_truncated_dll_does_not_throw(tmp_path):
    """A half-copied file is a real thing to be handed."""
    stub = tmp_path / "half.dll"
    stub.write_bytes(b"MZ" + b"\x00" * 8)
    assert seedkey.machine(stub) == ""


@needs_windows
def test_only_the_other_bitness_needs_another_interpreter():
    ours = X64 if seedkey.host_bits() == 64 else X86
    theirs = X86 if seedkey.host_bits() == 64 else X64
    assert not seedkey.needs_another_python(ours)
    assert seedkey.needs_another_python(theirs)


# --- refusing, with a reason ------------------------------------------------------------
def test_a_missing_file_says_so(tmp_path):
    with pytest.raises(seedkey.SeedKeyError, match="No such file"):
        seedkey.available_privileges(tmp_path / "absent.dll")


@needs_windows
def test_a_dll_of_the_wrong_bitness_says_which_way_round_it_is():
    theirs = X86 if seedkey.host_bits() == 64 else X64
    with pytest.raises(seedkey.SeedKeyError) as raised:
        seedkey.compute_key(theirs, 1, b"\x01\x02")
    said = str(raised.value)
    assert "32-bit" in said and "64-bit" in said, said
    assert "bitness" in said


@needs_windows
def test_a_dll_that_is_not_a_seed_and_key_dll_names_the_missing_export():
    """kernel32 loads perfectly well and exports none of this. The message
    names the function that was wanted rather than claiming a seed and key
    DLL must export both -- one of them is optional."""
    ours = X64 if seedkey.host_bits() == 64 else X86
    with pytest.raises(seedkey.SeedKeyError) as raised:
        seedkey.compute_key(ours, 1, b"")
    assert seedkey.COMPUTE in str(raised.value)


def test_the_privilege_names_are_xcps_resource_bits():
    assert seedkey.names(0x01) == "CAL/PAG"
    assert seedkey.names(0x11) == "CAL/PAG, PGM"
    assert seedkey.names(0) == ""


# --- running it somewhere else -----------------------------------------------------------
def test_an_interpreter_can_be_asked_what_it_is():
    assert seedkey.bits_of(sys.executable) == seedkey.host_bits()


def test_an_interpreter_that_is_not_one_is_zero_rather_than_a_crash(tmp_path):
    assert seedkey.bits_of(tmp_path / "no-such-python.exe") == 0


@needs_windows
def test_the_bridge_carries_the_answer_back(tmp_path):
    """Run through this interpreter, which stands in for the 32-bit one.

    The point is the round trip: this file runs as a script with no
    pycangui on its path, and whatever it says comes back. Here what it
    says is a refusal, since kernel32 is not a seed and key DLL -- and a
    refusal that arrives intact is the same machinery as a key that does.
    """
    with pytest.raises(seedkey.SeedKeyError) as raised:
        seedkey.compute_key_elsewhere(sys.executable, X64, 1, b"\x01\x02")
    assert "XCP_ComputeKeyFromSeed" in str(raised.value)


def test_the_bridge_says_when_the_interpreter_will_not_run(tmp_path):
    with pytest.raises(seedkey.SeedKeyError, match="would not run"):
        seedkey.compute_key_elsewhere(tmp_path / "nope.exe", "any.dll", 1, b"\x01")


def test_the_script_takes_a_dll_a_privilege_and_a_seed(capsys):
    """It is the bridge, so its command line is an interface."""
    assert seedkey.main([]) == 2
    assert "usage" in capsys.readouterr().err

    assert seedkey.main(["no-such.dll", "1", "0102"]) == 1
    assert "No such file" in capsys.readouterr().err


def test_a_module_that_can_run_anywhere_imports_nothing_of_ours():
    """The 32-bit Python somebody has lying around will not have pycangui
    installed, and should not need it."""
    source = Path(seedkey.__file__).read_text(encoding="utf-8")
    for line in source.splitlines():
        assert not line.startswith(("import pycangui", "from pycangui")), line


# --- where it sits in the order ----------------------------------------------------------
@needs_windows
def test_no_dll_and_no_interpreter_says_all_three_ways_out():
    """The message has to carry the whole answer: this is read by somebody
    who has just been told their DLL cannot be loaded."""
    theirs = X86 if seedkey.host_bits() == 64 else X64
    with pytest.raises(seedkey.SeedKeyError) as raised:
        seedkey.key_for(theirs, 1, b"\x01", other_python="")
    said = str(raised.value)
    # Install one, name one, or rebuild the DLL.
    assert "Install" in said and "name one" in said and "rebuild" in said, said


@needs_windows
def test_a_dll_of_our_own_bitness_is_loaded_here(monkeypatch):
    """No subprocess where none is needed: it is a fifth of a second and a
    console window's worth of risk for nothing."""
    ours = X64 if seedkey.host_bits() == 64 else X86
    monkeypatch.setattr(
        seedkey, "compute_key_elsewhere", lambda *a, **k: pytest.fail("went out of process")
    )
    with pytest.raises(seedkey.SeedKeyError):  # kernel32 exports none of it
        seedkey.key_for(ours, 1, b"\x01")


# --- the hook first, then the DLL ---------------------------------------------------------
@pytest.fixture
def ctx(tmp_path, monkeypatch):
    from pycangui.core.context import Context

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    return Context(log=print)


def uds_manager(ctx, hooks):
    from pycangui.core.bus import BusManager
    from pycangui.uds.manager import UdsManager

    return UdsManager(BusManager(), hooks, ctx)


def hooks_answering(ctx, answer):
    """Hooks whose security_key and compute_key return `answer`."""
    from pycangui.core.hooks import Hooks

    out = Hooks(ctx)
    out.call = lambda kind, name, *args: answer
    return out


def test_uds_uses_the_hook_when_it_answers(app, ctx):
    manager = uds_manager(ctx, hooks_answering(ctx, b"\xaa\xbb"))
    ctx.settings.set(seedkey.DLL_KEY, r"C:\Windows\System32\kernel32.dll")
    assert manager._security_algo(1, b"\x01\x02", None) == b"\xaa\xbb", "the DLL was asked first"


def test_uds_falls_back_to_the_dll_when_the_hook_returns_none(app, ctx, monkeypatch):
    manager = uds_manager(ctx, hooks_answering(ctx, None))
    ctx.settings.set(seedkey.DLL_KEY, "whatever.dll")
    asked = []
    monkeypatch.setattr(
        seedkey,
        "key_for",
        lambda dll, level, seed, other="", protocol=seedkey.XCP: asked.append(
            (dll, level, seed, protocol)
        ),
    )
    manager._security_algo(3, b"\x09", None)
    assert asked == [("whatever.dll", 3, b"\x09", seedkey.UDS)], "not what the DLL was told"


def test_uds_with_no_hook_and_no_dll_says_both_ways_out(app, ctx):
    manager = uds_manager(ctx, hooks_answering(ctx, None))
    with pytest.raises(Exception) as raised:
        manager._security_algo(1, b"\x01", None)
    said = str(raised.value)
    assert "seed and key DLL" in said and "security_key" in said, said


def xcp_manager(ctx, hooks):
    from pycangui.core.bus import BusManager
    from pycangui.core.signals import SignalHub
    from pycangui.xcp.manager import XcpManager

    return XcpManager(BusManager(), hooks, SignalHub(), ctx)


def test_xcp_uses_the_hook_when_it_answers(app, ctx):
    manager = xcp_manager(ctx, hooks_answering(ctx, b"\x11"))
    ctx.settings.set(seedkey.DLL_KEY, r"C:\Windows\System32\kernel32.dll")
    assert manager._key_for(0x01, b"\x01") == b"\x11"
    manager.shutdown()


def test_xcp_falls_back_to_the_same_dll_as_uds(app, ctx, monkeypatch):
    """One ECU ships one algorithm, so naming it twice would be pycangui's
    filing system leaking out."""
    manager = xcp_manager(ctx, hooks_answering(ctx, None))
    ctx.settings.set(seedkey.DLL_KEY, "shared.dll")
    asked = []
    monkeypatch.setattr(
        seedkey,
        "key_for",
        lambda dll, res, seed, other="", protocol=seedkey.XCP: asked.append((dll, res, protocol)),
    )
    manager._key_for(0x10, b"\x01")
    assert asked == [("shared.dll", 0x10, seedkey.XCP)], "not what the DLL was told"
    manager.shutdown()


def test_the_ccp_engine_asks_the_dll_as_ccp(app, ctx, monkeypatch):
    manager = xcp_manager(ctx, hooks_answering(ctx, None))
    manager.set_component("ccp-builtin")
    ctx.settings.set(seedkey.DLL_KEY, "shared.dll")
    asked = []
    monkeypatch.setattr(
        seedkey,
        "key_for",
        lambda dll, res, seed, other="", protocol=seedkey.XCP: asked.append(protocol),
    )
    manager._key_for(0x02, b"\x01")
    assert asked == [seedkey.CCP]
    manager.shutdown()


def test_xcp_with_no_hook_and_no_dll_says_both_ways_out(app, ctx):
    manager = xcp_manager(ctx, hooks_answering(ctx, None))
    with pytest.raises(seedkey.SeedKeyError) as raised:
        manager._key_for(0x01, b"\x01")
    said = str(raised.value)
    assert "seed and key DLL" in said and "compute_key" in said, said
    manager.shutdown()


# --- the pane that chooses it --------------------------------------------------------------
@needs_windows
def test_the_dialog_says_a_matching_dll_loads_directly(app, ctx):
    from pycangui.ui.seedkey_view import SeedKeyDialog

    dialog = SeedKeyDialog(ctx)
    dialog.dll.setText(str(X64 if seedkey.host_bits() == 64 else X86))
    assert "loads directly" in dialog.verdict.text()


@needs_windows
def test_the_dialog_says_what_will_happen_to_a_32_bit_dll(app, ctx):
    """Said where the DLL is chosen, not discovered while unlocking an ECU."""
    from pycangui.ui.seedkey_view import SeedKeyDialog

    dialog = SeedKeyDialog(ctx)
    dialog.dll.setText(str(X86 if seedkey.host_bits() == 64 else X64))
    said = dialog.verdict.text()
    assert "cannot be loaded here" in said
    # Either it found an interpreter, or it says how to get one.
    assert "run in" in said or "Install one" in said, said


@needs_windows
def test_naming_an_interpreter_changes_the_answer(app, ctx):
    from pycangui.ui.seedkey_view import SeedKeyDialog

    dialog = SeedKeyDialog(ctx)
    dialog.dll.setText(str(X86 if seedkey.host_bits() == 64 else X64))
    dialog.python.setText(r"C:\somewhere\python.exe")
    assert "somewhere" in dialog.verdict.text()


def test_the_dialog_starts_from_nothing_and_says_so(app, ctx):
    from pycangui.ui.seedkey_view import NOTHING, SeedKeyDialog

    assert SeedKeyDialog(ctx).verdict.text() == NOTHING


def test_a_file_that_is_not_a_dll_is_called_out(app, ctx, tmp_path):
    from pycangui.ui.seedkey_view import NOT_A_DLL, SeedKeyDialog

    plain = tmp_path / "notes.txt"
    plain.write_text("nope", encoding="utf-8")
    dialog = SeedKeyDialog(ctx)
    dialog.dll.setText(str(plain))
    assert dialog.verdict.text() == NOT_A_DLL


def test_what_is_chosen_is_kept_in_the_workspace(app, ctx):
    from pycangui.ui.seedkey_view import SeedKeyDialog

    dialog = SeedKeyDialog(ctx)
    dialog.dll.setText(r"C:\keys\ecu.dll")
    dialog.python.setText(r"C:\py32\python.exe")
    dialog.save()
    assert ctx.settings.get(seedkey.DLL_KEY) == r"C:\keys\ecu.dll"
    assert ctx.settings.get(seedkey.PYTHON_KEY) == r"C:\py32\python.exe"

    assert SeedKeyDialog(ctx).dll.text() == r"C:\keys\ecu.dll", "not read back"


def test_both_panes_offer_the_same_dialog(app, ctx):
    """One mechanism, and one file for a given ECU."""
    from pycangui.ui import uds_view, xcp_view

    assert "seed and key DLL" in uds_view.SEED_KEY_TIP
    assert "seed and key DLL" in xcp_view.SEED_KEY_TIP
    assert uds_view.seedkey_view is xcp_view.seedkey_view


# --- a DLL only needs the one that answers a seed --------------------------------------
def test_the_compute_function_is_the_one_that_matters():
    """Reported: the check refused a DLL that had no
    XCP_GetAvailablePrivileges. That one only says which levels the DLL is
    willing to unlock, and plenty leave it out."""
    assert seedkey.COMPUTE == "XCP_ComputeKeyFromSeed"
    assert seedkey.PRIVILEGES_OF == "XCP_GetAvailablePrivileges"


@needs_windows
def test_a_dll_with_neither_function_exports_neither():
    ours = X64 if seedkey.host_bits() == 64 else X86
    assert seedkey.exports(ours) == set()
    assert not seedkey.usable(ours)


@needs_windows
def test_a_missing_export_says_which_one_rather_than_blaming_the_dll():
    """The old message claimed a seed and key DLL must export both."""
    ours = X64 if seedkey.host_bits() == 64 else X86
    with pytest.raises(seedkey.SeedKeyError) as raised:
        seedkey.available_privileges(ours)
    said = str(raised.value)
    assert seedkey.PRIVILEGES_OF in said
    assert "must export" not in said, "still says both are required"


@needs_windows
def test_the_dialog_calls_a_dll_without_privileges_usable(app, ctx, monkeypatch):
    """The whole of the reported bug: usable, and it said otherwise."""
    from pycangui.ui.seedkey_view import SeedKeyDialog

    dialog = SeedKeyDialog(ctx)
    dialog.dll.setText(str(X64 if seedkey.host_bits() == 64 else X86))
    monkeypatch.setattr(seedkey, "exports", lambda _p: {seedkey.COMPUTE})
    dialog._test()
    said = dialog.privileges.text()
    assert f"Supported: {seedkey.COMPUTE}" in said, said
    assert f"XCP uses {seedkey.COMPUTE}." in said or f"XCP uses {seedkey.COMPUTE};" in said


@needs_windows
def test_the_dialog_says_which_function_each_protocol_uses(app, ctx, monkeypatch):
    """Reported: a DLL built for UDS, with GenerateKeyEx and the CCP function
    but no XCP one, was called unusable."""
    from pycangui.ui.seedkey_view import SeedKeyDialog

    dialog = SeedKeyDialog(ctx)
    dialog.dll.setText(str(X64 if seedkey.host_bits() == 64 else X86))
    found = {seedkey.GENERATE_KEY_EX, seedkey.CCP_COMPUTE}
    monkeypatch.setattr(seedkey, "exports", lambda _p: found)
    dialog._test()
    said = dialog.privileges.text()
    assert "Supported:" in said, said
    assert f"UDS uses {seedkey.GENERATE_KEY_EX};" in said, "its own, so no mark"
    assert f"XCP uses {seedkey.GENERATE_KEY_EX} (fallback)" in said
    assert f"CCP uses {seedkey.CCP_COMPUTE}." in said


def test_the_dialog_says_which_protocol_a_dll_cannot_serve(app, ctx, monkeypatch):
    from pycangui.ui.seedkey_view import SeedKeyDialog

    dialog = SeedKeyDialog(ctx)
    dialog.dll.setText(str(X64 if seedkey.host_bits() == 64 else X86))
    monkeypatch.setattr(seedkey, "exports", lambda _p: {seedkey.CCP_COMPUTE})
    dialog._test()
    said = dialog.privileges.text()
    assert "UDS" in said.split("Nothing here for")[-1], "CCP's function has no level for UDS"


@needs_windows
def test_the_dialog_still_asks_when_the_dll_will_say(app, ctx, monkeypatch):
    from pycangui.ui.seedkey_view import SeedKeyDialog

    dialog = SeedKeyDialog(ctx)
    dialog.dll.setText(str(X64 if seedkey.host_bits() == 64 else X86))
    monkeypatch.setattr(seedkey, "exports", lambda _p: {seedkey.COMPUTE, seedkey.PRIVILEGES_OF})
    monkeypatch.setattr(seedkey, "available_privileges", lambda _p: 0x11)
    dialog._test()
    assert "CAL/PAG, PGM" in dialog.privileges.text()


@needs_windows
def test_a_dll_with_no_compute_is_called_out(app, ctx, monkeypatch):
    from pycangui.ui.seedkey_view import SeedKeyDialog

    dialog = SeedKeyDialog(ctx)
    dialog.dll.setText(str(X64 if seedkey.host_bits() == 64 else X86))
    monkeypatch.setattr(seedkey, "exports", lambda _p: {seedkey.PRIVILEGES_OF})
    dialog._test()
    assert "cannot answer a seed" in dialog.privileges.text()


# --- the functions a DLL may export, called through stand-ins ----------------------------
U8P = ctypes.POINTER(ctypes.c_ubyte)
GENERATE_KEY_EX = ctypes.CFUNCTYPE(
    ctypes.c_uint32,
    U8P,
    ctypes.c_uint32,
    ctypes.c_uint32,
    ctypes.c_char_p,
    U8P,
    ctypes.c_uint32,
    ctypes.POINTER(ctypes.c_uint32),
)
CCP_COMPUTE = ctypes.CFUNCTYPE(
    ctypes.c_bool,
    ctypes.POINTER(ctypes.c_char),
    ctypes.c_uint16,
    ctypes.POINTER(ctypes.c_char),
    ctypes.c_uint16,
    ctypes.POINTER(ctypes.c_uint16),
)
XCP_COMPUTE = ctypes.CFUNCTYPE(ctypes.c_uint32, ctypes.c_ubyte, ctypes.c_ubyte, U8P, U8P, U8P)


class StandIn:
    """A DLL made of Python functions, recording what each was given."""

    def __init__(self, key=b"\xaa\xbb\xcc", result=None):
        self.key = key
        self.result = result
        self.seen: dict = {}
        self.functions: dict = {}

    def generate_key_ex(self):
        def call(seed, seed_size, level, variant, out, room, size):
            self.seen.update(seed=bytes(seed[:seed_size]), level=level, variant=variant, room=room)
            for i, byte in enumerate(self.key):
                out[i] = byte
            size[0] = len(self.key)
            return 0 if self.result is None else self.result

        self.functions[seedkey.GENERATE_KEY_EX] = GENERATE_KEY_EX(call)
        return self

    def ccp(self):
        def call(seed, seed_size, out, room, size):
            self.seen.update(seed=bytes(seed[:seed_size]), room=room)
            for i, byte in enumerate(self.key):
                out[i] = bytes([byte])
            size[0] = len(self.key)
            return True if self.result is None else self.result

        self.functions[seedkey.CCP_COMPUTE] = CCP_COMPUTE(call)
        return self

    def xcp(self):
        def call(privilege, seed_size, seed, size, out):
            self.seen.update(privilege=privilege, seed=bytes(seed[:seed_size]))
            for i, byte in enumerate(self.key):
                out[i] = byte
            size[0] = len(self.key)
            return seedkey.ACK

        self.functions[seedkey.COMPUTE] = XCP_COMPUTE(call)
        return self

    def install(self, monkeypatch):
        handle = SimpleNamespace(**self.functions)
        monkeypatch.setattr(seedkey, "_open", lambda _path: (handle, SimpleNamespace()))
        return self


def test_uds_uses_generatekeyex_with_the_level_number(monkeypatch):
    """Level 2 is sub-functions 0x03 and 0x04, so the DLL is told 2."""
    dll = StandIn().generate_key_ex().xcp().install(monkeypatch)
    key = seedkey.compute_key("any.dll", 0x03, b"\x11\x22", seedkey.UDS)
    assert key == b"\xaa\xbb\xcc", "exactly as long as the DLL said"
    assert dll.seen["level"] == 2
    assert dll.seen["seed"] == b"\x11\x22"
    assert dll.seen["variant"] == b"", "no variant"
    assert dll.seen["room"] == seedkey.KEY_ROOM


@pytest.mark.parametrize(("sub_function", "level"), [(1, 1), (2, 1), (3, 2), (4, 2), (0x41, 33)])
def test_a_sub_function_belongs_to_a_level(sub_function, level):
    assert seedkey.uds_level(sub_function) == level


def test_a_level_the_dll_refuses_says_which_level(monkeypatch):
    StandIn(result=2).generate_key_ex().install(monkeypatch)
    with pytest.raises(seedkey.SeedKeyError) as raised:
        seedkey.compute_key("any.dll", 0x05, b"\x01", seedkey.UDS)
    said = str(raised.value)
    assert "security level" in said and "3" in said, said


def test_uds_falls_back_to_the_xcp_function_as_before(monkeypatch):
    """Which is still given the sub-function, as it always was."""
    dll = StandIn().xcp().install(monkeypatch)
    assert seedkey.compute_key("any.dll", 0x03, b"\x01", seedkey.UDS) == b"\xaa\xbb\xcc"
    assert dll.seen["privilege"] == 0x03


def test_ccp_uses_its_own_function(monkeypatch):
    dll = StandIn(key=b"\x10\x20").ccp().xcp().install(monkeypatch)
    assert seedkey.compute_key("any.dll", 0x02, b"\x99\x88", seedkey.CCP) == b"\x10\x20"
    assert dll.seen["seed"] == b"\x99\x88"
    assert "privilege" not in dll.seen, "the XCP function was not asked as well"


def test_a_ccp_function_that_says_no_is_an_error(monkeypatch):
    StandIn(result=False).ccp().install(monkeypatch)
    with pytest.raises(seedkey.SeedKeyError):
        seedkey.compute_key("any.dll", 0x02, b"\x01", seedkey.CCP)


def test_xcp_falls_back_to_generatekeyex_with_no_level(monkeypatch):
    """XCP has resources, not levels, so it gives none: 0."""
    dll = StandIn().generate_key_ex().install(monkeypatch)
    assert seedkey.compute_key("any.dll", 0x10, b"\x01", seedkey.XCP) == b"\xaa\xbb\xcc"
    assert dll.seen["level"] == 0, "not the resource, and not a UDS level made from it"


def test_xcp_still_prefers_its_own_function(monkeypatch):
    dll = StandIn().xcp().generate_key_ex().install(monkeypatch)
    seedkey.compute_key("any.dll", 0x01, b"\x01", seedkey.XCP)
    assert "privilege" in dll.seen and "level" not in dll.seen


def test_ccp_falls_back_through_xcp_to_generatekeyex(monkeypatch):
    dll = StandIn().generate_key_ex().install(monkeypatch)
    seedkey.compute_key("any.dll", 0x02, b"\x01", seedkey.CCP)
    assert dll.seen["level"] == 0, "no level, as for XCP"


def test_each_protocol_prefers_its_own_function_then_the_others():
    everything = {seedkey.GENERATE_KEY_EX, seedkey.CCP_COMPUTE, seedkey.COMPUTE}
    assert seedkey.used_by(everything, seedkey.UDS) == seedkey.GENERATE_KEY_EX
    assert seedkey.used_by(everything, seedkey.CCP) == seedkey.CCP_COMPUTE
    assert seedkey.used_by(everything, seedkey.XCP) == seedkey.COMPUTE
    only_xcp = {seedkey.COMPUTE}
    assert all(seedkey.used_by(only_xcp, p) == seedkey.COMPUTE for p in seedkey.PREFERRED)
    only_uds = {seedkey.GENERATE_KEY_EX}
    assert all(seedkey.used_by(only_uds, p) == seedkey.GENERATE_KEY_EX for p in seedkey.PREFERRED)
    assert seedkey.used_by({seedkey.CCP_COMPUTE}, seedkey.UDS) is None, "no level to give it"


def test_the_script_takes_the_protocol_too(capsys):
    """The bridge to another interpreter passes it on, so a 32-bit DLL is
    asked the same way as one loaded here."""
    assert seedkey.main(["no-such.dll", "3", "0102", "uds"]) == 1, "a missing file"
    assert seedkey.main(["no-such.dll", "3", "0102", "kwp"]) == 2, "not a protocol it knows"


# --- reading the export table instead of loading the DLL ----------------------------------
@needs_windows
@pytest.mark.parametrize("dll", [X64, X86])
def test_the_exports_are_read_from_either_bitness(dll):
    """Without loading: 64-bit pycangui cannot load a 32-bit DLL, and that is
    the one somebody most wants to check."""
    names = seedkey.pe_exports(dll)
    assert "CreateFileW" in names and len(names) > 1000


def test_a_file_that_is_not_a_dll_has_no_exports(tmp_path):
    (tmp_path / "text.dll").write_text("not a DLL", encoding="utf-8")
    assert seedkey.pe_exports(tmp_path / "text.dll") == []
    assert seedkey.pe_exports(tmp_path / "absent.dll") == []


@pytest.mark.parametrize(
    ("exported", "plain"),
    [
        ("_GenerateKeyEx@28", "GenerateKeyEx"),
        ("_XCP_ComputeKeyFromSeed@20", "XCP_ComputeKeyFromSeed"),
        ("GenerateKeyEx", "GenerateKeyEx"),
        ("ASAP1A_CCP_ComputeKeyFromSeed", "ASAP1A_CCP_ComputeKeyFromSeed"),
    ],
)
def test_a_decorated_name_is_the_function_it_names(exported, plain):
    """A 32-bit stdcall build exports _Name@bytes unless it has a .def file."""
    assert seedkey.plain_name(exported) == plain


def test_a_decorated_export_is_called_by_the_name_it_has(monkeypatch):
    """Found by its plain name, called by its real one: looking it up by the
    plain name alone called such a DLL unusable."""
    dll = StandIn().generate_key_ex()
    handle = SimpleNamespace()
    setattr(handle, "_GenerateKeyEx@28", dll.functions[seedkey.GENERATE_KEY_EX])
    monkeypatch.setattr(seedkey, "_open", lambda _path: (SimpleNamespace(), handle))
    monkeypatch.setattr(
        seedkey, "export_names", lambda _path: {seedkey.GENERATE_KEY_EX: "_GenerateKeyEx@28"}
    )
    assert seedkey.compute_key("any.dll", 0x01, b"\x01", seedkey.UDS) == b"\xaa\xbb\xcc"


@needs_windows
def test_a_dll_of_the_other_bitness_is_still_checked(app, ctx):
    """It used to stop at "cannot be loaded here" and say nothing about what
    the DLL contains. kernel32 has no seed and key functions, and it says so."""
    from pycangui.ui.seedkey_view import SeedKeyDialog

    dialog = SeedKeyDialog(ctx)
    dialog.dll.setText(str(X86 if seedkey.host_bits() == 64 else X64))
    dialog._test()
    said = dialog.privileges.text()
    assert "none of" in said and "loaded" not in said, said
