# SPDX-License-Identifier: MIT-0
#
# A starting point, copied into your workspace for you to change. It is
# yours to edit, keep private or give away: pycangui claims nothing in it
# and asks for no credit, so what you write here needs nobody's permission.
"""A CANopen device: heartbeat, SDO server, and a TPDO that means something.

The one to copy if your product speaks CANopen. Almost all of what a CANopen
node has to do is the same for every node -- answer SDO reads and writes,
produce a heartbeat, obey NMT -- so ``node.canopen()`` does all of it from
your EDS and hands back the server. What is left in this file is the only
part that is actually about your device: what its measurements do.

This one slews a speed toward whatever is written to *Speed demand*, counts
an odometer, and transmits both in TPDO1. Write to it with an SDO from the
CANopen pane, or send it RPDO1, and watch the trace.

To make it yours: change ``EDS`` to your own file in the workspace ``eds``
folder, change ``NODE_ID``, and rewrite ``poll``.
"""

from __future__ import annotations

import struct

from pycangui import resources

NAME = "CANopen device"
DESCRIPTION = "Heartbeat, SDO server and a cyclic TPDO, built from an EDS."
RATE_HZ = 10

#: Your device's EDS. ``ctx.eds_dir / "yours.eds"`` once you have one; the
#: shipped sample is here so this file works the moment it is started.
EDS = resources.path("demo.eds")
NODE_ID = 5

#: How fast the measured speed may change per poll, so it ramps rather than
#: jumping -- which is what makes it look like a machine on the plot.
SLEW = 50

SPEED_DEMAND = 0x2001  # written by whoever is commanding this device
MEASUREMENTS = 0x2000  # what it reports back: speed at sub 1, odometer at 2
ERROR_REGISTER = 0x1001  # CiA 301: which kinds of fault are active
#: CiA 301's list of what went wrong, newest first, with sub 0 the count.
#: A real drive keeps one so that a tool plugged in afterwards can still
#: find out what happened; this device keeps one for the same reason.
STORED_ERRORS = 0x1003
MOST_STORED = 4  # what the EDS declares room for

#: Past this demand the device complains. A device that never faults is a
#: device you cannot test the fault handling of.
TOO_FAST = 2000
OVER_CURRENT = 0x2310  # the emergency code for it, from CiA 301
ERROR_CURRENT = 0x02  # the bit in the error register that goes with it


def start(node, *, ctx):
    """Stand the CANopen server up, and put the device in a sane state.

    Called once, before the first poll. Everything built here is reached
    again through ``node.state``, which is this node's own and is not shared
    with a second copy of it on another channel.
    """
    device = node.canopen(EDS, NODE_ID)
    device.nmt.state = "PRE-OPERATIONAL"
    device.nmt.start_heartbeat(500)

    # Read from the EDS, but deliberately not started on a timer of its own:
    # poll() below transmits it, so that it stops when the device is not
    # operational. A periodic task would go on sending regardless, and the
    # NMT buttons in the CANopen pane would appear to do nothing.
    tpdo = device.tpdo[1]
    tpdo.read(from_od=True)

    # An RPDO is how a controller commands this device without an SDO for
    # every value. canopen leaves applying one to the application, which is
    # what the callback below is for.
    rpdo = device.rpdo[1]
    rpdo.read(from_od=True)
    rpdo.add_callback(lambda pdo_map: _apply(device, pdo_map))

    node.state.device = device
    node.state.tpdo = tpdo
    node.state.speed = 0
    node.state.odometer = 0
    node.state.over_current = False
    node.log(f"CANopen node {NODE_ID} is up")


def poll(node, *, ctx):
    """The device's own behaviour, ten times a second.

    Everything above this line is CANopen boilerplate that every node shares.
    This is the part that is about the machine.
    """
    device, tpdo = node.state.device, node.state.tpdo
    demand = struct.unpack("<h", device.get_data(SPEED_DEMAND, 0))[0]

    step = max(-SLEW, min(SLEW, demand - node.state.speed))
    node.state.speed += step
    node.state.odometer += abs(node.state.speed)

    device.set_data(MEASUREMENTS, 1, struct.pack("<h", node.state.speed))
    device.set_data(MEASUREMENTS, 2, struct.pack("<I", node.state.odometer))
    tpdo["Measurements.Motor speed"].raw = node.state.speed
    tpdo["Measurements.Odometer"].raw = node.state.odometer

    # A CANopen device transmits process data only while it is operational,
    # which is most of what the NMT state is for. Start the node from the
    # CANopen pane and the TPDO appears in the trace; stop it and it goes.
    #
    # transmit() rather than update(): update() feeds a periodic task, and
    # this PDO deliberately has none -- one running on canopen's own thread
    # would go on sending through a stop, and at a rate this file does not
    # set. Sending it here means the TPDO rate is RATE_HZ, where a reader
    # would look for it.
    if device.nmt.state == "OPERATIONAL":
        tpdo.transmit()

    _emergencies(node, device, demand)


def _emergencies(node, device, demand: int) -> None:
    """Complain past a speed demand this device cannot meet, and stop when
    it comes back down.

    An emergency is the one CANopen message a tool cannot provoke by asking
    for it, so a demo device that never raised one would leave the
    Emergencies tab with nothing to show and no way to get anything. The
    manufacturer bytes are filled in too: an EMCY with an empty tail is not
    what a real device sends.
    """
    from pycangui.canopen.emcy import encode as encode_emcy

    too_fast = abs(demand) > TOO_FAST
    if too_fast and not node.state.over_current:
        node.state.over_current = True
        current = min(abs(demand) // 10, 0xFFFF)  # 0.1 A per bit
        device.set_data(ERROR_REGISTER, 0, bytes([ERROR_CURRENT]))
        _remember(device, OVER_CURRENT, current)
        node.send(
            0x80 + NODE_ID,
            encode_emcy(OVER_CURRENT, ERROR_CURRENT, current.to_bytes(2, "little") + b"\x01"),
        )
    elif not too_fast and node.state.over_current:
        node.state.over_current = False
        device.set_data(ERROR_REGISTER, 0, b"\x00")
        node.send(0x80 + NODE_ID, encode_emcy(0x0000, 0x00, b""))  # error reset


def _remember(device, code: int, info: int) -> None:
    """Push an entry onto 0x1003, newest first, as CiA 301 describes it.

    The reset is deliberately not recorded. 0x1003 is a list of what went
    wrong; an entry saying nothing went wrong is how a device fills its own
    history with silence.
    """
    kept = int.from_bytes(device.get_data(STORED_ERRORS, 0), "little")
    for sub in range(min(kept, MOST_STORED - 1), 0, -1):
        device.set_data(STORED_ERRORS, sub + 1, device.get_data(STORED_ERRORS, sub))
    device.set_data(STORED_ERRORS, 1, (code | (info << 16)).to_bytes(4, "little"))
    device.set_data(STORED_ERRORS, 0, bytes([min(kept + 1, MOST_STORED)]))


def stop(node, *, ctx):
    """Put the device away.

    The bus and the notifier are pycangui's to close. The heartbeat runs on
    a thread of canopen's own, and was started here, so it is stopped here.
    """
    node.state.device.nmt.stop_heartbeat()


def _apply(device, pdo_map):
    """Copy a received RPDO into the object dictionary.

    canopen decodes the frame and stops there, on the reasonable grounds that
    only the application knows whether a written value should be accepted.
    This one accepts everything.
    """
    for var in pdo_map:
        device.set_data(var.index, var.subindex, var.get_data())
