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

#: Keys that identify *which device*, rather than which channel on it.  Only
#: used to build a readable label; every reported key is passed to the backend
#: whether it is listed here or not.
IDENTITY_KEYS = ("unique_hardware_id", "serial", "hw_type", "device", "vid", "pid")


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


def describe(config: dict) -> str:
    """A label for one detected channel, leading with what distinguishes it."""
    channel = config.get("channel", "")
    identity = [f"{config[key]}" for key in IDENTITY_KEYS if config.get(key) not in (None, "")]
    rest = [
        f"{key}={value!r}"
        for key, value in sorted(config.items())
        if key not in ("channel", "interface", *IDENTITY_KEYS)
    ]
    detail = ", ".join(identity + rest)
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
