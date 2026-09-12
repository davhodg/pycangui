"""Simulated nodes: the rest of the bus, written in Python.

The thing being tested is a *mechanism*, so most of these run a node file
written in the test rather than a shipped one -- a two-line node makes it
obvious which behaviour is the mechanism's and which is the example's.  The
shipped ones get their own section at the end, because an example that does
not run is worse than no example.
"""

import can
import pytest

from pycangui.core import simnodes
from pycangui.core.channels import Channels
from pycangui.core.simnodes import NodeError, SimulatedNodes


class FakeContext:
    """Enough Context for a node: a folder to read, and somewhere to shout."""

    def __init__(self, folder):
        self.nodes_dir = folder
        self.eds_dir = folder
        self.messages: list[str] = []

    def log(self, message, level=None):
        self.messages.append(message)

    warn = log
    error = log


@pytest.fixture
def folder(tmp_path):
    made = tmp_path / "nodes"
    made.mkdir()
    return made


@pytest.fixture
def ctx(folder):
    return FakeContext(folder)


@pytest.fixture
def manager(app, ctx):
    made = SimulatedNodes(ctx)
    yield made
    made.stop_all()


#: A node that counts its own polls.  Used wherever a test needs to know
#: that polling happened, or that two instances are not sharing one counter.
COUNTER = (
    "RATE_HZ = 50\n"
    "def start(node, *, ctx): node.state.n = 0\n"
    "def poll(node, *, ctx): node.state.n += 1\n"
)


def write(folder, name: str, body: str) -> None:
    (folder / f"{name}.py").write_text(body, encoding="utf-8")


def spin(app, seconds=1.0, until=None):
    """Turn the event loop while real time passes, up to a deadline.

    ``processEvents`` in a tight loop is not enough on its own: a QTimer
    fires on the clock, and a thousand iterations of a busy loop take no
    time at all, so nothing would ever be due.  Returns whether ``until``
    came true, and comes back the moment it does rather than always waiting.
    """
    from time import monotonic, sleep

    deadline = monotonic() + seconds
    while monotonic() < deadline:
        app.processEvents()
        if until is not None and until():
            return True
        sleep(0.005)
    app.processEvents()
    return bool(until and until())


def pump(app, bus, seconds=1.0):
    """Turn the event loop until a frame turns up, or give up.

    A node answers on the GUI thread by way of a queued signal, so nothing
    happens at all unless somebody is running the loop -- and a sleep would
    only make the test slow and still empty.
    """
    from time import monotonic

    deadline = monotonic() + seconds
    while monotonic() < deadline:
        app.processEvents()
        if (msg := bus.recv(timeout=0.01)) is not None:
            return msg
    return None


# --- what a file says it is, before running any of it ----------------------------------
def test_a_node_file_is_described_without_importing_it(folder):
    """Which is what lets a broken one still be listed.  A kind that vanished
    from the dialog because of a typo would send somebody looking for a file
    they are staring at."""
    write(
        folder,
        "thing",
        'NAME = "A thing"\nDESCRIPTION = "does things"\nRATE_HZ = 4\n'
        "def poll(node, *, ctx): pass\n",
    )
    kind = simnodes.kinds_in(folder)[0]
    assert (kind.id, kind.name, kind.description, kind.rate_hz) == (
        "thing",
        "A thing",
        "does things",
        4.0,
    )
    assert kind.error is None


def test_a_file_that_will_not_parse_is_listed_with_its_error(folder):
    write(folder, "broken", "def poll(node:\n")
    kind = simnodes.kinds_in(folder)[0]
    assert kind.error and "line 1" in kind.error
    assert kind.label == "broken", "under its filename, since it never said a name"


def test_a_file_with_none_of_the_functions_is_refused(folder):
    """A node that does nothing is a mistake rather than a very quiet node."""
    write(folder, "empty", 'NAME = "Nothing"\nVALUE = 3\n')
    assert "none of" in simnodes.kinds_in(folder)[0].error


def test_a_node_that_declares_nothing_still_runs(folder):
    """Every declaration has a default.  Somebody trying the smallest thing
    that could work should find that it works."""
    write(folder, "bare", "def poll(node, *, ctx): pass\n")
    kind = simnodes.kinds_in(folder)[0]
    assert kind.error is None and kind.label == "bare"
    assert kind.rate_hz == simnodes.DEFAULT_RATE_HZ


