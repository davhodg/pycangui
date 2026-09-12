# SPDX-License-Identifier: MIT-0
#
# A starting point, copied into your workspace for you to change.  It is
# yours to edit, keep private or give away: pycangui claims nothing in it
# and asks for no credit, so what you write here needs nobody's permission.
"""A J1939 engine: claims an address, broadcasts, and answers requests.

Where ``canopen_device.py`` takes a whole protocol server from
``node.canopen()``, this one builds its own from the ``j1939`` library --
which is the more usual shape.  pycangui has a server to hand you for
CANopen and not for anything else, so most node files look like this: bring
the stack you need, wire it to the channel, and get on with what your device
does.

A J1939 device cannot simply start shouting.  It claims a source address
first and defends it, and until that succeeds nothing it sends means
anything -- which is why ``poll`` checks before it broadcasts.

It sends EEC1 (engine speed), CCVS1 (wheel speed) and a DM1 fault, and
answers a request for ComponentID with a multi-packet BAM transfer -- the
one message here that does not fit in eight bytes, and the reason the
library is worth having.

To make it yours: change ``ADDRESS``, ``MANUFACTURER`` and ``IDENTITY`` to
your ECU's, and broadcast the parameter groups it broadcasts.
"""

from __future__ import annotations

import math
import struct

NAME = "J1939 engine"
DESCRIPTION = "Claims an address, broadcasts engine and wheel speed, reports a fault."
RATE_HZ = 10

#: The address this ECU claims.  0x00 is the engine's by convention.
ADDRESS = 0x00

#: Who it says it is, which is what decides who wins an address contest.
#: A real one is allocated; these are a demonstration.
MANUFACTURER = 66
IDENTITY = 0x1234

#: Parameter groups, from J1939-71.
EEC1 = (0xF0, 0x04)  # electronic engine controller 1: engine speed
CCVS1 = (0xFE, 0xF1)  # cruise control / vehicle speed
DM1 = (0xFE, 0xCA)  # active diagnostic trouble codes
COMPONENT_ID = 65259
COMPONENT_ID_PGN = (0xFE, 0xEB)

#: Every N polls.  EEC1 is a fast message and the others are not, and a node
#: that sent all three at the fastest rate is one that fills somebody's trace.
EVERY_CCVS1 = 2
EVERY_DM1 = 10

WHAT_IT_IS = b"PYCANGUI*DEMO ENGINE*SN0001*UNIT1*"


def ecu_name():
    """Who this ECU says it is.

    A J1939 NAME is sixty-four bits saying what a device is and who made
    it, and it is what decides who wins when two claim one address.  Its
    own function so that the number can be checked against a decoder
    without standing the whole engine up.
    """
    import j1939 as library

    return library.Name(
        arbitrary_address_capable=False,
        industry_group=library.Name.IndustryGroup.OnHighway,
        vehicle_system_instance=0,
        vehicle_system=0,
        function=0,  # engine
        function_instance=0,
        ecu_instance=0,
        manufacturer_code=MANUFACTURER,
        identity_number=IDENTITY,
    )


def start(node, *, ctx):
    import j1939 as library

    from pycangui.j1939 import _compat  # noqa: F401 - patches a typo in can-j1939

    name = ecu_name()
    # The library wants somewhere to put frames; the channel is that.
    ecu = library.ElectronicControlUnit(
        send_message=lambda can_id, ext, data, fd=False: _send(node, can_id, ext, data)
    )
    # node.listen rather than the notifier directly: the channel echoes what
    # this node sends, and a claim the stack hears back from itself is a
    # contender it will fight for ever.
    for listener in list(ecu._listeners):
        node.listen(listener)

    application = ecu.add_ca(name=name, device_address=ADDRESS)
    application.subscribe_request(lambda src, dest, pgn: _requested(node, pgn))
    application.start()  # begins the address claim

    node.state.library = library
    node.state.ecu = ecu
    node.state.ca = application
    node.state.polls = 0
    node.state.rpm = 800.0


def poll(node, *, ctx):
    """Broadcast, but only once the address is ours.

    Everything a J1939 device says is stamped with its source address, so
    sending before the claim has settled is sending as somebody else.
    """
    application = node.state.ca
    if application.state != node.state.library.ControllerApplication.State.NORMAL:
        return

    node.state.polls += 1
    node.state.rpm = 800 + 600 * (1 + math.sin(node.state.polls / 20))
    speed = max(0.0, (node.state.rpm - 800) / 12)

    application.send_pgn(0, *EEC1, 3, list(_eec1(node.state.rpm)))
    if node.state.polls % EVERY_CCVS1 == 0:
        application.send_pgn(0, *CCVS1, 6, list(_ccvs1(speed)))
    if node.state.polls % EVERY_DM1 == 0:
        application.send_pgn(0, *DM1, 6, list(_dm1(node.state.polls // EVERY_DM1)))


def stop(node, *, ctx):
    try:
        node.state.ca.stop()
    except Exception:  # an address never claimed has nothing to give back
        pass
    node.state.ecu.stop()  # the listeners come off with the node


# --- the messages ------------------------------------------------------------------
def _send(node, can_id: int, extended: bool, data) -> None:
    node.send(can_id, bytes(data), extended=extended)


def _requested(node, pgn: int) -> None:
    """Answer a request for what this ECU is.

    Thirty-four bytes, so the library breaks it into a broadcast
    announcement and a run of data frames.  Nothing here has to know that,
    which is the point of using a stack rather than composing frames.
    """
    if pgn == COMPONENT_ID:
        node.state.ca.send_pgn(0, *COMPONENT_ID_PGN, 6, list(WHAT_IT_IS))


def _eec1(rpm: float) -> bytes:
    """Engine speed at bytes 4-5, 0.125 rpm per bit.

    Everything this ECU does not model is 0xFF, which in J1939 means "not
    available" rather than zero -- a tool showing 0% torque is being told
    something quite different from a tool showing nothing.
    """
    return b"\xff\xff\xff" + struct.pack("<H", int(rpm / 0.125)) + b"\xff\xff\xff"


def _ccvs1(kph: float) -> bytes:
    """Wheel-based speed at bytes 2-3, 1/256 km/h per bit."""
    return b"\xff" + struct.pack("<H", int(kph * 256)) + b"\xff\xff\xff\xff\xff"


def _dm1(occurrences: int) -> bytes:
    """One active fault, so the J1939 pane has something to decode."""
    from pycangui.j1939 import Dtc, encode_dm1

    return encode_dm1(
        [Dtc(spn=110, fmi=3, occurrence=occurrences % 127, conversion_method=0)], awl=1
    )
