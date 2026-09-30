# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""UDS (ISO 14229) diagnostics on ISO-TP, built on udsoncan + can-isotp."""

from __future__ import annotations

from dataclasses import dataclass, field, fields

#: The frame lengths CAN FD has. There is nothing in between: a message
#: shorter than the next one up is padded to reach it.
CAN_DL = (8, 12, 16, 20, 24, 32, 48, 64)


#: An address box left empty. The standard addresses are a good default,
#: but a bus that uses those ids for something else should be able to say
#: so, and then nothing is named UDS on the strength of a number nobody
#: typed.
NO_ID = -1

#: The largest 11-bit identifier. Anything above it can only be a 29-bit
#: frame, which is how the pane knows: the identifier says so, and a box to
#: tick as well would be a second answer to the same question -- and a way
#: to get it wrong.
MAX_11_BIT = 0x7FF

#: ISO 15765-2 normal fixed addressing, which is UDS on a J1939 bus. The
#: identifier is worked out rather than typed: priority 6, then the page
#: and PDU format, then the two 8-bit addresses. Physical requests and
#: responses use PDU format 0xDA, functional ones 0xDB, both defined by
#: the standard -- so what somebody has to know is the ECU's address and
#: their own, which is what the boxes ask for.
FIXED_PRIORITY = 0x18
PHYSICAL_PF = 0xDA
FUNCTIONAL_PF = 0xDB
#: Who a functional request is addressed to. 0x33 is the OBD functional
#: address; a manufacturer's own diagnostics may use another, so it is a
#: box rather than a constant in the code.
OBD_FUNCTIONAL = 0x33


def fixed_id(pdu_format: int, target: int, source: int) -> int:
    """One normal fixed identifier, from the two addresses it carries."""
    return (FIXED_PRIORITY << 24) | (pdu_format << 16) | (target << 8) | source


def fixed_addressing(ecu: int, tester: int, functional_target: int = OBD_FUNCTIONAL) -> tuple:
    """(request, response, functional) for a pair of 8-bit addresses.

    The response swaps the two addresses over, because the ECU is then the
    one doing the sending: that is the whole of the scheme, and the reason
    it needs no identifiers typed at all.
    """
    return (
        fixed_id(PHYSICAL_PF, ecu, tester),
        fixed_id(PHYSICAL_PF, tester, ecu),
        fixed_id(FUNCTIONAL_PF, functional_target, tester),
    )


#: Whose P2 and P2* a request waits for. The ECU's by default: whether an ECU
#: keeps to the timing it gives is part of testing it. A bootloader finishing a
#: flash write is the usual one that does not -- 447 ms to answer a transfer
#: exit, with 50 ms promised -- and there the tester's own values are the ones
#: to use, as a floor or instead.
TIMING_ECU = "ecu"
TIMING_AT_LEAST = "at least"
TIMING_FORCED = "forced"

#: The services that can go to every ECU at once, as the pane names them, and
#: which go that way unless somebody says otherwise. A choice per service
#: rather than one for all: a flash usually sends tester present,
#: CommunicationControl and DTC setting to every ECU and everything else to
#: the one being flashed -- and a baud rate change that half the bus makes is
#: a broken bus.
FUNCTIONAL_SERVICES = {
    "session": "Session change",
    "reset": "ECU reset",
    "clear": "Clear DTCs",
    "comm": "Communication control",
    "tester": "Tester present",
    "dtc_setting": "DTC setting",
    "link": "Baud rate change",
    "raw": "Raw request (one frame)",
}
FUNCTIONAL_DEFAULT = ["tester", "comm", "dtc_setting", "link"]


@dataclass
class UdsConfig:
    """Addressing and timing for one ECU. Saved in settings "uds.config"."""

    tx_id: int = 0x7E0  # tester -> ECU
    rx_id: int = 0x7E8  # ECU -> tester
    functional_id: int = 0x7DF
    #: How the identifiers above were arrived at: typed, or worked out from
    #: two 8-bit addresses the J1939 way. Kept so the pane opens showing
    #: what somebody set rather than the identifiers it produced.
    fixed: bool = False
    ecu_address: int = 0x00
    tester_address: int = 0xF9
    functional_target: int = OBD_FUNCTIONAL
    #: The byte frames are padded out to 8 with, or None for no padding.
    padding: int | None = 0x00
    #: ISO 15765-2 over CAN FD. CAN_DL is how many bytes go in one frame:
    #: 8 as it always was, or one of the FD lengths up to 64, which is what
    #: makes a transfer over FD worth having.
    can_fd: bool = False
    tx_data_length: int = 8
    #: Whether the data phase of an FD frame switches to the faster rate.
    #: Without it an FD frame runs end to end at the arbitration bitrate, so
    #: the data rate chosen on the toolbar never gets used.
    bitrate_switch: bool = False
    #: The tester's own P2 and P2*: used until the ECU gives its own, and,
    #: as ``timing`` says, as a floor under the ECU's or in place of them.
    p2_timeout_s: float = 1.0
    p2_star_timeout_s: float = 5.0
    timing: str = TIMING_ECU
    tester_present_s: float = 2.0
    #: Which services go to every ECU, by FUNCTIONAL_SERVICES key.
    functional: list[str] = field(default_factory=lambda: list(FUNCTIONAL_DEFAULT))

    def goes_to_all(self, service: str) -> bool:
        """Whether this service is sent functionally, to every ECU."""
        return service in self.functional

    @property
    def extended_id(self) -> bool:
        """Whether these are 29-bit identifiers, which the identifier says.

        It used to be a tick box beside them, which is one place too many
        for the same fact: an id above 0x7FF is 29-bit and an id below it
        is not, and the two could disagree.
        """
        return self.fixed or max(self.tx_id, self.rx_id) > MAX_11_BIT

    def to_dict(self) -> dict:
        return dict(vars(self))

    @classmethod
    def from_dict(cls, d: dict) -> UdsConfig:
        # Fields only, not any attribute: extended_id is worked out from the
        # identifiers now, so a settings file that still carries one would
        # otherwise be assigning to a property and taking the window with it.
        wanted = {f.name for f in fields(cls)}
        cfg = cls()
        for key, value in d.items():
            if key in wanted:
                setattr(cfg, key, value)
        return cfg
