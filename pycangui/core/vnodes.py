"""Virtual nodes: the rest of the bus, written in Python.

One real device is rarely testable on its own.  It expects a controller to
command it, or peers to claim addresses against, or a master to poll it, and
without them it sits in a fault state saying nothing useful.  A virtual node
is the missing half: a device pycangui pretends to be, so the real one has
something to talk to.

**This is Python you write, not a GUI you configure.**  There is no node
editor and no visual state machine.  A node is one file, and the file is the
node -- what it sends, when, and what it does about what arrives.  The GUI's
whole job is to start one on a channel and stop it again.

A node file declares who it is and implements whichever of four functions it
needs::

    NAME = "Thermistor sender"
    DESCRIPTION = "Sends a temperature that drifts."
    RATE_HZ = 10

    def start(node, *, ctx): ...            # once, before the first poll
    def poll(node, *, ctx): ...             # every 1/RATE_HZ seconds
    def on_frame(node, frame, *, ctx): ...  # a frame arrived
    def stop(node, *, ctx): ...             # once, on the way out

All four are optional -- a node that only listens implements ``on_frame``, one
that only shouts implements ``poll`` -- and all four run on the GUI thread, so
nothing in a node file has to think about locks.  The other side of that
bargain is that a node which blocks holds up the window, so a poll that wants
to take a second should take it in pieces across several polls instead.

``node`` is the instance, and is where per-node state lives: two of the same
kind on two channels get a ``node.state`` each and never see each other's.
See :class:`Node` for what else it offers.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
import traceback
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from types import SimpleNamespace
from typing import Any

import can
from PySide6.QtCore import QObject, QTimer, Signal

#: Copied into the workspace on first run, the way hook defaults are.
DEFAULTS_PACKAGE = "pycangui.nodes"

#: What a node file may declare.  Everything has a default, so a file that
#: declares nothing at all still runs -- under its own filename.
DEFAULT_RATE_HZ = 10.0
FUNCTIONS = ("start", "poll", "on_frame", "stop")

#: Slower than this and a timer is the wrong tool; faster and the GUI thread
#: is the wrong place.  Both ends are held to rather than warned about,
#: because a typo in a rate should not be a frozen window.
MIN_RATE_HZ = 0.01
MAX_RATE_HZ = 1000.0

#: What a node opens for itself when the application has no bus on that
#: channel.  python-can's virtual buses rendezvous by name inside one
#: process, which is the whole reason a node can exist with nothing plugged
#: in.  Where the application *does* have the channel open -- any adapter,
#: real or virtual -- the node joins that bus instead of opening a second;
#: see Node._open.
INTERFACE = "virtual"

#: Given to a virtual bus, which ignores it.  Named rather than repeated so
#: that nobody reads 500000 here and thinks it means anything.
VIRTUAL_BITRATE = 500000

#: How many of a node's own sends to remember, and for how long, so that
#: the echo of one can be told from the application transmitting the same
#: bytes.  Both are generous: the echo of a frame arrives within
#: milliseconds, and anything older is not an echo.
ECHO_MEMORY = 256
ECHO_SECONDS = 2.0


class NodeError(RuntimeError):
    """A node could not be started.  The message says why, for a user."""


@dataclass
class Kind:
    """One node file, described without running a line of it.

    Read by parsing rather than importing, so a file with a syntax error in it
    is still listed -- with its error -- instead of vanishing from the dialog
    and leaving somebody to wonder where their node went.
    """

    id: str  # the filename without .py, and how a node is asked for
    path: Path
    name: str = ""
    description: str = ""
    rate_hz: float = DEFAULT_RATE_HZ
    #: What is wrong with it, if anything.  Listed anyway when there is.
    error: str | None = None

    @property
    def label(self) -> str:
        return self.name or self.id


def describe(path: Path) -> Kind:
    """What a node file says it is, without importing it."""
    kind = Kind(id=path.stem, path=path)
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        kind.error = str(exc)
        return kind
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        kind.error = f"line {exc.lineno}: {exc.msg}"
        return kind

    found: dict[str, Any] = {}
    defined: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            defined.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    try:
                        found[target.id] = ast.literal_eval(node.value)
                    except ValueError:
                        pass  # computed at run time; it can have its default
    kind.name = str(found.get("NAME", "") or "")
    kind.description = str(found.get("DESCRIPTION", "") or "")
    rate = found.get("RATE_HZ", DEFAULT_RATE_HZ)
    kind.rate_hz = float(rate) if isinstance(rate, (int, float)) else DEFAULT_RATE_HZ
    if not defined & set(FUNCTIONS):
        kind.error = (
            "defines none of " + ", ".join(FUNCTIONS) + ", so there is nothing for it to do."
        )
    return kind


def kinds_in(folder: Path) -> list[Kind]:
    """Every node file in a folder, in a settled order."""
    if not folder.is_dir():
        return []
    return [describe(p) for p in sorted(folder.glob("*.py")) if not p.name.startswith("_")]


class Node(QObject):
    """One running virtual node: its channel, its state and its way out.

    Handed to every function in the node file as the first argument.  What a
    node file keeps between calls goes in ``state``; what it sends goes
    through ``send``; and a CANopen node gets the whole SDO and heartbeat
    server from ``canopen()`` rather than building one by hand.
    """

    #: A frame arrived, already back on the GUI thread.  Internal: a node file
    #: implements ``on_frame`` and never sees this.
    _received = Signal(object)

    def __init__(
        self,
        kind: Kind,
        channel: str,
        rate_hz: float,
        ctx,
        functions: dict[str, Any],
        buses: dict,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.kind = kind
        self.channel = channel
        #: Every channel this node is on.  One for an ordinary node; a gateway
        #: is the same thing bound to more than one, which is why this is a
        #: list even in the common case.
        self.channels: list[str] = [channel]
        self.rate_hz = max(MIN_RATE_HZ, min(MAX_RATE_HZ, float(rate_hz)))
        self.ctx = ctx
        #: Yours.  pycangui never reads it.
        self.state = SimpleNamespace()

        self._functions = functions
        #: The application's channels this node stands on, by name.  Used
        #: to learn what to open and never closed here: they belong to the
        #: window, and a node that closed one would disconnect it.
        self._channels: dict[str, Any] = dict(buses)
        #: This node's own handle on each of those channels, and its reader.
        #: A device needs a handle of its own to be a separate participant:
        #: sharing the application's means neither can tell the other's
        #: frames from its own, and pycangui's protocol stacks correctly
        #: ignore what the local handle sent -- so a master and a simulated
        #: device on one handle cannot talk at all.
        self._buses: dict[str, can.BusABC] = {}
        self._notifiers: dict[str, can.Notifier] = {}
        #: Channels where a second handle was refused and the application's
        #: is being shared.  Those are not ours to close.
        self._shared: set[str] = set()
        #: What this node has sent and not yet heard back, so its own
        #: echoes can be told from the application's traffic on the same
        #: handle.  A deque because the match is by content and the oldest
        #: one is the right one to claim; short-lived, because a bus that
        #: does not echo at all must not fill it.
        self._sent_echoes: deque[tuple[tuple, float]] = deque(maxlen=ECHO_MEMORY)
        #: (channel, listener) for everything this node put on a channel's
        #: notifier -- its own frame forwarder, and any CANopen server it
        #: built.  A list of pairs because one channel can carry several.
        self._listeners: list[tuple[str, can.Listener]] = []
        self._networks: list[Any] = []
        self._timer: QTimer | None = None
        self._running = False

    # --- what a node file may use -------------------------------------------
    @property
    def name(self) -> str:
        return self.kind.label

    def channel_bus(self, channel: str | None = None):
        """The application's channel this node is standing on."""
        wanted = channel or self.channel
        if (manager := self._channels.get(wanted)) is None:
            raise NodeError(f"{self.name} is not on channel {wanted!r}")
        return manager

    def bus(self, channel: str | None = None) -> can.BusABC:
        """This node's own handle on that channel."""
        wanted = channel or self.channel
        if (bus := self._buses.get(wanted)) is None:
            raise NodeError(f"{self.name} is not on channel {wanted!r}")
        return bus

    def listen(self, listener: can.Listener, channel: str | None = None) -> None:
        """Put a listener on this node's reader, and take it off at stop.

        Use this rather than reaching for the notifier: what arrives here is
        only what came from somewhere else -- see :class:`_Received` -- and
        it is removed again when the node stops without anybody remembering
        to.
        """
        wanted = channel or self.channel
        wrapped = _Received(self, listener)
        self._listeners.append((wanted, wrapped))
        self.notifier(wanted).add_listener(wrapped)

    def notifier(self, channel: str | None = None) -> can.Notifier:
        """This node's frame reader, for a protocol stack that wants one.

        ISO-TP is the case: ``isotp.NotifierBasedCanStack`` takes a bus and a
        notifier and does the segmenting, which is a great deal better than a
        node file reassembling multi-frame messages by hand.
        """
        wanted = channel or self.channel
        if (notifier := self._notifiers.get(wanted)) is None:
            raise NodeError(f"{self.name} is not on channel {wanted!r}")
        return notifier

    def send(
        self,
        message: can.Message | int,
        data: bytes | None = None,
        *,
        channel: str | None = None,
        extended: bool = False,
    ) -> None:
        """Put a frame on the bus.

        Either a ready-made ``can.Message``, or an id and its bytes, which is
        what most node files want and saves them importing ``can`` to say it.
        ``channel`` picks which bus for a node bound to several; without it,
        the one the node was started on.
        """
        if isinstance(message, can.Message):
            frame = message
        else:
            frame = can.Message(arbitration_id=message, data=data or b"", is_extended_id=extended)
        self._sent_echoes.append((_signature(frame), monotonic()))
        self.bus(channel).send(frame)

    def claim_echo(self, msg: can.Message) -> bool:
        """Whether this frame is one this node sent, coming back.

        Matched by content rather than by ``is_rx``, because on a shared
        handle that flag cannot tell this node's frames from the
        application's -- see :class:`_Received`.  Stale entries are dropped
        as we go, so a bus that never echoes does not fill the record and a
        frame that looks like a very old send is not mistaken for one.
        """
        if msg.is_rx:
            return False  # plainly somebody else's
        now = monotonic()
        while self._sent_echoes and now - self._sent_echoes[0][1] > ECHO_SECONDS:
            self._sent_echoes.popleft()
        signature = _signature(msg)
        for index, (candidate, _when) in enumerate(self._sent_echoes):
            if candidate == signature:
                del self._sent_echoes[index]
                return True
        return False

    def canopen(self, eds: str | Path, node_id: int, channel: str | None = None):
        """A CANopen server on this node's bus: SDO, heartbeat, NMT, PDO.

        Every CANopen node wants the same several hundred lines of it, so it
        is here rather than in each node file.  Returns the
        ``canopen.LocalNode``: set an object with ``node.set_data(...)``, read
        one with ``get_data``, and drive its PDOs through ``node.tpdo``.

        Imported here rather than at the top of the module: a node file that
        speaks J1939 or raw frames should not pay for the CANopen stack.
        """
        import canopen

        wanted = channel or self.channel
        network = canopen.Network(bus=self.bus(wanted))
        local = network.create_node(node_id, str(eds))
        self._networks.append(network)
        # Onto this node's own reader.  A second notifier on one bus races
        # the first for every frame and each gets about half, which looks
        # like a device that answers every other request.
        for listener in network.listeners:
            self.listen(listener, wanted)
        network.notifier = self.notifier(wanted)
        return local

    def log(self, message: str) -> None:
        self.ctx.log(f"{self.name}: {message}")

    # --- lifecycle, which is pycangui's business ----------------------------
    def start(self) -> None:
        if self._running:
            return
        for channel in self.channels:
            self._open(channel)
        self._received.connect(self._deliver)
        self._running = True
        # Not _call(): a node that cannot set itself up has not started, and
        # whoever asked for it is owed the reason rather than a line in the
        # log and an entry in the running list that is doing nothing.
        if (setup := self._functions.get("start")) is not None:
            try:
                setup(self, ctx=self.ctx)
            except Exception:
                self.stop()
                raise
        if "poll" in self._functions:
            interval = max(1, round(1000.0 / self.rate_hz))
            self._timer = QTimer(self, interval=interval, timeout=lambda: self._call("poll"))
            self._timer.start()

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False  # first: a teardown that raises must not run twice
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        self._call("stop")
        # No notifiers to stop: a node never owns one.  It puts listeners on
        # the channel's notifier and takes them off again below.
        self._networks.clear()
        for channel, listener in self._listeners:
            try:
                self._notifiers[channel].remove_listener(listener)
            except Exception:  # the channel may have gone already
                pass
        self._listeners.clear()
        for channel, notifier in self._notifiers.items():
            if channel in self._shared:
                continue  # the application's reader, not ours to stop
            try:
                notifier.stop()
            except Exception:
                pass
        for channel, bus in self._buses.items():
            if channel in self._shared:
                continue  # ditto: closing it would disconnect the window
            try:
                bus.shutdown()
            except Exception:
                pass
        self._notifiers.clear()
        self._buses.clear()
        self._channels.clear()
        self._shared.clear()

    @property
    def running(self) -> bool:
        return self._running

    # --- the plumbing underneath --------------------------------------------
    def _open(self, channel: str) -> None:
        """Take this node's own handle on a pycangui channel.

        The channel says which interface and which adapter channel; the
        node opens a second handle on the same one.  That is what makes it
        a separate participant rather than part of the application: on a
        shared handle python-can marks everything this process sent as not
        received, so the application's stacks ignore the node's frames and
        the node cannot tell the application's from its own.  Neither side
        can hear the other, which is the opposite of the point.

        A real adapter may refuse a second handle -- several drivers do --
        and then the node shares the application's.  It still works against
        equipment out on the bus; what it cannot do is talk to pycangui's
        own protocol panes, and it says so rather than being quietly deaf.
        """
        manager = self._channels[channel]
        interface = manager.interface or INTERFACE
        where = manager.channel or channel
        try:
            bus = can.Bus(interface=interface, channel=where)
            self._buses[channel] = bus
            self._notifiers[channel] = can.Notifier(bus, [], timeout=0.02)
        except Exception as exc:
            if manager.bus is None or manager.notifier is None:
                raise NodeError(f"{self.name}: cannot get onto {channel}: {exc}") from exc
            self.ctx.warn(
                f"{self.name}: {interface} would not give a second handle on {channel} "
                f"({exc}).  Sharing the application's, so pycangui's own protocol panes "
                "will not see this node -- equipment on the bus still will."
            )
            self._buses[channel] = manager.bus
            self._notifiers[channel] = manager.notifier
            self._shared.add(channel)
        if "on_frame" in self._functions:
            self.listen(_Forwarder(self, channel), channel)

    def _deliver(self, frame: can.Message) -> None:
        """A frame, on the GUI thread, on its way to the node file."""
        if self._running:
            self._call("on_frame", frame)

    def _call(self, what: str, *args: Any) -> None:
        """Run one of the node's functions, and survive whatever it does.

        A node file is somebody's work in progress.  One that raises says so
        in the Event Log and stops -- rather than raising once every hundred
        milliseconds for the rest of the session, which fills the log with one
        message and hides everything else in it.
        """
        fn = self._functions.get(what)
        if fn is None:
            return
        try:
            fn(self, *args, ctx=self.ctx)
        except Exception:
            self.ctx.error(
                f"Virtual node {self.name} raised in {what}() and has been stopped:\n"
                + traceback.format_exc()
            )
            if what != "stop":
                self.stop()


