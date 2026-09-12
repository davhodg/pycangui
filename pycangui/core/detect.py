"""Finding out what adapters are plugged in, and how to name one exactly.

Three problems, all of which made connecting to real hardware guesswork:

* **What do I type?**  A channel is "can0" on socketcan, "PCAN_USBBUS1" on a
  PEAK, and plain "0" on an IXXAT.  Most backends can enumerate what is
  actually attached, so ask them rather than expecting the user to know.

* **Which one of them?**  Channel numbers are per adapter, so two IXXAT
  dongles both report channels 0 and 1, and only ``unique_hardware_id`` tells
  them apart.  Vector uses a serial number, neoVI likewise.  A channel on its
  own therefore cannot identify a device, and whichever the driver happened to
  pick would be the one you got.  So the whole configuration a backend reports
  is carried through to ``can.Bus``, which is what python-can intends -- its
  detected configurations are documented as being passed back verbatim.

* **What type is it?**  ``can.Bus`` hands the channel straight to the backend,
  and the backends disagree: IXXAT and Kvaser declare ``channel: int``,
  socketcan and PCAN declare ``str``.  A "0" typed into a text box is a
  string, and an IXXAT given a string does not open.  The type is read from
  the backend's own signature, so a new or changed backend needs no edit here;
  the small table below is only for backends that cannot be imported at all on
  this platform, such as a Windows-only vendor driver seen from Linux.
"""

from __future__ import annotations

import importlib
import inspect
from dataclasses import dataclass, field

import can
import can.interfaces

DETECT_TIMEOUT_S = 5.0

#: Backends that declare ``channel: int``, as a fallback for when the backend
#: cannot be imported and so cannot be asked.  Reading the signature is the
#: primary route and keeps working as python-can changes; this is only reached
#: when the module will not load at all -- a Windows-only vendor driver on
#: Linux, say -- which is also a case where connecting could not work anyway.
INT_CHANNEL_BACKENDS = frozenset({"cantact", "ixxat", "kvaser"})

#: Channels worth offering when an interface cannot enumerate its own.  Not
#: a claim that these exist -- they are the conventional names, so that the
#: box is something to choose from rather than something to guess at.
SUGGESTIONS = {
    "socketcan": ("can0", "can1", "vcan0"),
    "socketcand": ("can0", "can1"),
    "ixxat": ("0", "1", "2", "3"),
    "kvaser": ("0", "1", "2", "3"),
    "cantact": ("0", "1"),
    "neousys": ("0", "1"),
    "systec": ("0", "1"),
    "pcan": ("PCAN_USBBUS1", "PCAN_USBBUS2", "PCAN_USBBUS3", "PCAN_USBBUS4"),
    "vector": ("0", "1", "2", "3"),
    "udp_multicast": ("225.0.0.1",),
}

#: The channel the demo device runs on.  Selecting it is what starts the
#: device: a channel that says what is on it beats a separate switch
#: somewhere else that you have to know about.
DEMO_CHANNEL = "vcan0"

#: The virtual channels, and what each one carries.  A fixed set rather than
#: whatever the backend reports: python-can's virtual bus lists the channels
#: in use plus one random unused name, which is a different name every time,
#: is never the one the demo runs on, and means nothing to anybody.
VIRTUAL_CHANNELS = (
    (DEMO_CHANNEL, "Demo device: CANopen, UDS, J1939, XCP"),
    ("vcan1", "empty"),
    ("vcan2", "empty"),
)

#: Backends that are a serial port underneath, so the ports themselves are
#: the useful suggestion.  pyserial is a declared dependency (python-can does
#: not require it, but the slcan and serial backends do not work without it).
SERIAL_BACKENDS = frozenset({"slcan", "serial", "robotell", "seeedstudio", "usb2can"})

#: Keys that say *which device*, rather than which channel on it.  Used only
#: to build a readable label; every reported key is passed to the backend
#: whether it is listed here or not.  hw_type and the various index fields
#: are deliberately absent: "55" identifies nothing to a human.
IDENTITY_KEYS = ("unique_hardware_id", "serial", "device", "vid", "pid")

#: Longest value worth putting in a label.  Backends report objects as well
#: as numbers -- Vector hands back its entire channel configuration, whose
#: repr runs to a dozen lines -- and a label is for recognising a device,
#: not for describing it exhaustively.
MAX_LABEL_VALUE = 40