# --- starting and stopping -------------------------------------------------------------
def test_polling_happens_at_about_the_rate_asked_for(app, folder, manager):
    write(folder, "counter", COUNTER)
    node = manager.start("counter", "vtest1")
    assert spin(app, 1.0, until=lambda: node.state.n >= 10), (
        f"50 Hz for a second should be dozens of polls, not {node.state.n}"
    )


def test_a_node_sends_and_another_bus_hears_it(app, folder, manager):
    """The whole point: a device that is not there, on a bus that is."""
    write(
        folder,
        "shouter",
        "RATE_HZ = 50\ndef poll(node, *, ctx): node.send(0x123, b'\\x01\\x02')\n",
    )
    listening = can.Bus(interface="virtual", channel="vtest2")
    try:
        manager.start("shouter", "vtest2")
        got = pump(app, listening)
        assert got is not None and got.arbitration_id == 0x123
        assert bytes(got.data) == b"\x01\x02"
    finally:
        listening.shutdown()


def test_a_node_hears_what_is_on_its_channel_and_can_answer(app, folder, manager):
    write(
        folder,
        "echo",
        "def on_frame(node, frame, *, ctx):\n"
        "    if frame.arbitration_id == 0x100:\n"
        "        node.send(0x101, bytes(frame.data))\n",
    )
    other = can.Bus(interface="virtual", channel="vtest3")
    try:
        manager.start("echo", "vtest3")
        other.send(can.Message(arbitration_id=0x100, data=b"\xaa", is_extended_id=False))
        got = pump(app, other)
        assert got is not None and got.arbitration_id == 0x101
    finally:
        other.shutdown()


def test_stopping_puts_the_bus_down(app, folder, manager):
    write(folder, "quiet", "def poll(node, *, ctx): pass\n")
    node = manager.start("quiet", "vtest4")
    assert node.running and manager.running() == [node]
    manager.stop(node)
    assert not node.running and manager.running() == []


def test_stop_all_takes_everything_with_it(app, folder, manager):
    """Called as the window closes.  A node still holding a bus open is a
    process that will not exit, which is a bug nobody can see."""
    write(folder, "quiet", "def poll(node, *, ctx): pass\n")
    manager.start("quiet", "vtest5")
    manager.start("quiet", "vtest6")
    manager.stop_all()
    assert manager.running() == []


# --- state, which is what makes two of the same kind possible ---------------------
def test_two_of_one_kind_do_not_share_their_state(app, folder, manager):
    """The reason a node is handed an object rather than being two free
    functions with a module between them."""
    write(folder, "counter", COUNTER)
    first = manager.start("counter", "vtest7")
    second = manager.start("counter", "vtest8")
    second.state.n = 1000
    spin(app, 0.5, until=lambda: first.state.n >= 5)
    assert 0 < first.state.n < 500, "the two are sharing a counter"


# --- being wrong, which a file being written is most of the time -----------------
def test_a_node_that_raises_says_so_and_stops(app, folder, manager, ctx):
    """Rather than raising every hundred milliseconds for the rest of the
    session, which fills the Event Log with one message and buries the rest."""
    write(folder, "bad", "RATE_HZ = 50\ndef poll(node, *, ctx): raise ValueError('no')\n")
    node = manager.start("bad", "vtest9")
    assert spin(app, 1.0, until=lambda: not node.running), "it kept going"
    assert any("has been stopped" in m for m in ctx.messages)


def test_a_node_that_will_not_import_is_a_message_not_a_crash(app, folder, manager):
    write(folder, "explodes", "def poll(node, *, ctx): pass\nraise RuntimeError('at import')\n")
    with pytest.raises(NodeError, match="failed to load"):
        manager.start("explodes", "vtest10")


def test_asking_for_a_kind_that_is_not_there_says_which(app, manager):
    with pytest.raises(NodeError, match="nonesuch"):
        manager.start("nonesuch", "vtest11")


