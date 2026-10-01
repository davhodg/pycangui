# SPDX-License-Identifier: MIT-0
#
# A starting point, copied into your workspace for you to change. It is
# yours to edit, keep private or give away: pycangui claims nothing in it
# and asks for no credit, so what you write here needs nobody's permission.
"""A J1939 engine: claims an address, broadcasts, and answers requests.

Where ``canopen_device.py`` takes a whole protocol server from
``node.canopen()``, this one builds its own from the ``j1939`` library --
which is the more usual shape. pycangui has a server to hand you for
CANopen and not for anything else, so most node files look like this: bring
the stack you need, wire it to the channel, and get on with what your device
does.

A J1939 device cannot simply start shouting. It claims a source address
first and defends it, and until that succeeds nothing it sends means
anything -- which is why ``poll`` checks before it broadcasts.

It sends EEC1 (engine speed), CCVS1 (wheel speed) and a DM1 fault, and
answers requests: its identification -- component, ECU and software, sent
as multi-packet BAM transfers because they do not fit in eight bytes, the
reason the library is worth having -- a previously active fault (DM2), its
readiness (DM5), and the two requests that clear faults (DM3, DM11), which
it acknowledges.

To make it yours: change ``ADDRESS``, ``MANUFACTURER`` and ``IDENTITY`` to
your ECU's, and broadcast the parameter groups it broadcasts.
"""

from __future__ import annotations

import math
import struct

NAME = "J1939 engine"
DESCRIPTION = "Claims an address, broadcasts engine and wheel speed, reports a fault."
RATE_HZ = 10

#: The address this ECU claims. 0x00 is the engine's by convention.
ADDRESS = 0x00

#: Who it says it is, which is what decides who wins an address contest.
#: A real one is allocated; these are a demonstration, and 0 is the
#: manufacturer code that is reserved and allocated to nobody.
MANUFACTURER = 0
IDENTITY = 0x1234

#: Parameter groups, from J1939-71.
EEC1 = (0xF0, 0x04)  # electronic engine controller 1: engine speed
CCVS1 = (0xFE, 0xF1)  # cruise control / vehicle speed
DM1 = (0xFE, 0xCA)  # active diagnostic trouble codes
COMPONENT_ID = 65259
ECU_ID = 64965
SOFTWARE_ID = 65242
DM2 = 65227  # previously active diagnostic trouble codes
DM3 = 65228  # clear previously active
DM5 = 65230  # diagnostic readiness
DM11 = 65235  # clear active
ACKNOWLEDGEMENT = 59392  # to everybody: it names who asked inside

#: Every N polls. EEC1 is a fast message and the others are not, and a node
#: that sent all three at the fastest rate is one that fills somebody's trace.
EVERY_CCVS1 = 2
EVERY_DM1 = 10

WHAT_IT_IS = b"PYCANGUI*DEMO ENGINE*SN0001*UNIT1*"
#: Part number, serial number, location, type -- each ended by '*'.
ECU_IS = b"PN-1000*SN0001*ENGINE BAY*DEMO ECU*"
#: How many software identifiers, then each ended by '*'.
SOFTWARE_IS = b"\x02APP 1.0.0*BOOT 2.1*"


def ecu_name():
    """Who this ECU says it is.

    A J1939 NAME is sixty-four bits saying what a device is and who made
    it, and it is what decides who wins when two claim one address. Its
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
    application.subscribe_request(lambda src, dest, pgn: _requested(node, pgn, src))
    application.start()  # begins the address claim

    node.state.library = library
    node.state.ecu = ecu
    node.state.ca = application
    node.state.polls = 0
    node.state.rpm = 800.0
    node.state.previous_cleared = False


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


def _requested(node, pgn: int, asker: int) -> None:
    """Answer a request for what this ECU is, or what is wrong with it.

    The identification is more than eight bytes, so the library breaks it
    into a broadcast announcement and a run of data frames. Nothing here has
    to know that, which is the point of using a stack rather than composing
    frames.
    """
    if pgn == COMPONENT_ID:
        _answer(node, COMPONENT_ID, WHAT_IT_IS)
    elif pgn == ECU_ID:
        _answer(node, ECU_ID, ECU_IS)
    elif pgn == SOFTWARE_ID:
        _answer(node, SOFTWARE_ID, SOFTWARE_IS)
    elif pgn == DM2:
        _answer(node, DM2, _dm2(node))
    elif pgn == DM5:
        # One active, one or none previously active, not built to OBD rules (5).
        previous = 0 if node.state.previous_cleared else 1
        _answer(node, DM5, bytes([1, previous, 5, 0, 0, 0, 0, 0]))
    elif pgn in (DM3, DM11):
        if pgn == DM3:
            node.state.previous_cleared = True
        # Acknowledged: control byte 0, then the asker's address and the PGN.
        answer = bytes([0x00, 0xFF, 0xFF, 0xFF, asker]) + pgn.to_bytes(3, "little")
        _answer(node, ACKNOWLEDGEMENT, answer, to=0xFF)


def _answer(node, pgn: int, data: bytes, to: int | None = None) -> None:
    """Send a parameter group; ``to`` is the address, for one that takes one."""
    low = pgn & 0xFF if to is None else to
    node.state.ca.send_pgn(0, (pgn >> 8) & 0xFF, low, 6, list(data))


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


def _dm2(node) -> bytes:
    """One fault that was active once and is not now, until DM3 clears it."""
    from pycangui.j1939 import Dtc, encode_dm1

    if node.state.previous_cleared:
        return encode_dm1([])
    return encode_dm1([Dtc(spn=190, fmi=2, occurrence=4, conversion_method=0)])


def _dm1(occurrences: int) -> bytes:
    """One active fault, so the J1939 pane has something to decode."""
    from pycangui.j1939 import Dtc, encode_dm1

    return encode_dm1(
        [Dtc(spn=110, fmi=3, occurrence=occurrences % 127, conversion_method=0)], awl=1
    )
