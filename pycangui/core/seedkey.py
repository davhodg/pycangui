# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""The standard seed and key DLL, which is how an unlock algorithm ships.

XCP leaves the algorithm to the maker and says only how to carry it: the
slave sends a seed, the tool sends back a key. The settled answer to "where
does the tool get the algorithm" is a Windows DLL exporting two functions:

    XCP_GetAvailablePrivileges(BYTE *privilege)
    XCP_ComputeKeyFromSeed(BYTE privilege, BYTE lenSeed, BYTE *seed,
                           BYTE *lenKey, BYTE *key)

Every measurement tool loads one, so a maker who has written a seed and key
DLL for another tool has already written the one pycangui needs.

UDS SecurityAccess asks the same question -- here is a seed, what is the key
-- so it uses the same file, with the security level where XCP puts the
resource. That is why this lives in core rather than under xcp: one ECU
ships one algorithm, and having to name the same DLL twice would be
pycangui's filing system leaking into somebody's afternoon.

    XCP_GetAvailablePrivileges(BYTE *privilege)
    XCP_ComputeKeyFromSeed(BYTE privilege, BYTE lenSeed, BYTE *seed,
                           BYTE *lenKey, BYTE *key)

``lenKey`` goes in as the room available and comes back as the length used.

Two other interfaces are common, and a DLL may export them instead of, or
as well as, the XCP one:

    GenerateKeyEx(const uint8 *seed, uint32 seedSize, uint32 level,
                  const char *variant, uint8 *key, uint32 keyRoom,
                  uint32 *keySize)

which is how UDS algorithms are usually delivered -- it returns 0 for
success -- and, from the CCP specification,

    ASAP1A_CCP_ComputeKeyFromSeed(char *seed, uint16 seedSize, char *key,
                                  uint16 keyRoom, uint16 *keySize)

returning true for success, with no level at all. Each protocol prefers
its own and falls back to the others: UDS to the XCP function, XCP to
GenerateKeyEx, CCP to the XCP function and then GenerateKeyEx. For UDS,
GenerateKeyEx is given the level number -- 1 for sub-functions 0x01/0x02,
2 for 0x03/0x04; for XCP and CCP, which have no levels, 0. The variant is
always empty.

**This module imports nothing but the standard library, on purpose.** A
32-bit DLL cannot be loaded into a 64-bit process -- there is no flag for
it, the two cannot share an address space -- and these DLLs are generally
built 32-bit, so the one somebody hands you usually cannot be loaded into
64-bit pycangui. The way round it is to run the DLL in a 32-bit interpreter and
pass the answer back, and the thing that has to run there is this file. It
is therefore runnable as a script by any Python, with no pycangui on its
path:

    python seedkey.py <dll> <privilege> <seed as hex>