def test_a_node_is_not_started_if_the_question_is_declined(app, folder, ctx):
    """A node transmits, and transmitting onto a real bus is what pycangui
    asks about everywhere else.  One that joined quietly would be the hole
    in that."""
    write(folder, "quiet", "def poll(node, *, ctx): pass\n")
    manager = SimulatedNodes(ctx, may_transmit=lambda _channels: False)
    with pytest.raises(NodeError, match="not agreed"):
        manager.start("quiet", "Sim")
    assert manager.running() == []


def test_the_question_is_asked_once_with_every_channel(app, folder, ctx):
    """A gateway stands on two, and being asked twice for one action is a
    dialog people learn to dismiss without reading."""
    write(folder, "quiet", "def poll(node, *, ctx): pass\n")
    asked = []
    manager = SimulatedNodes(ctx, may_transmit=lambda channels: asked.append(channels) or True)
    manager.start("quiet", "va", extra=["vb"])
    manager.stop_all()
    assert asked == [["va", "vb"]]


# --- more than one bus, which is all a gateway is ---------------------------------
def test_a_node_can_stand_on_two_channels_and_pass_frames_across(app, folder, manager):
    write(
        folder,
        "bridge",
        "def on_frame(node, frame, *, ctx):\n"
        "    for channel in node.channels:\n"
        "        if channel != frame.channel:\n"
        "            node.send(frame.arbitration_id, bytes(frame.data), channel=channel)\n",
    )
    left = can.Bus(interface="virtual", channel="vleft")
    right = can.Bus(interface="virtual", channel="vright")
    try:
        node = manager.start("bridge", "vleft", extra=["vright"])
        assert node.channels == ["vleft", "vright"]
        left.send(can.Message(arbitration_id=0x321, data=b"\x07", is_extended_id=False))
        got = pump(app, right)
        assert got is not None and got.arbitration_id == 0x321
    finally:
        left.shutdown()
        right.shutdown()


def test_which_channel_a_frame_arrived_on_is_told(app, folder, manager):
    """Without it a gateway cannot tell forwards from backwards, and every
    frame it relays comes straight back at it."""
    write(
        folder,
        "noter",
        "def start(node, *, ctx): node.state.seen = []\n"
        "def on_frame(node, frame, *, ctx): node.state.seen.append(frame.channel)\n",
    )
    right = can.Bus(interface="virtual", channel="vright2")
    try:
        node = manager.start("noter", "vleft2", extra=["vright2"])
        right.send(can.Message(arbitration_id=1, data=b"", is_extended_id=False))
        spin(app, 1.0, until=lambda: bool(node.state.seen))
        assert node.state.seen == ["vright2"]
    finally:
        right.shutdown()


# --- the shipped examples ---------------------------------------------------------
def test_the_shipped_nodes_are_copied_into_the_workspace(app, ctx):
    """The same bargain as the hook defaults: they arrive as editable source
    where somebody will find them, not buried in the package."""
    SimulatedNodes(ctx)
    copied = {p.name for p in ctx.nodes_dir.glob("*.py")}
    assert {"canopen_device.py", "uds_server.py", "j1939_engine.py", "xcp_slave.py"} <= copied


def test_a_users_edit_is_never_written_over(app, ctx):
    SimulatedNodes(ctx)
    mine = ctx.nodes_dir / "canopen_device.py"
    mine.write_text("# mine now\ndef poll(node, *, ctx): pass\n", encoding="utf-8")
    SimulatedNodes(ctx)
    assert mine.read_text(encoding="utf-8").startswith("# mine now")


def test_every_shipped_node_describes_itself(app, manager):
    """A shipped example with no name, or one that does not parse, would be
    listed in the Add dialog as a filename and an error."""
    kinds = {k.id: k for k in manager.kinds()}
    assert kinds, "nothing was copied in"
    for kind in kinds.values():
        assert kind.error is None, f"{kind.id}: {kind.error}"
        assert kind.name and kind.description


@pytest.mark.parametrize("kind_id", ["j1939_engine", "xcp_slave", "uds_server"])
def test_a_shipped_node_starts_and_stops(app, manager, kind_id):
    """Imported and run, not merely parsed.  An example nobody starts is an
    example that quietly stops working."""
    node = manager.start(kind_id, f"v_{kind_id}")
    spin(app, 0.3)
    assert node.running, "it stopped itself"
    manager.stop(node)


