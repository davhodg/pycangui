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
from dataclasses import dataclass
from pathlib import Path
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

#: Virtual channels rendezvous by name inside one process, which is the whole
#: reason a node can exist without hardware.  A real adapter is a different
#: question -- a second open handle on one physical channel is backend
#: dependent, and disturbing real equipment is something pycangui asks about
#: rather than does -- so for now a node runs on a virtual channel only.
INTERFACE = "virtual"

NOT_VIRTUAL = (
    "Virtual nodes run on virtual channels.  {channel!r} is a real adapter, "
    "and standing a second device on it is not something to do quietly."
)


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
        self._buses: dict[str, can.BusABC] = {}
        self._notifiers: dict[str, can.Notifier] = {}
        self._networks: list[Any] = []
        self._timer: QTimer | None = None
        self._running = False

    # --- what a node file may use -------------------------------------------
    @property
    def name(self) -> str:
        return self.kind.label

    def bus(self, channel: str | None = None) -> can.BusABC:
        """The python-can bus this node is holding open on a channel."""
        wanted = channel or self.channel
        if (bus := self._buses.get(wanted)) is None:
            raise NodeError(f"{self.name} is not on channel {wanted!r}")
        return bus

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
        self.bus(channel).send(frame)

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
        # One notifier per bus, shared.  Two of them reading the same bus race
        # for every frame and each gets about half, which looks like a device
        # that answers every other request.
        notifier = self._notifier_for(wanted)
        for listener in network.listeners:
            notifier.add_listener(listener)
        network.notifier = notifier
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
        for notifier in self._notifiers.values():
            try:
                notifier.stop()
            except Exception:  # a bus already gone is not worth a traceback
                pass
        self._notifiers.clear()
        self._networks.clear()
        for bus in self._buses.values():
            try:
                bus.shutdown()
            except Exception:
                pass
        self._buses.clear()

    @property
    def running(self) -> bool:
        return self._running

    # --- the plumbing underneath --------------------------------------------
    def _open(self, channel: str) -> None:
        self._buses[channel] = can.Bus(interface=INTERFACE, channel=channel)
        if "on_frame" in self._functions:
            self._notifier_for(channel).add_listener(_Forwarder(self, channel))

    def _notifier_for(self, channel: str) -> can.Notifier:
        """The one notifier on this channel, made on first use."""
        if (notifier := self._notifiers.get(channel)) is None:
            notifier = can.Notifier(self.bus(channel), [], timeout=0.02)
            self._notifiers[channel] = notifier
        return notifier

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

    def __init__(self, ctx, is_virtual=None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        #: Asked whether a channel name is a virtual one.  Injected so this
        #: does not have to know about Channels, and so a test can say.
        self._is_virtual = is_virtual or (lambda _name: True)
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
        wanted = [channel, *extra]
        for name in wanted:
            if not self._is_virtual(name):
                raise NodeError(NOT_VIRTUAL.format(channel=name))
        functions = self._import(kind)
        node = Node(kind, channel, rate_hz or kind.rate_hz, self.ctx, functions, parent=self)
        node.channels = list(dict.fromkeys(wanted))
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