printing the key as hex, or a reason on stderr and a non-zero exit.
"""

from __future__ import annotations

import ctypes
import re
import struct
import subprocess
import sys
from pathlib import Path

#: Room offered for the key. The DLL is told this and writes back what it
#: used. A seed is a handful of bytes and a key is normally the same, so
#: this is far larger than anything real and costs nothing.
KEY_ROOM = 255

#: What the two functions return.
ACK = 0
ERR_PRIVILEGE_NOT_AVAILABLE = 1
ERR_INVALID_SEED_LENGTH = 2
ERR_INSUFFICIENT_KEY_LENGTH = 3

RETURNS = {
    ERR_PRIVILEGE_NOT_AVAILABLE: "this DLL cannot unlock that resource",
    ERR_INVALID_SEED_LENGTH: "the DLL rejected the seed length",
    ERR_INSUFFICIENT_KEY_LENGTH: f"the key needs more than {KEY_ROOM} bytes",
}

#: XCP's resource bits, which the DLL calls privileges. The same numbers.
PRIVILEGES = {
    0x01: "CAL/PAG",
    0x04: "DAQ",
    0x08: "STIM",
    0x10: "PGM",
    0x20: "DBG",
}

#: PE machine types, from the COFF header.
_MACHINES = {0x014C: "32-bit", 0x8664: "64-bit", 0xAA64: "64-bit (ARM)"}


class SeedKeyError(Exception):
    """Anything that stopped a key being computed, said in words."""


def machine(path: Path | str) -> str:
    """ "32-bit", "64-bit" or "" for a file that is not a PE at all.

    Read rather than inferred from a failed load. Windows reports the wrong
    bitness as error 193, "not a valid Win32 application", which is true
    and tells nobody what to do about it.
    """
    try:
        with open(path, "rb") as f:
            if f.read(2) != b"MZ":
                return ""
            f.seek(0x3C)
            (offset,) = struct.unpack("<I", f.read(4))
            f.seek(offset)
            if f.read(4) != b"PE\0\0":
                return ""
            (kind,) = struct.unpack("<H", f.read(2))
    except (OSError, struct.error):
        return ""
    return _MACHINES.get(kind, "")


def host_bits() -> int:
    return struct.calcsize("P") * 8


def _open(path: Path | str):
    """The DLL, with whichever calling convention it was built with.

    The usual template declares the exports with ``__declspec(dllexport)``
    on MSVC, which leaves the convention at the compiler's default of
    cdecl, and ``_stdcall`` on Borland. Both are in the wild. Loading costs nothing
    and does not commit to either; the call does, so this returns both
    handles and the caller tries them in turn.
    """
    if not Path(path).is_file():
        raise SeedKeyError(f"No such file: {path}")
    kind = machine(path)
    if kind and not kind.startswith(str(host_bits())):
        raise SeedKeyError(
            f"{Path(path).name} is a {kind} DLL and this is {host_bits()}-bit Python. "
            "A DLL can only be loaded into a process of its own bitness."
        )
    try:
        return ctypes.CDLL(str(path)), ctypes.WinDLL(str(path))
    except OSError as exc:
        raise SeedKeyError(f"{Path(path).name} would not load: {exc}") from exc
    except AttributeError as exc:  # WinDLL is Windows only
        raise SeedKeyError("A seed and key DLL can only be used on Windows") from exc


#: The one that does the work. A DLL without it cannot answer a seed.
COMPUTE = "XCP_ComputeKeyFromSeed"

#: Which resources the DLL will unlock. Optional: plenty of DLLs implement
#: only the key computation, and a caller that already knows which level it
#: wants never needs to ask. Insisting on it turned a usable DLL into a
#: refused one.
PRIVILEGES_OF = "XCP_GetAvailablePrivileges"


#: How UDS algorithms are usually delivered: level and variant, and the key's
#: length written back.
GENERATE_KEY_EX = "GenerateKeyEx"
#: The function the CCP specification gives the DLL. No level.
CCP_COMPUTE = "ASAP1A_CCP_ComputeKeyFromSeed"

UDS, XCP, CCP = "uds", "xcp", "ccp"
#: The function each protocol asks for, best first: its own, then the others.
#: A DLL built for one protocol often serves another through the same
#: function, and one that cannot says so -- a level it does not know is an
#: error from the DLL, and nothing is sent.
PREFERRED = {
    UDS: (GENERATE_KEY_EX, COMPUTE),
    XCP: (COMPUTE, GENERATE_KEY_EX),
    CCP: (CCP_COMPUTE, COMPUTE, GENERATE_KEY_EX),
}
#: Every function this module knows how to call.
KNOWN = (COMPUTE, PRIVILEGES_OF, GENERATE_KEY_EX, CCP_COMPUTE)
#: What each function computes a key with, in the order they are offered.
KEY_FUNCTIONS = (GENERATE_KEY_EX, CCP_COMPUTE, COMPUTE)

#: What GenerateKeyEx returns, other than 0 for success.
GENERATE_RETURNS = {
    1: f"the key needs more than {KEY_ROOM} bytes",
    2: "the DLL says the security level is not valid",
    3: "the DLL says the variant is not valid",
    4: "the DLL could not compute a key",
}


#: Seed and key functions pycangui recognises but does not call, so that the
#: check can say they are there rather than leave them unmentioned.
NOT_USED = ("GenerateKeyExOpt", "GenerateKey", "KWP2000_ComputeKeyFromSeed")

#: A 32-bit stdcall export is decorated -- _GenerateKeyEx@28 -- unless the DLL
#: was built with a .def file. The plain name is what is being looked for.
_DECORATED = re.compile(r"^_?([A-Za-z]\w*?)(@\d+)?$")


def pe_exports(path: Path | str) -> list[str]:
    """Every name a DLL exports, read from its export table without loading it.

    Read rather than loaded so that it works whatever the DLL's bitness: a
    32-bit DLL cannot be loaded into 64-bit pycangui, and it is exactly the
    DLL somebody most wants to check. Empty for anything that is not a DLL
    with an export table.
    """
    try:
        data = Path(path).read_bytes()
    except OSError:
        return []
    try:
        if data[:2] != b"MZ":
            return []
        (pe,) = struct.unpack_from("<I", data, 0x3C)
        if data[pe : pe + 4] != b"PE\0\0":
            return []
        (sections_count,) = struct.unpack_from("<H", data, pe + 6)
        (optional_size,) = struct.unpack_from("<H", data, pe + 20)
        optional = pe + 24
        (magic,) = struct.unpack_from("<H", data, optional)
        # The data directories follow the rest of the optional header, which
        # is 96 bytes in a 32-bit image and 112 in a 64-bit one.
        directories = optional + (96 if magic == 0x10B else 112)
        (export_rva,) = struct.unpack_from("<I", data, directories)
        if not export_rva:
            return []
        sections = []
        for index in range(sections_count):
            at = optional + optional_size + 40 * index
            virtual_size, address, raw_size, raw_at = struct.unpack_from("<IIII", data, at + 8)
            sections.append((address, max(virtual_size, raw_size), raw_at))

        def offset(rva: int) -> int:
            for address, size, raw_at in sections:
                if address <= rva < address + size:
                    return rva - address + raw_at
            raise ValueError(f"RVA {rva:#x} is in no section")

        table = offset(export_rva)
        (names_count,) = struct.unpack_from("<I", data, table + 24)
        (names_rva,) = struct.unpack_from("<I", data, table + 32)
        names_at = offset(names_rva)
        out = []
        for index in range(names_count):
            (name_rva,) = struct.unpack_from("<I", data, names_at + 4 * index)
            start = offset(name_rva)
            out.append(data[start : data.index(b"\0", start)].decode("ascii", "replace"))
        return out
    except (struct.error, ValueError):  # truncated, or not laid out as a PE should be
        return []


def plain_name(export: str) -> str:
    """_GenerateKeyEx@28 is GenerateKeyEx: the name as it was written."""
    match = _DECORATED.match(export)
    return match.group(1) if match else export


def export_names(path: Path | str) -> dict[str, str]:
    """The seed and key functions a DLL exports: plain name to the name it is
    exported under, which is what has to be asked for when calling it."""
    wanted = set(KNOWN) | set(NOT_USED)
    out: dict[str, str] = {}
    for export in pe_exports(path):
        name = plain_name(export)
        if name in wanted:
            out.setdefault(name, export)
    return out


def _exported(cdecl, stdcall) -> dict[str, str]:
    """What the loaded DLL answers to by plain name, for a file whose export
    table could not be read."""
    return {name: name for name in KNOWN if hasattr(cdecl, name) or hasattr(stdcall, name)}


def exports(path: Path | str) -> set[str]:
    """Which of the functions this module calls the DLL exports, by plain name.

    Read from the file rather than by loading it, so it answers for a DLL of
    either bitness. None of them is required on its own: a DLL needs one that
    computes a key, and which one decides what each protocol can use.
    """
    if not Path(path).is_file():
        raise SeedKeyError(f"No such file: {path}")
    return {name for name in export_names(path) if name in KNOWN}


def not_used(path: Path | str) -> list[str]:
    """Seed and key functions the DLL exports that pycangui does not call."""
    found = export_names(path)
    return [name for name in NOT_USED if name in found]


def is_fallback(name: str, protocol: str) -> bool:
    """Whether a protocol using this function is using another's."""
    return name != PREFERRED[protocol][0]