def ask_uds(app, tester, payload: bytes):
    """One ISO-TP single-frame request, and the first frame of the answer."""
    body = bytes([len(payload)]) + payload
    tester.send(
        can.Message(
            arbitration_id=0x7E0,
            data=body + b"\xaa" * (8 - len(body)),
            is_extended_id=False,
        )
    )
    return pump(app, tester, seconds=2.0)


def test_the_uds_server_answers_a_request(app, manager):
    """The one shipped example whose whole job is to reply, so silence is a
    failure rather than a quiet moment."""
    tester = can.Bus(interface="virtual", channel="v_uds_talk")
    try:
        manager.start("uds_server", "v_uds_talk")
        got = ask_uds(app, tester, b"\x22\x01\x01")  # battery, a short answer
        assert got is not None, "the server said nothing"
        assert got.arbitration_id == 0x7E8
        assert bytes(got.data)[:2] == b"\x05\x62", "a positive response to service 0x22"
    finally:
        tester.shutdown()


def test_an_answer_too_long_for_one_frame_is_segmented(app, manager):
    """Which is the reason this example uses a real ISO-TP stack rather than
    packing single frames by hand: the VIN alone does not fit in one, and an
    ECU that could only answer in seven bytes would be a poor stand-in."""
    tester = can.Bus(interface="virtual", channel="v_uds_long")
    try:
        manager.start("uds_server", "v_uds_long")
        got = ask_uds(app, tester, b"\x22\xf1\x90")  # the VIN: 20 bytes back
        assert got is not None, "the server said nothing"
        first = bytes(got.data)
        assert first[0] >> 4 == 1, f"not a first frame: {first.hex(' ')}"
        assert ((first[0] & 0x0F) << 8) | first[1] == 20, "the length it promises"
    finally:
        tester.shutdown()


def test_the_uds_server_refuses_what_it_does_not_know(app, manager):
    """A tester tells "I will not" from "I cannot" by the code.  Silence is
    indistinguishable from a broken bus."""
    tester = can.Bus(interface="virtual", channel="v_uds_no")
    try:
        manager.start("uds_server", "v_uds_no")
        got = ask_uds(app, tester, b"\x85\x01")  # control DTC setting: not offered
        assert got is not None
        assert bytes(got.data)[1:4] == b"\x7f\x85\x11", "service not supported"
    finally:
        tester.shutdown()


def test_writing_is_refused_until_the_ecu_is_unlocked(app, manager):
    """The security exchange is most of why a demo ECU is worth having: a
    pane that can drive seed and key against something is a pane you can
    trust before you point it at a real one."""
    tester = can.Bus(interface="virtual", channel="v_uds_lock")
    try:
        manager.start("uds_server", "v_uds_lock")
        ask_uds(app, tester, b"\x10\x03")  # extended session
        got = ask_uds(app, tester, b"\x2e\x01\x02\x00\x2a")
        assert bytes(got.data)[1:4] == b"\x7f\x2e\x33", "security access denied"

        seed = ask_uds(app, tester, b"\x27\x01")
        assert bytes(seed.data)[1:3] == b"\x67\x01"
        key = bytes(b ^ 0xFF for b in bytes(seed.data)[3:7])
        assert bytes(ask_uds(app, tester, b"\x27\x02" + key).data)[1:3] == b"\x67\x02"

        got = ask_uds(app, tester, b"\x2e\x01\x02\x00\x2a")
        assert bytes(got.data)[1:2] == b"\x6e", "unlocked, and the write took"
    finally:
        tester.shutdown()


def test_the_gateway_refuses_to_start_on_one_channel(app, manager):
    """It would silently be a node that echoes everything back at whoever
    sent it, which is worse than saying no."""
    with pytest.raises(NodeError, match="two channels"):
        manager.start("gateway", "v_gw_alone")


# --- the CANopen example, which has a real protocol behind it ---------------------
def collect(app, bus, seconds=1.2):
    """Every arbitration id seen in a window."""
    from time import monotonic

    seen = set()
    deadline = monotonic() + seconds
    while monotonic() < deadline:
        app.processEvents()
        if (msg := bus.recv(timeout=0.01)) is not None:
            seen.add(msg.arbitration_id)
    return seen


