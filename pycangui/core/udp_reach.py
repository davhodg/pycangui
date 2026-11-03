# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""How far UDP multicast goes: off, this computer only, or the local network.

One choice for everything pycangui does over UDP multicast -- udp_multicast
channels, and the signals sent to pyscopegui -- in both directions. A hop limit
only stops what is sent from leaving the computer: python-can listens on every
network adapter, so "this computer only" also needs what arrives from another
computer dropped, which :func:`keep_to_this_computer` does.
"""

from __future__ import annotations

import ipaddress
import threading
import time
from collections.abc import Callable

from PySide6.QtNetwork import QNetworkInterface

OFF, COMPUTER, NETWORK = "off", "computer", "network"
CHOICES = (OFF, COMPUTER, NETWORK)
#: Hop limits: 0 keeps a datagram on this computer, 1 lets it onto the local
#: network and no further, as no router passes it on.
LOCAL_ONLY, LOCAL_NETWORK = 0, 1

#: This computer only unless chosen otherwise: what a udp_multicast channel
#: did before there was a choice, so nothing that worked stops working.
_reach = COMPUTER


def set_reach(reach: str) -> None:
    global _reach
    _reach = reach if reach in CHOICES else COMPUTER


def reach() -> str:
    return _reach


def hop_limit() -> int:
    return LOCAL_NETWORK if _reach == NETWORK else LOCAL_ONLY


class _OwnAddresses:
    """This computer's addresses, looked up again when a stranger turns up.

    Again rather than once: an adapter that gets a new address from DHCP, or
    a VPN coming up, would otherwise make this computer's own frames look
    like another's. At most once a second, so a flood from another computer
    costs a set lookup each rather than a walk of the adapters.
    """

    REFRESH_S = 1.0

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._known: set = set()
        self._looked = 0.0
        self._refresh()

    def _refresh(self) -> None:
        found = set()
        for address in QNetworkInterface.allAddresses():
            parsed = _parse(address.toString())
            if parsed is not None:
                found.add(parsed)
        self._known, self._looked = found, time.monotonic()

    def __contains__(self, host: str) -> bool:
        parsed = _parse(host)
        if parsed is None:
            return False
        if parsed in self._known or parsed.is_loopback:
            return True
        with self._lock:
            if time.monotonic() - self._looked >= self.REFRESH_S:
                self._refresh()
        return parsed in self._known


def _parse(host: str):
    """An address without its IPv6 zone (fe80::1%eth0), or None."""
    try:
        return ipaddress.ip_address(str(host).split("%", 1)[0])
    except ValueError:
        return None


def keep_to_this_computer(bus, dropped: Callable[[str], None]) -> None:
    """Make a python-can udp_multicast bus drop what another computer sends.

    python-can reads each datagram's sender and then throws it away, so this
    reaches into its socket wrapper (``bus._multicast``, not public) and checks
    the sender before python-can does. ``dropped`` hears each new address once.

    Raises RuntimeError where python-can is no longer built that way: a
    channel that cannot keep to this computer must not open as if it did.
    """
    multicast = getattr(bus, "_multicast", None)
    recv = getattr(multicast, "recv", None)
    if not callable(recv):
        raise RuntimeError(
            "this version of python-can gives no way to tell where a frame came from"
        )
    own = _OwnAddresses()
    told: set = set()

    def from_here(timeout):
        result = recv(timeout)
        if not result:
            return result
        sender = result[1]
        host = sender[0] if isinstance(sender, tuple) else sender
        if host in own:
            return result
        if host not in told:
            told.add(host)
            dropped(str(host))
        # As if nothing had arrived: python-can's recv waits on for the rest
        # of its timeout.
        return None

    multicast.recv = from_here