def used_by(found: set[str], protocol: str) -> str | None:
    """The function a protocol would call, of those found, or None."""
    return next((name for name in PREFERRED[protocol] if name in found), None)


def usable(path: Path | str) -> bool:
    """Whether this DLL can answer a seed at all."""
    return any(name in KEY_FUNCTIONS for name in exports(path))


def uds_level(sub_function: int) -> int:
    """The security level a requestSeed sub-function belongs to: 0x01 and
    0x02 are level 1, 0x03 and 0x04 are level 2."""
    return (sub_function + 1) // 2


def _call(cdecl, stdcall, name: str, *args, restype=ctypes.c_uint32) -> int:
    """Call the export, trying cdecl and then stdcall.

    Getting the convention wrong leaves the stack unbalanced, which ctypes
    notices and raises for, so the wrong guess is loud rather than a wrong
    key. cdecl is tried first because that is what the template produces.
    """
    last: Exception | None = None
    missing = True
    for handle in (cdecl, stdcall):
        try:
            function = getattr(handle, name)
        except AttributeError as exc:
            last = exc
            continue
        missing = False
        function.restype = restype
        try:
            return int(function(*args))
        except ValueError as exc:  # ctypes: the stack did not balance
            last = exc
    if missing:
        raise SeedKeyError(f"This DLL does not export {name}.")
    raise SeedKeyError(f"{name} could not be called: {last}")