@dataclass(frozen=True)
class Channel:
    """One channel an interface reports as being present."""

    #: Everything the backend reported, minus "interface".  Passed to can.Bus
    #: as keyword arguments, so a second dongle is addressed unambiguously.
    config: dict = field(default_factory=dict)
    label: str = ""

    @property
    def value(self) -> object:
        return self.config.get("channel", "")

    @property
    def text(self) -> str:
        return str(self.value)

    @property
    def extra(self) -> dict:
        """The configuration apart from the channel itself."""
        return {k: v for k, v in self.config.items() if k != "channel"}


def channel_annotation(interface: str) -> str:
    """The declared annotation of the backend's ``channel`` parameter.

    Returns "" when the backend cannot be imported or says nothing, in which
    case nothing is assumed and the text is passed through as typed.
    """
    entry = can.interfaces.BACKENDS.get(interface)
    if entry is None:
        return ""
    module_name, class_name = entry
    try:
        bus_class = getattr(importlib.import_module(module_name), class_name)
        parameter = inspect.signature(bus_class.__init__).parameters.get("channel")
    except Exception:  # a backend whose vendor library is absent, and worse
        return ""
    if parameter is None or parameter.annotation is inspect.Parameter.empty:
        return ""
    return str(parameter.annotation)


#: Backends that take ``fd`` or ``data_bitrate`` but cannot be imported on
#: the machine doing the asking -- no vendor driver here, so no signature to
#: read.  Same reason as INT_CHANNEL_BACKENDS above: a Linux build must not
#: decide that a Windows-only adapter has no FD support.
FD_BACKENDS = frozenset({"ixxat", "vector", "socketcan", "nixnet", "udp_multicast"})
DATA_BITRATE_BACKENDS = frozenset({"ixxat", "vector"})


def bus_parameters(interface: str) -> frozenset[str]:
    """The parameters a backend's Bus declares, or nothing if it cannot be asked."""
    try:
        module_name, class_name = can.interfaces.BACKENDS[interface]
        bus_class = getattr(importlib.import_module(module_name), class_name)
        return frozenset(inspect.signature(bus_class.__init__).parameters)
    except Exception:  # the vendor library is not installed here
        return frozenset()


def takes_fd(interface: str) -> bool:
    """Whether python-can can put this backend into FD mode from a keyword.

    Several cannot.  pcan, kvaser and slcan take no ``fd`` at all: FD is
    expressed to them as a ``can.BitTimingFd``, which needs the controller's
    clock frequency and so cannot be guessed from a bitrate.  Passing fd=True
    to one of those is swallowed by its **kwargs and the channel opens as
    classic CAN, which is worth saying out loud rather than discovering from
    the traffic.
    """
    parameters = bus_parameters(interface)
    if parameters:
        return "fd" in parameters or "data_bitrate" in parameters
    return interface in FD_BACKENDS


def takes_data_bitrate(interface: str) -> bool:
    """Whether the data phase rate can be set from here.

    Only ixxat and vector.  socketcan takes fd=True but its data rate comes
    from ``ip link`` rather than from python-can.
    """
    parameters = bus_parameters(interface)
    if parameters:
        return "data_bitrate" in parameters
    return interface in DATA_BITRATE_BACKENDS


def channel_parameter(interface: str):
    """The backend's ``channel`` parameter, or None if it has none."""
    entry = can.interfaces.BACKENDS.get(interface)
    if entry is None:
        return None
    module_name, class_name = entry
    try:
        bus_class = getattr(importlib.import_module(module_name), class_name)
        return inspect.signature(bus_class.__init__).parameters.get("channel")
    except Exception:  # a backend whose vendor library is absent, and worse
        return None


def takes_a_channel(interface: str) -> bool:
    """Whether this interface has a channel to speak of at all.

    A backend with no ``channel`` parameter has nothing to choose, so the box
    is better empty and disabled than inviting a value that is thrown away.
    Unknown or unimportable interfaces are given the benefit of the doubt.
    """
    if interface not in can.interfaces.BACKENDS:
        return True
    parameter = channel_parameter(interface)
    return parameter is not None or not channel_annotation(interface) == ""


def channel_default(interface: str) -> str:
    """The channel the backend itself falls back on, if it declares one.

    Better than anything written down here: PCAN says PCAN_USBBUS1 and NI-XNET
    says CAN1 in their own signatures, and they will not drift.
    """
    parameter = channel_parameter(interface)
    if parameter is None or parameter.default in (inspect.Parameter.empty, None, ""):
        return ""
    return str(parameter.default)