HEARTBEAT = 0x705  # 0x700 + node 5
TPDO1 = 0x185
SDO_RESPONSE = 0x585
SDO_REQUEST = 0x605


@pytest.fixture
def canopen_bus(app, manager):
    """The shipped CANopen node, running, with a bus to talk to it on."""
    bus = can.Bus(interface="virtual", channel="v_canopen")
    node = manager.start("canopen_device", "v_canopen")
    yield node, bus
    bus.shutdown()


def test_the_canopen_node_produces_a_heartbeat(app, canopen_bus):
    _node, bus = canopen_bus
    assert HEARTBEAT in collect(app, bus)


def test_process_data_waits_for_the_node_to_be_operational(app, canopen_bus):
    """The NMT state is most of what it is for, and a device that transmits
    regardless makes the NMT buttons look broken."""
    _node, bus = canopen_bus
    assert TPDO1 not in collect(app, bus), "transmitting while pre-operational"

    bus.send(can.Message(arbitration_id=0x000, data=b"\x01\x05", is_extended_id=False))
    assert TPDO1 in collect(app, bus), "started, and still saying nothing"


def test_an_sdo_write_reaches_the_device_and_is_answered(app, canopen_bus):
    """Which is the whole of what a CANopen node is for: something the
    CANopen pane can read from and write to with no hardware present."""
    import struct

    node, bus = canopen_bus
    bus.send(
        can.Message(
            arbitration_id=SDO_REQUEST,
            data=b"\x2b\x01\x20\x00" + struct.pack("<h", 500) + b"\x00\x00",
            is_extended_id=False,
        )
    )
    assert SDO_RESPONSE in collect(app, bus), "the SDO server did not answer"
    # And the write did something: the speed slews toward the demand rather
    # than the value merely sitting in the dictionary.
    assert spin(app, 2.0, until=lambda: node.state.speed == 500), (
        f"speed reached {node.state.speed}, not the 500 that was demanded"
    )


# --- standing on a channel the application already has ---------------------------
def test_a_node_stands_on_the_channel_with_a_handle_of_its_own(app, folder, ctx):
    """A device has to be a separate participant.

    Sharing the application's handle looks tidier and cannot work: python-can
    marks everything this process sent as not-received, so pycangui's own
    protocol stacks -- which rightly ignore what the local handle sent --
    would never hear the node, and the node could not tell the application's
    frames from its own.  Neither side could hear the other.
    """
    write(folder, "shouter", "def poll(node, *, ctx): node.send(0x321, b'\x01')\n")
    channels = Channels()
    existing = channels.add("Rig")
    existing.connect_bus("virtual", "vrig", 500000, False)
    heard = []
    existing.frames.connect(heard.extend)
    nodes = SimulatedNodes(ctx, channels=channels)
    try:
        node = nodes.start("shouter", "Rig", rate_hz=50)
        assert node.bus() is not existing.bus, "it shared the application's handle"
        spin(app, 2.0, until=lambda: any(f.can_id == 0x321 for f in heard))
        mine = [f for f in heard if f.can_id == 0x321]
        assert mine, "the application never heard the node"
        assert mine[0].rx, "and it must look received, or every stack will drop it"
    finally:
        nodes.stop_all()
        existing.disconnect_bus()


def test_stopping_leaves_the_channel_open(app, folder, ctx):
    """It is the application's channel.  A node that closed it on the way
    out would disconnect the window."""
    write(folder, "quiet", "def on_frame(node, frame, *, ctx): pass\n")
    channels = Channels()
    existing = channels.add("Rig")
    existing.connect_bus("virtual", "vrig2", 500000, False)
    nodes = SimulatedNodes(ctx, channels=channels)
    try:
        nodes.stop(nodes.start("quiet", "Rig"))
        assert existing.is_connected, "the node closed the application's channel"
    finally:
        existing.disconnect_bus()


def test_a_channel_that_is_not_connected_is_refused(app, folder, ctx):
    """It was configured for something, quite possibly a real adapter, and
    quietly connecting it as virtual would be pycangui deciding what a named
    channel is for."""
    write(folder, "quiet", "def poll(node, *, ctx): pass\n")
    channels = Channels()
    channels.add("Rig")  # added, never connected
    nodes = SimulatedNodes(ctx, channels=channels)
    with pytest.raises(NodeError, match="not connected"):
        nodes.start("quiet", "Rig")