def available_privileges(path: Path | str) -> int:
    """The resources this DLL says it can unlock, as XCP resource bits."""
    cdecl, stdcall = _open(path)
    actual = export_names(path).get(PRIVILEGES_OF, PRIVILEGES_OF)
    out = ctypes.c_ubyte(0)
    result = _call(cdecl, stdcall, actual, ctypes.byref(out))
    if result != ACK:
        raise SeedKeyError(RETURNS.get(result, f"the DLL returned {result}"))
    return int(out.value)


def compute_key(path: Path | str, privilege: int, seed: bytes, protocol: str = XCP) -> bytes:
    """Ask the DLL for the key to this seed, through the function this
    protocol prefers of those it exports. ``privilege`` is XCP's resource, or
    for UDS the requestSeed sub-function."""
    cdecl, stdcall = _open(path)
    table = export_names(path) or _exported(cdecl, stdcall)
    name = used_by(set(table), protocol)
    if name is None:
        raise SeedKeyError(
            f"This DLL does not export {' or '.join(PREFERRED[protocol])}, so it "
            "cannot answer a seed."
        )
    if name == GENERATE_KEY_EX:
        # XCP and CCP have resources, not levels, so they give none: 0.
        level = uds_level(privilege) if protocol == UDS else 0
        return _generate_key_ex(cdecl, stdcall, table[name], level, seed)
    if name == CCP_COMPUTE:
        return _ccp_key(cdecl, stdcall, table[name], seed)
    buffer = (ctypes.c_ubyte * KEY_ROOM)()
    length = ctypes.c_ubyte(KEY_ROOM)
    result = _call(
        cdecl,
        stdcall,
        table[name],
        ctypes.c_ubyte(privilege),
        ctypes.c_ubyte(len(seed)),
        (ctypes.c_ubyte * len(seed))(*seed) if seed else None,
        ctypes.byref(length),
        buffer,
    )
    if result != ACK:
        raise SeedKeyError(RETURNS.get(result, f"the DLL returned {result}"))
    return bytes(buffer[: length.value])


def _generate_key_ex(cdecl, stdcall, actual: str, level: int, seed: bytes) -> bytes:
    key = (ctypes.c_ubyte * KEY_ROOM)()
    size = ctypes.c_uint32(0)
    result = _call(
        cdecl,
        stdcall,
        actual,
        (ctypes.c_ubyte * max(len(seed), 1))(*seed),
        ctypes.c_uint32(len(seed)),
        ctypes.c_uint32(level),
        ctypes.c_char_p(b""),  # the variant: none, which most DLLs ignore anyway
        key,
        ctypes.c_uint32(KEY_ROOM),
        ctypes.byref(size),
    )
    if result != 0:
        said = GENERATE_RETURNS.get(result, f"the DLL returned {result}")
        raise SeedKeyError(f"{said} (security level {level})")
    if size.value > KEY_ROOM:
        raise SeedKeyError(f"the DLL says the key is {size.value} bytes, in {KEY_ROOM}")
    return bytes(key[: size.value])


def _ccp_key(cdecl, stdcall, actual: str, seed: bytes) -> bytes:
    key = ctypes.create_string_buffer(KEY_ROOM)
    size = ctypes.c_uint16(0)
    done = _call(
        cdecl,
        stdcall,
        actual,
        ctypes.create_string_buffer(bytes(seed), max(len(seed), 1)),
        ctypes.c_uint16(len(seed)),
        key,
        ctypes.c_uint16(KEY_ROOM),
        ctypes.byref(size),
        restype=ctypes.c_bool,
    )
    if not done:
        raise SeedKeyError("the DLL could not compute a key for this seed")
    if size.value > KEY_ROOM:
        raise SeedKeyError(f"the DLL says the key is {size.value} bytes, in {KEY_ROOM}")
    return key.raw[: size.value]


def names(privileges: int) -> str:
    """ "CAL/PAG, PGM" for a set of resource bits."""
    return ", ".join(name for bit, name in PRIVILEGES.items() if privileges & bit)


#: Where the DLL and the other interpreter are kept in the workspace. Not
#: under xcp or uds: it is one mechanism and, for a given ECU, usually one
#: file, so setting it in either pane sets it for both.
DLL_KEY = "seed_key.dll"
PYTHON_KEY = "seed_key.python"


def needs_another_python(path: Path | str) -> bool:
    """Whether this DLL is the wrong bitness for the running process."""
    kind = machine(path)
    return bool(kind) and not kind.startswith(str(host_bits()))


