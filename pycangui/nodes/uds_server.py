# SPDX-License-Identifier: MIT-0
#
# A starting point, copied into your workspace for you to change. It is
# yours to edit, keep private or give away: pycangui claims nothing in it
# and asks for no credit, so what you write here needs nobody's permission.
"""A UDS server: something for the UDS pane to talk to.

The example of the *reactive* half of a node. There is no ``poll`` doing any
work here -- a diagnostic server says nothing until it is asked -- and the
one that exists only drains the transport.

Enough of ISO 14229 to exercise the UDS pane properly: sessions, security
with a seed and key, tester present, a handful of identifiers, stored faults
with a clear, a routine, ECU reset, communication control, DTC setting, a
baud rate change, and the negative responses a tester needs to see when it
gets something wrong. It answers functional requests too -- the ones sent to
every ECU at once -- and keeps quiet when asked to.

**The transport is not hand-rolled.**  ``isotp.NotifierBasedCanStack`` takes
the channel's bus and its reader and does the segmenting, so a response
longer than seven bytes works without this file knowing what a flow control
frame is. A node file reassembling multi-frame messages by hand would be a
poor example and a worse ECU.

To make it yours: change ``REQUEST_ID`` / ``RESPONSE_ID`` to your ECU's, put
your own identifiers in ``IDENTIFIERS``, and rewrite the services it does not
answer the way yours does.
"""

from __future__ import annotations

NAME = "UDS server"
DESCRIPTION = "A diagnostic ECU: sessions, security, DIDs, DTCs, routines."
#: Only to drain the transport, so it is quick without being silly.
RATE_HZ = 200

#: The conventional pair for an ECU with no better idea: the tester sends to
#: 0x7E0 and listens on 0x7E8.
REQUEST_ID = 0x7E0
RESPONSE_ID = 0x7E8
#: Where a tester sends a request meant for every ECU. Always one frame, since
#: nobody could send flow control for a message addressed to everybody.
FUNCTIONAL_ID = 0x7DF

#: Negative response codes, from ISO 14229. A tester tells the difference
#: between "I will not" and "I cannot" by these, so a server that answers
#: everything with one code is barely better than silence.
NRC_SERVICE_NOT_SUPPORTED = 0x11
NRC_SUBFUNC_NOT_SUPPORTED = 0x12
NRC_INCORRECT_LENGTH = 0x13
NRC_CONDITIONS_NOT_CORRECT = 0x22
NRC_REQUEST_OUT_OF_RANGE = 0x31
NRC_SECURITY_ACCESS_DENIED = 0x33
NRC_INVALID_KEY = 0x35
NRC_NOT_SUPPORTED_IN_SESSION = 0x7F

#: The services whose second byte is a sub-function, and whose top bit asks
#: for no positive answer: the tester only wants to hear an objection.
SUBFUNCTION_SERVICES = {0x10, 0x11, 0x28, 0x3E, 0x85, 0x87}
#: The refusals an ECU keeps to itself when the request went to every ECU:
#: "I do not do that" from each of them is noise, not news.
QUIET_WHEN_FUNCTIONAL = {
    NRC_SERVICE_NOT_SUPPORTED,
    NRC_SUBFUNC_NOT_SUPPORTED,
    NRC_REQUEST_OUT_OF_RANGE,
    0x7E,
    NRC_NOT_SUPPORTED_IN_SESSION,
}
#: The bitrates LinkControl can move this ECU to, by ISO 14229-1's fixed ids.
LINK_RATES = {0x10: 125_000, 0x11: 250_000, 0x12: 500_000, 0x13: 1_000_000}

#: How long a tester should wait for an answer (P2) and for one this ECU has
#: said is coming (P2*), announced with every session change.
P2_MS = 250
P2_STAR_MS = 5000

#: What this ECU says it is. 0x0102 is writable, and only once unlocked,
#: which is what makes the security services worth having.
IDENTIFIERS: dict[int, bytes] = {
    0xF190: b"PYCANGUI0DEMO0001",  # VIN
    0xF187: b"DEMO-ECU-1",  # spare part number
    0xF195: b"1.2.3",  # software version
    0x0101: (132).to_bytes(2, "big"),  # battery, 0.1 V -> 13.2 V
    0x0102: (0).to_bytes(2, "big"),  # a setpoint somebody may write
}

#: Stored faults, as code -> status byte.
FAULTS: dict[int, int] = {0x012345: 0x09, 0x9A0100: 0x2F}

#: The seed this ECU hands out, and the key is every byte inverted. A real
#: one is a secret and an algorithm; this is a demonstration that the
#: exchange happens at all.
SEED = bytes([0x12, 0x34, 0x56, 0x78])
ROUTINE = 0x0203