def coerce_channel(interface: str, channel: object) -> object:
    """Turn a typed-in channel into what the backend actually expects.

    Only ever converts digits to an int, and only when the backend says it
    takes one: an interface that wants "can0" keeps its string, and a name
    that is not a number is never mangled into one.  A channel that came from
    detection is already the right type and passes through untouched.
    """
    if not isinstance(channel, str):
        return channel
    text = channel.strip()
    if not text.lstrip("-").isdigit():
        return text
    annotation = channel_annotation(interface)
    if annotation:
        return int(text) if "int" in annotation else text
    return int(text) if interface in INT_CHANNEL_BACKENDS else text


def readable(value) -> str:
    """A short piece of text for a reported value, or "" if it has none.

    A backend may report an object rather than a number.  Vector reports the
    whole VectorChannelConfig, and printing it gives a dozen lines of ctypes
    enums -- but it carries a name, "VN1610 Channel 1", which is exactly the
    part worth showing.  So: scalars if they are short, otherwise a name if
    there is one, otherwise nothing.
    """
    if isinstance(value, (str, int, float, bool)):
        text = str(value)
        return text if len(text) <= MAX_LABEL_VALUE else ""
    name = getattr(value, "name", None)
    if isinstance(name, str) and 0 < len(name) <= MAX_LABEL_VALUE:
        return name
    return ""


def summarise(config: dict) -> str:
    """The few words that tell one adapter from another.

    Not everything the backend said: a serial number and a product name
    identify a device, while channel_index=0 and supports_fd=True describe one
    without distinguishing it, and the configuration object behind them is a
    paragraph.  The full configuration still goes to can.Bus -- this is only
    what to call it.
    """
    parts = []
    for key in IDENTITY_KEYS:
        if text := readable(config.get(key)):
            parts.append(text)
    # Names carried by the objects a backend reports, whatever they are called.
    for key, value in sorted(config.items()):
        if key in ("channel", "interface", *IDENTITY_KEYS):
            continue
        if not isinstance(value, (str, int, float, bool)) and (text := readable(value)):
            parts.append(text)
    return ", ".join(dict.fromkeys(parts))  # in order, without repeats


def describe(config: dict) -> str:
    """A label for one detected channel, leading with what distinguishes it."""
    channel = config.get("channel", "")
    detail = summarise(config)
    return f"{channel}  ({detail})" if detail else str(channel)


def detect_channels(interface: str, timeout: float = DETECT_TIMEOUT_S) -> list[Channel]:
    """The channels this interface reports, best effort.

    Enumerating adapters is not joining a bus: nothing is transmitted and no
    bitrate is applied.  Backends that cannot enumerate return nothing rather
    than failing, and so does this.
    """
    try:
        configs = can.detect_available_configs(interface, timeout=timeout)
    except Exception:  # a missing vendor DLL raises from inside the backend
        return []
    found = []
    for config in configs:
        if "channel" not in config:
            continue
        kept = {key: value for key, value in config.items() if key != "interface"}
        found.append(Channel(config=kept, label=describe(config)))
    return found


def serial_ports() -> list[str]:
    """The serial ports on this machine, for the adapters that are one."""
    try:
        from serial.tools import list_ports
    except Exception:  # pyserial missing: not fatal, there is just nothing to add
        return []
    return [port.device for port in list_ports.comports()]


def channel_suggestions(interface: str) -> list[Channel]:
    """Plausible channel names for an interface, when it cannot say itself."""
    if interface == "virtual":
        return [
            Channel(config={"channel": name}, label=f"{name}  ({what})")
            for name, what in VIRTUAL_CHANNELS
        ]
    names = SUGGESTIONS.get(interface, ())
    if interface in SERIAL_BACKENDS:
        names = tuple(serial_ports()) or names
    # The backend's own default is the most likely right answer where we have
    # no opinion -- PCAN says PCAN_USBBUS1 in its own signature, and that will
    # not drift.  Where there are curated names it goes after them: vcan0 is
    # what the demo device and the default settings use, so for the virtual
    # bus it beats the backend's "channel-0".
    if (declared := channel_default(interface)) and declared not in names:
        names = (*names, declared) if names else (declared,)
    return [Channel(config={"channel": name}, label=name) for name in names]


def channels_for(interface: str, timeout: float = DETECT_TIMEOUT_S) -> list[Channel]:
    """What to put in the channel drop-down: what is there, then what is usual.

    Suggestions only fill in where detection came back empty, so a real
    adapter is never buried under invented names -- with two dongles attached
    a bare "2" would say nothing about which device it meant.
    """
    if interface == "virtual":
        return channel_suggestions(interface)  # a known set, not whatever is in use
    found = detect_channels(interface, timeout)
    if found:
        return found
    return channel_suggestions(interface)