def test_a_node_still_invents_a_channel_nobody_has_open(app, folder, manager):
    """The case that needs nothing configured and nothing plugged in, which
    is most of why a simulated node is useful in the first place."""
    write(manager.ctx.nodes_dir, "shouter", "def poll(node, *, ctx): node.send(0x99, b'')\n")
    listening = can.Bus(interface="virtual", channel="vinvented")
    try:
        manager.start("shouter", "vinvented", rate_hz=50)
        assert pump(app, listening) is not None
    finally:
        listening.shutdown()


#: PDU format bytes.  An address claim is 0xEE and EEC1 is 0xF0.
CLAIM_PF = 0xEE
EEC1_PF = 0xF0


def pdu_format(message) -> int:
    """The PF byte of a 29-bit identifier.

    Not the whole PGN: below 240 the next byte is a destination address
    rather than part of the group, so comparing the pair would make an
    address claim to the global address look like a different message.
    """
    return (message.arbitration_id >> 16) & 0xFF


def collect_until(app, bus, wanted, seconds=6.0):
    """Frames until ``wanted`` says enough, or time runs out."""
    from time import monotonic

    seen = []
    deadline = monotonic() + seconds
    while monotonic() < deadline and not wanted(seen):
        app.processEvents()
        if (msg := bus.recv(timeout=0.01)) is not None:
            seen.append(msg)
    return seen


def test_the_j1939_engine_claims_an_address_then_broadcasts(app, manager):
    """A J1939 device that broadcast before its claim settled would be
    sending as somebody else, so the claim comes first -- and the claim
    taking a moment is why this waits for the broadcast rather than looking
    once."""
    watching = can.Bus(interface="virtual", channel="v_j1939")
    try:
        manager.start("j1939_engine", "v_j1939")
        seen = collect_until(app, watching, lambda got: any(pdu_format(m) == EEC1_PF for m in got))
        formats = [pdu_format(m) for m in seen if m.is_extended_id]
        assert CLAIM_PF in formats, f"no address claim: {[hex(f) for f in formats]}"
        assert EEC1_PF in formats, f"claimed but never broadcast: {[hex(f) for f in formats]}"
        assert formats.index(CLAIM_PF) < formats.index(EEC1_PF), "it broadcast before claiming"
    finally:
        watching.shutdown()


def test_a_node_does_not_hear_its_own_transmissions(app, folder, manager):
    """pycangui echoes what it sends so the trace can show it.  A node fed
    its own frames is a node arguing with itself -- and for a gateway it is
    an endless loop, which is the version of this that hurts."""
    write(
        folder,
        "watcher",
        "def start(node, *, ctx): node.state.heard = []\n"
        "def poll(node, *, ctx): node.send(0x7AA, b'')\n"
        "def on_frame(node, frame, *, ctx): node.state.heard.append(frame.arbitration_id)\n",
    )
    other = can.Bus(interface="virtual", channel="v_echo")
    try:
        node = manager.start("watcher", "v_echo", rate_hz=50)
        other.send(can.Message(arbitration_id=0x7BB, data=b"", is_extended_id=False))
        spin(app, 1.0, until=lambda: 0x7BB in node.state.heard)
        assert 0x7BB in node.state.heard, "it did not hear the other bus at all"
        assert 0x7AA not in node.state.heard, "it heard itself"
    finally:
        other.shutdown()


def test_the_gateway_does_not_forward_its_own_forwarding(app, manager):
    """The loop this would otherwise be: a frame crosses to the far side,
    comes back as an echo there, and is forwarded home again, for ever."""
    left = can.Bus(interface="virtual", channel="v_gwl")
    right = can.Bus(interface="virtual", channel="v_gwr")
    try:
        manager.start("gateway", "v_gwl", extra=["v_gwr"])
        left.send(can.Message(arbitration_id=0x2A, data=b"\x01", is_extended_id=False))
        seen = collect_until(app, right, lambda got: len(got) > 4, seconds=1.5)
        assert len(seen) == 1, f"one frame across became {len(seen)}"
        assert seen[0].arbitration_id == 0x2A
    finally:
        left.shutdown()
        right.shutdown()