def start(node, *, ctx):
    import isotp

    address = isotp.Address(isotp.AddressingMode.Normal_11bits, txid=RESPONSE_ID, rxid=REQUEST_ID)
    node.state.stack = isotp.NotifierBasedCanStack(
        node.bus(), node.notifier(), address=address, params={"tx_padding": 0xAA}
    )
    node.state.stack.start()
    node.state.functional = []
    node.listen(_Functional(node.state.functional))
    node.state.session = 1
    node.state.communication = (0, 0)
    node.state.dtc_setting_on = True
    node.state.link_rate = None  # verified, waiting for the transition
    node.state.bitrate = None  # what it was told to change to
    node.state.unlocked = False
    node.state.seed = None
    node.state.identifiers = dict(IDENTIFIERS)
    node.state.faults = dict(FAULTS)
    node.state.routine_running = False


def poll(node, *, ctx):
    """Answer whatever the transport has reassembled.

    All this does is move requests from the stack to the handler. The work
    is in the services, and the segmenting is the library's.
    """
    while (request := node.state.stack.recv()) is not None:
        if answer := _answer(node, bytes(request)):
            node.state.stack.send(answer)
    while node.state.functional:
        request = node.state.functional.pop(0)
        answer = _answer(node, request)
        if answer[:1] == b"\x7f" and answer[2] in QUIET_WHEN_FUNCTIONAL:
            continue
        if answer:
            node.state.stack.send(answer)


def stop(node, *, ctx):
    node.state.stack.stop()


class _Functional:
    """Picks the requests sent to every ECU off the bus: one frame each."""

    def __init__(self, queue: list) -> None:
        self._queue = queue

    def on_message_received(self, msg) -> None:
        data = bytes(msg.data)
        if msg.arbitration_id == FUNCTIONAL_ID and data and data[0] >> 4 == 0:
            self._queue.append(data[1 : 1 + (data[0] & 0x0F)])

    def stop(self) -> None:
        pass


def _answer(node, request: bytes) -> bytes:
    if not request:
        return b""
    service = request[0]
    handler = _SERVICES.get(service)
    if handler is None:
        return _no(service, NRC_SERVICE_NOT_SUPPORTED)
    answer = handler(node, request)
    quiet = service in SUBFUNCTION_SERVICES and len(request) > 1 and request[1] & 0x80
    if quiet and answer[:1] != b"\x7f":
        return b""  # done, and asked not to say so
    return answer


