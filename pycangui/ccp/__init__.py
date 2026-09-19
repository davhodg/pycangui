# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""CCP: the calibration protocol XCP replaced, and plenty of ECUs still speak.

Same job as XCP -- read and write a controller's memory by address, with the
A2L saying which address a named measurement lives at -- and near enough the
same shape, which is why both are driven from one pane. The differences are
worth knowing before reading the engine:

* **A station address.** CCP connects to a 16-bit station address, so several
  controllers can share one pair of CAN identifiers and be addressed in turn.
  XCP has nothing like it: the identifiers *are* the addressing.
* **A message counter.** Every command carries one and the answer echoes it,
  which is how a master tells this answer from the last one. Not every slave
  is careful about it.
* **Big-endian addresses.** CCP is a Motorola-order protocol: addresses in
  SET_MTA go out most significant byte first, where XCP follows whatever the
  slave declares. The station address in CONNECT is the exception, and is
  little-endian by the standard.
* **One return code for everything.** A CCP answer begins 0xFF, then a return
  code, then the counter; XCP has a positive response byte and a separate
  error packet.

The command codes and return codes below are from the ASAM CCP 2.1 standard.
"""

from __future__ import annotations

#: Commands, from the standard. Only the ones pycangui sends are named.
CMD_CONNECT = 0x01
CMD_SET_MTA = 0x02
CMD_DNLOAD = 0x03
CMD_UPLOAD = 0x04
CMD_GET_CCP_VERSION = 0x1B
CMD_EXCHANGE_ID = 0x17
CMD_GET_SEED = 0x12
CMD_UNLOCK = 0x13
CMD_SHORT_UP = 0x0F
CMD_DISCONNECT = 0x07

#: Every answer starts with this: a command return message.
PID_RETURN = 0xFF
#: A return code of zero is "did it"; anything else is a complaint.
ACKNOWLEDGE = 0x00

#: What the counter wraps at: one byte, and it is only there to pair an
#: answer with its command.
COUNTER_WRAP = 0x100

#: The most bytes one DNLOAD or SHORT_UP can carry. The frame holds eight:
#: command, counter, and then five for a short upload's size and address, or
#: for a download's size and data.
MOST_PER_FRAME = 5

#: The version pycangui asks for, and the one it is written against.
VERSION = (2, 1)

#: Resources, as the seed and key exchange names them. Calibration is bit 0,
#: which happens to be the same bit XCP uses, so the pane's Unlock button
#: means the same thing either way.
RESOURCE_CAL = 0x01
RESOURCE_DAQ = 0x02
RESOURCE_PGM = 0x40

#: What a slave says when it will not do something. The standard's list is
#: longer; these are the ones worth reading out in full, and anything else
#: is shown as its number.
RETURN_CODES = {
    0x00: "acknowledge",
    0x01: "DAQ processor overload",
    0x10: "command processor busy",
    0x11: "DAQ processor busy",
    0x12: "internal timeout",
    0x18: "key request",
    0x19: "session status request",
    0x20: "cold start request",
    0x21: "cal data initialisation request",
    0x22: "DAQ list initialisation request",
    0x23: "code update request",
    0x30: "unknown command",
    0x31: "command syntax",
    0x32: "parameter out of range",
    0x33: "access denied",
    0x34: "overload",
    0x35: "access locked",
    0x36: "resource or function not available",
}
