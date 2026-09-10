"""A UDS server: something for the UDS pane to talk to.

The example of the *reactive* half of a node.  It has no ``poll`` at all --
a diagnostic server says nothing until it is asked -- so everything here
hangs off ``on_frame``.

It answers the handful of services that make the UDS pane usable without an
ECU: open a session, read a couple of identifiers, report no faults, run a
routine.  Anything it does not know it refuses the way a real server does,
with a negative response rather than silence, because silence is
indistinguishable from a broken bus and a tester should be able to tell.

Single frames only.  Anything longer than seven bytes needs ISO-TP's
flow control, which is a transport rather than an example, and is what
``pycangui.uds`` already implements on the tester's side.

To make it yours: change ``REQUEST_ID`` / ``RESPONSE_ID`` to your ECU's, and
put your own identifiers in ``IDENTIFIERS``.
"""

from __future__ import annotations

NAME = "UDS server"
DESCRIPTION = "Answers diagnostic requests: sessions, DIDs, DTCs, a routine."

#: The conventional pair for an ECU that has no better idea: the tester
#: sends to 0x7E0 and listens on 0x7E8.
REQUEST_ID = 0x7E0
RESPONSE_ID = 0x7E8

#: Services this server knows.  Anything else gets 0x11, service not
#: supported, which is what tells a tester it reached *something*.
DIAGNOSTIC_SESSION_CONTROL = 0x10
TESTER_PRESENT = 0x3E
READ_DATA_BY_IDENTIFIER = 0x22
READ_DTC_INFORMATION = 0x19
ROUTINE_CONTROL = 0x31

POSITIVE = 0x40  # added to the service id in a positive response
NEGATIVE = 0x7F
SERVICE_NOT_SUPPORTED = 0x11
REQUEST_OUT_OF_RANGE = 0x31

#: What this ECU says it is.  Ordinary ASCII, which is how most of the
#: identification DIDs are defined.
IDENTIFIERS = {
    0xF190: b"PYCANGUIDEMO00001",  # VIN
    0xF187: b"DEMO-0001",  # manufacturer spare part number
    0xF195: b"1.0.0",  # supplier software version
}


def on_frame(node, frame, *, ctx):
    """Answer a request, if it is one and it is for us.

    Every frame on the channel arrives here, including this node's own
    answers coming back around on other buses, so the first thing to do is
    decide whether this one is any of our business.
    """
    if frame.arbitration_id != REQUEST_ID or frame.is_extended_id:
        return
    request = _unwrap(frame.data)
    if not request:
        return  # not a single frame, or an empty one
    node.send(RESPONSE_ID, _wrap(_answer(request)))


def _answer(request: bytes) -> bytes:
    """The response bytes for a request, positive or negative."""
    service = request[0]

    if service == DIAGNOSTIC_SESSION_CONTROL and len(request) >= 2:
        # The two timing parameters a tester reads out of this: P2 in
        # milliseconds, then extended P2 in tens of milliseconds.
        return bytes([service + POSITIVE, request[1], 0x00, 0x32, 0x01, 0xF4])

    if service == TESTER_PRESENT:
        return bytes([service + POSITIVE, 0x00])

    if service == READ_DATA_BY_IDENTIFIER and len(request) >= 3:
        did = int.from_bytes(request[1:3], "big")
        if (value := IDENTIFIERS.get(did)) is None:
            return _refuse(service, REQUEST_OUT_OF_RANGE)
        return bytes([service + POSITIVE]) + request[1:3] + value

    if service == READ_DTC_INFORMATION and len(request) >= 2:
        # Report number of DTCs by status mask: availability mask, format,
        # then a count of zero.  A healthy ECU, which is the boring answer
        # and the right default.
        return bytes([service + POSITIVE, request[1], 0xFF, 0x01, 0x00, 0x00])

    if service == ROUTINE_CONTROL and len(request) >= 4:
        return bytes([service + POSITIVE, request[1], request[2], request[3]])

    return _refuse(service, SERVICE_NOT_SUPPORTED)


def _refuse(service: int, why: int) -> bytes:
    return bytes([NEGATIVE, service, why])


def _unwrap(data: bytes) -> bytes:
    """The payload of an ISO-TP single frame, or nothing.

    A single frame is a length in the low nibble of the first byte and that
    many bytes after it.  A first frame (0x1n) means the tester is sending
    something longer than this server handles, and is ignored rather than
    half-answered.
    """
    if not data or (data[0] >> 4) != 0:
        return b""
    length = data[0] & 0x0F
    return bytes(data[1 : 1 + length]) if 0 < length <= len(data) - 1 else b""


def _wrap(payload: bytes) -> bytes:
    """A response as an ISO-TP single frame, padded the conventional way."""
    body = payload[:7]
    return bytes([len(body)]) + body + b"\xaa" * (7 - len(body))
