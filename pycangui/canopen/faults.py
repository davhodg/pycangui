# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""What a node says about its own faults, beyond the emergency it sent once.

An EMCY is heard only by whoever was listening at the time. Plug in after a
controller has faulted and the tool shows a quiet bus and a healthy-looking
node, which is the wrong answer to the only question being asked. CiA 301
provides for exactly this, in three objects, and the useful thing about them
is that they are *state* rather than an event:

* ``0x1001`` the error register -- one byte of categories, and **mandatory**,
  so every CANopen node has it. Non-zero means the node currently considers
  itself faulted, whatever it did or did not broadcast.
* ``0x1002`` the manufacturer status register -- one word meaning whatever
  the maker says. **Optional.**
* ``0x1003`` the predefined error field -- the recent emergency codes the
  node kept, newest first, with sub 0 the count and writing 0 to sub 0 the
  way to clear them. **Optional**, and the length is the maker's choice.

Two of the three being optional is the constraint this module is written
around: nothing here assumes an object exists, and the caller is given the
difference between "the node says no fault" and "the node has nowhere to
keep one".
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pycangui.canopen.emcy import describe_code, describe_register

ERROR_REGISTER = 0x1001
MANUFACTURER_STATUS = 0x1002
PREDEFINED_ERROR_FIELD = 0x1003

#: Sub 0 of 0x1003: how many entries are held, and the one to write 0 to.
COUNT_SUB = 0
#: What CiA 301 says to write there to empty the list. One byte, since sub 0
#: is an UNSIGNED8.
ZERO = bytes(1)

#: A node may keep a long history and a tool asking for all of it is a tool
#: holding the bus for a while, one SDO per entry. CiA 301 sets no maximum;
#: a device keeping more than this has more than anybody reads at once.
MOST_ENTRIES = 32

#: What 0x1003 holds per entry: the emergency code in the low word, and
#: whatever the maker puts in the high word.
CODE_MASK = 0xFFFF
INFO_SHIFT = 16

NOT_SUPPORTED = "this node has no {name} (0x{index:04X})"
NO_FAULT = "no error"


@dataclass(frozen=True)
class StoredError:
    """One entry of a node's fault list, however it was read.

    ``text`` is for a device whose codes are its own rather than CiA 301's:
    a hook that reads the list off a manufacturer object knows what each
    code means and can say so, where ``describe_code`` would answer with
    the wrong standard's name or with nothing at all.
    """

    code: int
    info: int = 0  #: the high word: manufacturer-specific, often a sub-code
    text: str = ""  #: what the maker calls it, if the standard has no name

    @property
    def description(self) -> str:
        return self.text or describe_code(self.code)

    @property
    def is_reset(self) -> bool:
        return self.code == 0x0000

    def __str__(self) -> str:
        text = f"0x{self.code:04X} {self.description}"
        if self.info:
            text += f" (manufacturer 0x{self.info:04X})"
        return text


def unpack(value: int) -> StoredError:
    """One 32-bit entry of 0x1003 split into the two halves CiA 301 gives it."""
    return StoredError(code=value & CODE_MASK, info=(value >> INFO_SHIFT) & CODE_MASK)


def as_errors(values) -> list[StoredError]:
    """Whatever a hook returned, as entries.

    A hook that has read a device's own fault list should not have to know
    this module to answer: a list of plain numbers is the obvious thing to
    return and is read the way 0x1003 is read. Anything already a
    StoredError is passed through, so a hook that wants to name its codes
    can.
    """
    return [value if isinstance(value, StoredError) else unpack(int(value)) for value in values]


@dataclass
class FaultState:
    """What one node currently says about itself.

    ``None`` and *absent* are different answers and are kept apart: a node
    with no 0x1002 has ``manufacturer_status`` None and 0x1002 in
    ``missing``, and a node that has one reading zero has 0 and nothing in
    ``missing``. Saying "0" for both would be inventing a reading.
    """

    node_id: int
    register: int | None = None
    manufacturer_status: int | None = None
    stored: list[StoredError] = field(default_factory=list)
    #: What is wrong *now*, where a hook has said so. None means nobody
    #: has: the error register is then the only answer available. An empty
    #: list is an answer -- this device has nothing active -- which is why
    #: it is not the default.
    #:
    #: CiA 301 has no object for this. 0x1001 gives categories rather than
    #: faults, and 0x1002 is one word a maker may use for anything or not
    #: implement at all, so a device that can list what is currently wrong
    #: does it its own way and a hook is the only place that fits.
    active: list[StoredError] | None = None
    #: Indices the node refused or that its EDS does not list.
    missing: set[int] = field(default_factory=set)

    @property
    def faulted(self) -> bool:
        """Whether the node says it is in error *now*.

        A hook listing what is active answers this where there is one,
        since it is the better answer: the error register says a category
        is set, and a list says which fault it is. Failing that, the
        register.

        A stored error is history either way: a node that faulted this
        morning and recovered still has the entry, and calling that a fault
        would be reporting the past as the present.
        """
        if self.active is not None:
            return bool(self.active)
        return bool(self.register)

    @property
    def active_text(self) -> str:
        """What is wrong now, in a few words, for a column in a list."""
        if self.active:
            return ", ".join(error.description for error in self.active)
        return self.register_text if self.faulted else ""

    @property
    def register_text(self) -> str:
        if self.register is None:
            return ""
        return describe_register(self.register)

    def says(self, index: int) -> bool:
        """Whether this node answered for an object at all."""
        return index not in self.missing


def missing_text(index: int) -> str:
    """Why a control is switched off, in words rather than as an abort code."""
    names = {
        ERROR_REGISTER: "error register",
        MANUFACTURER_STATUS: "manufacturer status register",
        PREDEFINED_ERROR_FIELD: "stored error list",
    }
    return NOT_SUPPORTED.format(name=names.get(index, "object"), index=index)