def _signature(msg: can.Message) -> tuple:
    """What makes two frames the same frame, for spotting an echo."""
    return (msg.arbitration_id, bool(msg.is_extended_id), bytes(msg.data))


class _Received(can.Listener):
    """Passes on everything except what this node itself sent.

    pycangui opens every bus with ``receive_own_messages`` so the trace can
    show what the tool transmitted, and a node shares that bus.  It must not
    hear *itself*: a J1939 stack takes its own address claim for a contender
    and fights itself for ever, and a gateway forwards its own forwarded
    frame straight back, also for ever.

    ``is_rx`` is not the test, though it looks like it.  On a shared handle
    it is false for everything this process sent -- the application's own
    transmissions included -- and a node deaf to the application is a node
    that cannot answer an SDO.  So the node keeps note of what it sent and
    claims those echoes back, and anything else is somebody else's.
    """

    def __init__(self, node: Node, inner: can.Listener) -> None:
        self._node = node
        self._inner = inner

    def on_message_received(self, msg: can.Message) -> None:
        if not self._node.claim_echo(msg):
            self._inner.on_message_received(msg)

    def on_error(self, exc: Exception) -> None:
        pass


class _Forwarder(can.Listener):
    """Moves a frame off the reader thread and onto the GUI thread.

    python-can reads on a thread of its own, and a node file called from there
    could touch a widget and take the process down with it.  The signal is a
    queued connection, so ``on_frame`` runs where every other hook runs.
    """

    def __init__(self, node: Node, channel: str) -> None:
        self._node = node
        self._channel = channel

    def on_message_received(self, msg: can.Message) -> None:
        msg.channel = self._channel  # which bus, for a node bound to several
        self._node._received.emit(msg)

    def on_error(self, exc: Exception) -> None:
        pass