# --- the services ------------------------------------------------------------------
def _session(node, request: bytes) -> bytes:
    if len(request) != 2:
        return _no(0x10, NRC_INCORRECT_LENGTH)
    sub = request[1] & 0x7F
    if sub not in (1, 2, 3):
        return _no(0x10, NRC_SUBFUNC_NOT_SUPPORTED)
    node.state.session = sub
    if sub == 1:
        # Back to default, and security goes with it. An ECU that stayed
        # unlocked across a session change would be a security hole with a
        # very short life.
        node.state.unlocked = False
    # P2, in milliseconds, then P2* in tens of them. A real ECU promises
    # 50 ms because its reply comes from an interrupt; this one's comes from a
    # timer in pycangui's own window, which waits behind a plot redrawing or a
    # busy trace, so it promises what it can keep. The tester believes
    # whatever is sent here and gives up on the ECU the moment it passes.
    return bytes([0x50, sub]) + (P2_MS).to_bytes(2, "big") + (P2_STAR_MS // 10).to_bytes(2, "big")


def _tester_present(node, request: bytes) -> bytes:
    return bytes([0x7E, request[1] & 0x7F])


def _communication(node, request: bytes) -> bytes:
    if len(request) < 3:
        return _no(0x28, NRC_INCORRECT_LENGTH)
    if node.state.session == 1:
        return _no(0x28, NRC_NOT_SUPPORTED_IN_SESSION)
    node.state.communication = (request[1] & 0x7F, request[2])
    return bytes([0x68, request[1] & 0x7F])


def _dtc_setting(node, request: bytes) -> bytes:
    sub = request[1] & 0x7F
    if sub not in (1, 2):
        return _no(0x85, NRC_SUBFUNC_NOT_SUPPORTED)
    if node.state.session == 1:
        return _no(0x85, NRC_NOT_SUPPORTED_IN_SESSION)
    node.state.dtc_setting_on = sub == 1
    return bytes([0xC5, sub])


def _link(node, request: bytes) -> bytes:
    """LinkControl: verify a fixed rate, then move to it on the transition.

    On a virtual bus there is no rate to change, so "moving" is noting it;
    a real ECU would switch its controller once the answer had gone out.
    """
    sub = request[1] & 0x7F
    if node.state.session == 1:
        return _no(0x87, NRC_NOT_SUPPORTED_IN_SESSION)
    if sub == 1:
        if len(request) != 3 or request[2] not in LINK_RATES:
            return _no(0x87, NRC_REQUEST_OUT_OF_RANGE)
        node.state.link_rate = LINK_RATES[request[2]]
        return bytes([0xC7, 1])
    if sub == 3:
        if node.state.link_rate is None:
            return _no(0x87, 0x24)  # request sequence error: nothing verified
        node.state.bitrate, node.state.link_rate = node.state.link_rate, None
        return bytes([0xC7, 3])
    return _no(0x87, NRC_SUBFUNC_NOT_SUPPORTED)


def _reset(node, request: bytes) -> bytes:
    if node.state.session == 1:
        return _no(0x11, NRC_NOT_SUPPORTED_IN_SESSION)
    node.state.session = 1
    node.state.unlocked = False
    return bytes([0x51, request[1] & 0x7F])


def _security(node, request: bytes) -> bytes:
    if node.state.session == 1:
        return _no(0x27, NRC_NOT_SUPPORTED_IN_SESSION)
    sub = request[1]
    if sub == 1:  # request seed
        node.state.seed = SEED if not node.state.unlocked else bytes(len(SEED))
        return bytes([0x67, 1]) + node.state.seed
    if sub == 2:  # send key
        if node.state.seed is None:
            return _no(0x27, NRC_CONDITIONS_NOT_CORRECT)
        if bytes(request[2:]) != bytes(b ^ 0xFF for b in node.state.seed):
            node.state.seed = None  # one go per seed, as a real ECU does
            return _no(0x27, NRC_INVALID_KEY)
        node.state.unlocked = True
        return bytes([0x67, 2])
    return _no(0x27, NRC_SUBFUNC_NOT_SUPPORTED)


def _read_did(node, request: bytes) -> bytes:
    if len(request) != 3:
        return _no(0x22, NRC_INCORRECT_LENGTH)
    did = int.from_bytes(request[1:3], "big")
    if did not in node.state.identifiers:
        return _no(0x22, NRC_REQUEST_OUT_OF_RANGE)
    return bytes([0x62]) + request[1:3] + node.state.identifiers[did]


def _write_did(node, request: bytes) -> bytes:
    did = int.from_bytes(request[1:3], "big")
    if did != 0x0102:
        return _no(0x2E, NRC_REQUEST_OUT_OF_RANGE)
    if not node.state.unlocked:
        return _no(0x2E, NRC_SECURITY_ACCESS_DENIED)
    node.state.identifiers[did] = bytes(request[3:])
    return bytes([0x6E]) + request[1:3]


def _read_faults(node, request: bytes) -> bytes:
    if request[1] != 0x02:
        return _no(0x19, NRC_SUBFUNC_NOT_SUPPORTED)
    mask = request[2]
    out = bytes([0x59, 0x02, 0xFF])  # the availability mask, then the faults
    for code, status in node.state.faults.items():
        if status & mask:
            out += code.to_bytes(3, "big") + bytes([status])
    return out


def _clear_faults(node, request: bytes) -> bytes:
    node.state.faults.clear()
    return bytes([0x54])


def _routine(node, request: bytes) -> bytes:
    sub = request[1]
    rid = int.from_bytes(request[2:4], "big")
    if rid != ROUTINE:
        return _no(0x31, NRC_REQUEST_OUT_OF_RANGE)
    if sub == 1:
        node.state.routine_running = True
        return bytes([0x71, 1]) + request[2:4]
    if sub == 2:
        node.state.routine_running = False
        return bytes([0x71, 2]) + request[2:4]
    if sub == 3:
        running = b"\x01" if node.state.routine_running else b"\x00"
        return bytes([0x71, 3]) + request[2:4] + running
    return _no(0x31, NRC_SUBFUNC_NOT_SUPPORTED)


#: Service id -> what answers it. A table rather than a chain of ifs: adding
#: a service your ECU has is one line here and one function.
_SERVICES = {
    0x10: _session,
    0x11: _reset,
    0x14: _clear_faults,
    0x19: _read_faults,
    0x22: _read_did,
    0x27: _security,
    0x2E: _write_did,
    0x28: _communication,
    0x31: _routine,
    0x3E: _tester_present,
    0x85: _dtc_setting,
    0x87: _link,
}


def _no(service: int, why: int) -> bytes:
    return bytes([0x7F, service, why])
