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


def _call(cdecl, stdcall, name: str, *args) -> int:
    """Call the export, trying cdecl and then stdcall.

    Getting the convention wrong leaves the stack unbalanced, which ctypes
    notices and raises for, so the wrong guess is loud rather than a wrong
    key. cdecl is tried first because that is what the template produces.
    """
    last: Exception | None = None
    for handle in (cdecl, stdcall):
        try:
            function = getattr(handle, name)
        except AttributeError as exc:
            last = exc
            continue
        function.restype = ctypes.c_uint32
        try:
            return int(function(*args))
        except ValueError as exc:  # ctypes: the stack did not balance
            last = exc
    raise SeedKeyError(
        f"{name} could not be called: {last}. The DLL may not be a seed and "
        "key DLL, which must export XCP_GetAvailablePrivileges and "
        "XCP_ComputeKeyFromSeed."
    )


def available_privileges(path: Path | str) -> int:
    """The resources this DLL says it can unlock, as XCP resource bits."""
    cdecl, stdcall = _open(path)
    out = ctypes.c_ubyte(0)
    result = _call(cdecl, stdcall, "XCP_GetAvailablePrivileges", ctypes.byref(out))
    if result != ACK:
        raise SeedKeyError(RETURNS.get(result, f"the DLL returned {result}"))
    return int(out.value)


def compute_key(path: Path | str, privilege: int, seed: bytes) -> bytes:
    """Ask the DLL for the key to this seed, for one resource."""
    cdecl, stdcall = _open(path)
    buffer = (ctypes.c_ubyte * KEY_ROOM)()
    length = ctypes.c_ubyte(KEY_ROOM)
    result = _call(
        cdecl,
        stdcall,
        "XCP_ComputeKeyFromSeed",
        ctypes.c_ubyte(privilege),
        ctypes.c_ubyte(len(seed)),
        (ctypes.c_ubyte * len(seed))(*seed) if seed else None,
        ctypes.byref(length),
        buffer,
    )
    if result != ACK:
        raise SeedKeyError(RETURNS.get(result, f"the DLL returned {result}"))
    return bytes(buffer[: length.value])


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


def key_for(path: Path | str, privilege: int, seed: bytes, other_python: str = "") -> bytes:
    """The key, from the DLL here or through an interpreter that can load it.

    The bitness is read from the file rather than discovered by failing to
    load it, so the choice is made before anything goes wrong and the
    message when there is no way through says which way it needed to go.
    """
    if not needs_another_python(path):
        return compute_key(path, privilege, seed)
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
    return compute_key_elsewhere(other_python, path, privilege, seed)


# --- running the DLL somewhere else ----------------------------------------------------
def compute_key_elsewhere(
    python: Path | str, path: Path | str, privilege: int, seed: bytes, timeout: float = 20.0
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
    """<dll> <privilege> <seed as hex>, printing the key as hex."""
    if len(argv) != 3:
        print(f"usage: {Path(__file__).name} <dll> <privilege> <seed as hex>", file=sys.stderr)
        return 2
    try:
        key = compute_key(argv[0], int(argv[1], 0), bytes.fromhex(argv[2]))
    except (SeedKeyError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(key.hex())
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