class VirtualNodes(QObject):
    """The node files there are, and the ones that are running.

    Reachable from the console and from a startup hook as ``window.vnodes``,
    which is the point: starting the rest of the bus is setup, and setup
    belongs in a file rather than in somebody's fingers every morning.
    """

    changed = Signal()  # something started or stopped

    def __init__(
        self, ctx, channels=None, may_transmit=None, parent: QObject | None = None
    ) -> None:
        super().__init__(parent)
        self.ctx = ctx
        #: The application's channels.  A node stands on one of these like
        #: everything else does -- there is one notion of a bus in pycangui
        #: and this is it.  A manager given none makes its own, which is what
        #: a test wants and what nothing else should.
        if channels is None:
            from pycangui.core.channels import Channels

            channels = Channels()
        self.channels = channels
        #: Asked before a node goes onto real equipment.  A node transmits,
        #: and transmitting onto a real bus is the thing pycangui asks about
        #: everywhere else; a node quietly joining one would be the hole in
        #: that.
        self._may_transmit = may_transmit or (lambda _channels: True)
        self._running: list[Node] = []
        self.ensure_user_files()

    # --- the files ----------------------------------------------------------
    def ensure_user_files(self) -> list[Path]:
        """Copy any shipped node the user does not have yet.

        The same bargain as the hook defaults: they arrive as editable,
        commented source in the workspace rather than staying buried in the
        package, because the first thing anybody does with an example is
        change it.
        """
        import shutil
        from importlib import resources

        created = []
        try:
            shipped = resources.files(DEFAULTS_PACKAGE)
        except ModuleNotFoundError:  # pragma: no cover - only if the package is dropped
            return created
        for entry in shipped.iterdir():
            if not entry.name.endswith(".py") or entry.name.startswith("_"):
                continue
            dest = self.ctx.nodes_dir / entry.name
            if not dest.exists():
                shutil.copy(str(entry), dest)
                created.append(dest)
        return created

    def kinds(self) -> list[Kind]:
        return kinds_in(self.ctx.nodes_dir)

    def kind(self, kind_id: str) -> Kind | None:
        for kind in self.kinds():
            if kind.id == kind_id:
                return kind
        return None

    # --- running them -------------------------------------------------------
    def running(self) -> list[Node]:
        return list(self._running)

    def start(
        self, kind_id: str, channel: str, rate_hz: float | None = None, extra: list[str] = ()
    ) -> Node:
        """Start one node, and return it.  Raises NodeError with a reason.

        ``extra`` names further channels for a gateway: the node opens all of
        them, ``on_frame`` says which one a frame arrived on, and ``send``
        takes its pick.
        """
        kind = self.kind(kind_id)
        if kind is None:
            raise NodeError(f"There is no virtual node called {kind_id!r} in this workspace.")
        if kind.error:
            raise NodeError(f"{kind.label}: {kind.error}")
        wanted = list(dict.fromkeys([channel, *extra]))
        buses = {name: self._channel(name) for name in wanted}
        if not self._may_transmit(wanted):
            raise NodeError("Not started: putting a node onto that bus was not agreed.")
        functions = self._import(kind)
        node = Node(
            kind,
            channel,
            rate_hz or kind.rate_hz,
            self.ctx,
            functions,
            buses,
            parent=self,
        )
        node.channels = wanted
        try:
            node.start()
        except Exception as exc:
            # Listed as running while doing nothing is the worst of the
            # outcomes here: it looks started, and stopping it changes
            # nothing.  It never reaches the list.
            raise NodeError(f"{kind.label} would not start: {exc}") from exc
        self._running.append(node)
        self.ctx.log(
            f"Virtual node {node.name} started on {', '.join(node.channels)} at {node.rate_hz:g} Hz"
        )
        self.changed.emit()
        return node

    def stop(self, node: Node) -> None:
        if node in self._running:
            self._running.remove(node)
        node.stop()
        self.ctx.log(f"Virtual node {node.name} stopped")
        self.changed.emit()

    def stop_all(self) -> None:
        """Every node, on the way out.  A virtual node holding a bus open
        after the window has gone is a process that will not exit."""
        for node in list(self._running):
            node.stop()
        self._running.clear()
        self.changed.emit()

    def _channel(self, name: str):
        """The channel a node is to stand on, made if it is a new name.

        Three cases, and the rule is the least surprising one for each:

        * a name pycangui does not know is **created and connected as a
          virtual channel**.  Asking for a node on a channel that does not
          exist is asking for that channel, and the alternative -- a node
          transmitting into a bus nothing else can see -- is a node that
          appears to do nothing for reasons nothing on screen explains.
        * a channel that is **connected** is joined, whatever it is on.
        * a channel that exists and is **not connected** is refused.  It was
          configured for something, quite possibly a real adapter, and
          quietly connecting it as virtual would be pycangui deciding what a
          named channel is for.
        """
        if not name:
            raise NodeError("A node needs a channel to stand on.")
        bus = self.channels.get(name)
        if bus is None:
            bus = self.channels.add(name)
            bus.connect_bus(INTERFACE, name, VIRTUAL_BITRATE, False)
            if not bus.is_connected:
                raise NodeError(f"Could not open a virtual channel called {name!r}.")
            self.ctx.log(f"Channel {name} added as a virtual bus for a node to stand on")
            return bus
        if not bus.is_connected:
            raise NodeError(
                f"Channel {name} is not connected.  Connect it first -- a node stands "
                "on a channel rather than opening one of its own."
            )
        return bus

    def _import(self, kind: Kind) -> dict[str, Any]:
        """Load a node file and take the four functions out of it."""
        saved = sys.dont_write_bytecode
        sys.dont_write_bytecode = True  # no __pycache__ in somebody's workspace
        try:
            spec = importlib.util.spec_from_file_location(
                f"pycangui_user_nodes.{kind.id}", kind.path
            )
            if spec is None or spec.loader is None:
                raise NodeError(f"{kind.path} cannot be loaded as Python.")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except NodeError:
            raise
        except Exception as exc:
            raise NodeError(f"{kind.label} failed to load:\n{traceback.format_exc()}") from exc
        finally:
            sys.dont_write_bytecode = saved
        return {name: fn for name in FUNCTIONS if callable(fn := getattr(module, name, None))}