def ask_xcp(app, bus, payload: bytes):
    """One XCP command, and the slave's answer."""
    bus.send(can.Message(arbitration_id=0x7A0, data=payload, is_extended_id=False))
    return pump(app, bus, seconds=2.0)


def test_the_xcp_slave_connects_and_uploads_a_moving_measurement(app, manager):
    """Reading the right address is the whole of XCP, and a value that never
    moves cannot tell you whether the tool read it or read zero."""
    master = can.Bus(interface="virtual", channel="v_xcp_go")
    try:
        manager.start("xcp_slave", "v_xcp_go")
        assert bytes(ask_xcp(app, master, b"\xff").data)[0] == 0xFF, "no answer to CONNECT"

        # SHORT_UPLOAD of the engine speed, whose address is little endian.
        read = b"\xf4\x02\x00\x00" + (0x1000).to_bytes(4, "little")
        first = bytes(ask_xcp(app, master, read).data)
        assert first[0] == 0xFF
        spin(app, 0.5)
        second = bytes(ask_xcp(app, master, read).data)
        assert first[1:3] != second[1:3], "the measurement never moved"
    finally:
        master.shutdown()


def test_the_xcp_slave_refuses_to_be_calibrated_until_unlocked(app, manager):
    """Reading a controller and writing new constants into a running one are
    different things, and the seed is where that line is drawn."""
    master = can.Bus(interface="virtual", channel="v_xcp_lock")
    try:
        manager.start("xcp_slave", "v_xcp_lock")
        ask_xcp(app, master, b"\xff")
        ask_xcp(app, master, b"\xf6\x00\x00\x00" + (0x2000).to_bytes(4, "little"))

        locked = bytes(ask_xcp(app, master, b"\xf0\x02\x11\x22").data)
        assert locked[0] == 0xFE, "it let a locked master write"

        seed = bytes(ask_xcp(app, master, b"\xf8\x00\x00").data)
        key = bytes(b ^ 0xFF for b in seed[2 : 2 + seed[1]])
        assert bytes(ask_xcp(app, master, bytes([0xF7, len(key)]) + key).data)[0] == 0xFF

        ask_xcp(app, master, b"\xf6\x00\x00\x00" + (0x2000).to_bytes(4, "little"))
        assert bytes(ask_xcp(app, master, b"\xf0\x02\x11\x22").data)[0] == 0xFF
    finally:
        master.shutdown()


def test_the_canopen_node_raises_and_clears_an_emergency(app, canopen_bus):
    """An EMCY is the one CANopen message a tool cannot provoke by asking
    for it.  A demo device that never raised one would leave the Emergencies
    tab with nothing to show and no way to get anything."""
    import struct

    _node, bus = canopen_bus
    emcy = 0x80 + 5

    def demand(value):
        bus.send(
            can.Message(
                arbitration_id=SDO_REQUEST,
                data=b"\x2b\x01\x20\x00" + struct.pack("<h", value) + b"\x00\x00",
                is_extended_id=False,
            )
        )

    demand(3000)  # past what the device will put up with
    seen = collect_until(app, bus, lambda got: any(m.arbitration_id == emcy for m in got), 3.0)
    raised = [m for m in seen if m.arbitration_id == emcy]
    assert raised, "no emergency at all"
    code = int.from_bytes(bytes(raised[0].data)[:2], "little")
    assert code == 0x2310, f"raised {code:#06x}, not over-current"
    assert bytes(raised[0].data)[2] == 0x02, "the error register says which kind"
    assert len(bytes(raised[0].data)) == 8, "an EMCY with an empty tail is not a real one"

    demand(0)  # back down, and it should say so
    seen = collect_until(
        app,
        bus,
        lambda got: any(
            m.arbitration_id == emcy and int.from_bytes(bytes(m.data)[:2], "little") == 0
            for m in got
        ),
        3.0,
    )
    resets = [m for m in seen if m.arbitration_id == emcy]
    assert resets and int.from_bytes(bytes(resets[-1].data)[:2], "little") == 0, "never cleared"