def _quietly() -> dict:
    """Keep a console window from flashing up on Windows.

    Every one of these runs is a console program started by a window
    program, and the default is to give it a console. Unlocking an ECU
    should not blink a black box at somebody, and neither should typing a
    path into the dialog, which asks an interpreter what it is on every
    keystroke.
    """
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return {"creationflags": flags} if flags else {}


def _ran(order: list[str], timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(
        order, capture_output=True, text=True, timeout=timeout, check=False, **_quietly()
    )


def bits_of(python: Path | str, timeout: float = 10.0) -> int:
    """How many bits an interpreter is, by asking it. 0 if it would not run."""
    try:
        code = "import struct;print(struct.calcsize('P')*8)"
        return int(_ran([str(python), "-c", code], timeout).stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0


def find_python(bits: int) -> str:
    """An interpreter of that bitness on this machine, or "".

    Through the Windows ``py`` launcher, which is the only thing that knows
    what is installed. A 64-bit tool running a 32-bit DLL in a helper
    process is what every measurement tool does, and somebody who has met
    that problem should not also have to go looking for the helper.
    """
    try:
        listed = _ran(["py", "-0p"], 15.0)
    except (OSError, subprocess.SubprocessError):
        return ""
    for line in listed.stdout.splitlines():
        # " -V:3.12-32 *   C:\...\python.exe", and the path may have spaces
        tag, _, rest = line.strip().partition(" ")
        if not tag.startswith("-V:"):
            continue
        where = rest.strip().lstrip("*").strip()
        if not where.lower().endswith("python.exe"):
            continue
        if bits_of(where) == bits:
            return where
    return ""


def key_for(
    path: Path | str, privilege: int, seed: bytes, other_python: str = "", protocol: str = XCP
) -> bytes:
    """The key, from the DLL here or through an interpreter that can load it.

    The bitness is read from the file rather than discovered by failing to
    load it, so the choice is made before anything goes wrong and the
    message when there is no way through says which way it needed to go.
    """
    if not needs_another_python(path):
        return compute_key(path, privilege, seed, protocol)
    wanted = 32 if machine(path).startswith("32") else 64
    if not other_python:
        other_python = find_python(wanted)
    if not other_python:
        raise SeedKeyError(
            f"{Path(path).name} is a {machine(path)} DLL and pycangui is running "
            f"{host_bits()}-bit Python, which cannot load it -- no process can "
            "load a library of the other bitness. Install a "
            f"{wanted}-bit Python and pycangui will find it and run the DLL "
            "there, name one in the XCP pane, or rebuild the DLL for "
            f"{host_bits()}-bit."
        )
    return compute_key_elsewhere(other_python, path, privilege, seed, protocol=protocol)


# --- running the DLL somewhere else ----------------------------------------------------
def compute_key_elsewhere(
    python: Path | str,
    path: Path | str,
    privilege: int,
    seed: bytes,
    timeout: float = 20.0,
    protocol: str = XCP,
) -> bytes:
    """Ask another interpreter to do it, for a DLL of the other bitness.

    The other interpreter runs *this file*, by path, which is why nothing
    here may import pycangui: the 32-bit Python somebody has lying around
    will not have pycangui installed and should not need it.
    """
    order = [
        str(python),
        str(Path(__file__).resolve()),
        str(path),
        str(privilege),
        seed.hex(),
        protocol,
    ]
    try:
        done = _ran(order, timeout)
    except OSError as exc:
        raise SeedKeyError(f"{python} would not run: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise SeedKeyError(f"{python} did not answer within {timeout:.0f} s") from exc
    if done.returncode != 0:
        raise SeedKeyError((done.stderr or "").strip() or f"exit code {done.returncode}")
    try:
        return bytes.fromhex(done.stdout.strip())
    except ValueError as exc:
        raise SeedKeyError(f"unreadable answer: {done.stdout.strip()!r}") from exc


def main(argv: list[str]) -> int:
    """<dll> <privilege> <seed as hex> [uds|xcp|ccp], printing the key as hex."""
    if len(argv) not in (3, 4) or (len(argv) == 4 and argv[3] not in PREFERRED):
        print(
            f"usage: {Path(__file__).name} <dll> <privilege> <seed as hex> [uds|xcp|ccp]",
            file=sys.stderr,
        )
        return 2
    protocol = argv[3] if len(argv) == 4 else XCP
    try:
        key = compute_key(argv[0], int(argv[1], 0), bytes.fromhex(argv[2]), protocol)
    except (SeedKeyError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(key.hex())
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
